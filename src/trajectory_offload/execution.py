"""Profile-bound patch verification and failure diagnosis in temporary source copies.

This isolates source changes, not operating-system permissions. Check profiles are
trusted host configuration; repository code must be trusted to execute on this host.
"""

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from .contracts import DiagnosisTask, ExecutionPolicy, InvestigationTask, Settings, VerificationTask
from .patching import apply_patch_to_copy, patched_files
from .repository import Snapshot, safe_relative
from .runtime import BudgetExceeded, Run, _function, investigate
from .workspace import copy_sources, isolated_workspace


def load_policy(path: Path) -> ExecutionPolicy:
    policy = ExecutionPolicy.model_validate_json(path.read_text(encoding="utf-8-sig"))
    for prefix in policy.protected_paths:
        safe_relative(prefix)
    return policy


def _kill(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def _clip(value: str, limit: int = 6000) -> str:
    return value if len(value) <= limit else value[:limit // 2] + "\n[output omitted]\n" + value[-limit // 2:]


class ExecutionSession:
    def __init__(self, run: Run, workspace: Path, policy: ExecutionPolicy, check_ids: list[str],
                 failure_signature: str = ""):
        checks = {check.id: check for check in policy.checks}
        if not check_ids or any(name not in checks for name in check_ids):
            raise ValueError("every check ID must exist in the startup execution policy")
        self.allowed_check_ids = list(dict.fromkeys(check_ids))
        self.checks = {name: checks[name] for name in self.allowed_check_ids}
        self.run, self.workspace = run, workspace
        self.failure_signature = failure_signature
        self.records = []
        self.expected_snapshot = None

    def tools(self):
        return [_function("run_checks", "Run 1-4 predeclared check IDs in the isolated source copy. "
                          "Use only to distinguish an evidenced hypothesis. No command strings or edits are accepted.",
                          {"check_ids": {"type": "array", "items": {"type": "string", "enum": self.allowed_check_ids}}},
                          ["check_ids"])]

    def capture(self):
        if self.workspace.resolve() != self.workspace or not self.workspace.is_dir():
            raise ValueError("temporary source directory identity changed")
        return Snapshot.capture(self.workspace, self.run.settings)

    def evidence(self):
        return {"checks": self.records, "failure_signature": self.failure_signature,
                "check_profiles": [{"id": check.id, "argv": check.argv, "environment_keys": sorted(check.env)}
                                   for check in self.checks.values()],
                "isolation": "temporary supported-source copy; host Python dependencies; not an OS sandbox"}

    def run_checks(self, check_ids: list[str], *, stage: str = "followup", substitutions: dict[str, str] | None = None):
        if (not 1 <= len(check_ids) <= 4 or len(set(check_ids)) != len(check_ids)
                or any(name not in self.checks for name in check_ids)):
            raise ValueError("select 1-4 distinct allowed check IDs")
        results = []
        for name in check_ids:
            self.run.check_time()
            if self.run.metrics["process_calls"] >= self.run.settings.max_process_calls:
                raise BudgetExceeded("process-call budget exhausted")
            before = self.capture()
            if self.expected_snapshot and before.snapshot_id != self.expected_snapshot:
                raise ValueError("execution source changed before a check")
            self.run.check_time()
            check = self.checks[name]
            replacements = {"{python}": sys.executable, **(substitutions or {})}
            if any(value in {"{test_file}", "{junit_option}"} and value not in replacements for value in check.argv):
                raise ValueError("parameterized regression checks can only run through construct_regression_test")
            argv = [replacements.get(value, value) for value in check.argv]
            environment = {key: value for key, value in os.environ.items()
                           if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "LANG", "LC_ALL"}}
            home = self.workspace.parent / "home"
            home.mkdir(exist_ok=True)
            environment.update({"HOME": str(home), "USERPROFILE": str(home), "TEMP": str(home), "TMP": str(home),
                                "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                                "PYTHONPATH": os.pathsep.join([str(self.workspace / "src"), str(self.workspace)])})
            environment.update(check.env)
            timeout = min(check.timeout_seconds, self.run.settings.max_process_seconds,
                          self.run.settings.max_seconds - (time.monotonic() - self.run.started))
            self.run.metrics["process_calls"] += 1
            started = time.monotonic()
            status, exit_code, out, err, truncated = "error", None, b"", b"", False
            try:
                with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                    process = subprocess.Popen(argv, cwd=self.workspace, env=environment, stdin=subprocess.DEVNULL,
                                               stdout=stdout, stderr=stderr, start_new_session=os.name != "nt",
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    while process.poll() is None:
                        size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                        if time.monotonic() - started >= timeout or size > self.run.settings.max_process_output_bytes:
                            status = "timeout" if time.monotonic() - started >= timeout else "output_limit"
                            _kill(process)
                            break
                        time.sleep(0.02)
                    exit_code = process.returncode
                    size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                    truncated = size > self.run.settings.max_process_output_bytes
                    if truncated:
                        status = "output_limit"
                    elif status not in {"timeout", "output_limit"}:
                        status = "passed" if exit_code == 0 else "failed"
                    stdout.seek(0)
                    stderr.seek(0)
                    out = stdout.read(self.run.settings.max_process_output_bytes)
                    err = stderr.read(self.run.settings.max_process_output_bytes)
            except OSError as exc:
                err = f"{type(exc).__name__}: check executable could not run".encode()
            after = self.capture()
            unchanged = after.snapshot_id == before.snapshot_id
            if not unchanged:
                status = "source_mutated"
            elapsed = time.monotonic() - started
            self.run.metrics["process_seconds"] += elapsed
            record = {"id": f"check-{len(self.records) + 1}", "check_id": name, "stage": stage,
                      "argv": argv, "timeout_seconds": timeout,
                      "status": status, "exit_code": exit_code, "elapsed_seconds": round(elapsed, 4),
                      "stdout": _clip(out.decode("utf-8", "replace")), "stderr": _clip(err.decode("utf-8", "replace")),
                      "output_truncated": truncated, "source_unchanged": unchanged,
                      "snapshot_id": before.snapshot_id, "output_sha256": hashlib.sha256(out + b"\0" + err).hexdigest(),
                      "failure_signature_matched": bool(self.failure_signature and self.failure_signature.encode() in out + err)}
            self.records.append(record)
            self.run.event("check_run", **record)
            results.append(record)
            self.run.check_time()
        return results


def _analysis(run, workspace, task, session, mode):
    question = InvestigationTask(objective=task.objective, task_type="test_failure",
        known_evidence=getattr(task, "known_evidence", []), acceptance_criteria=[
            "Explain the observed check failure using exact source evidence; distinguish the observed behavior "
            "from untested causal hypotheses and identify any remaining diagnostic gap."])
    run.task = question
    return investigate(workspace, question, run.settings, mode, run=run, execution=session)


def execute_task(root: Path, task: VerificationTask | DiagnosisTask, settings: Settings,
                 policy: ExecutionPolicy, mode: str = "specialized", client=None) -> dict:
    """All required checks run independently of the model's choice of follow-up checks."""
    question = InvestigationTask(objective=task.objective, task_type="test_failure")
    run = Run(settings, root, mode, question, client)
    is_patch = isinstance(task, VerificationTask)
    result = {"status": "failed", "task_type": "patch_verification" if is_patch else "failure_diagnosis",
              "verdict": "inconclusive", "snapshot_id": None, "run_id": run.id, "mode": mode, "check_runs": [],
              "analysis": None, "diagnostics": [], "verification": {}, "metrics": run.metrics,
              "log_path": str(run.log_path)}
    result["execution_policy_id"] = hashlib.sha256(policy.model_dump_json().encode()).hexdigest()
    snapshot, session = None, None
    try:
        if mode not in {"generic", "specialized"}:
            raise ValueError("execution mode must be generic or specialized")
        if len(set(task.check_ids)) != len(task.check_ids):
            raise ValueError("required check IDs must be distinct")
        for prefix in policy.protected_paths:
            safe_relative(prefix)
        snapshot = Snapshot.capture(root, settings)
        result["snapshot_id"] = snapshot.snapshot_id
        if task.snapshot_id != snapshot.snapshot_id:
            raise ValueError("stale snapshot_id; investigate the current source before executing")
        if is_patch:
            # Validate every hunk and protected source before launching any process.
            patched_files(snapshot, task.patch, policy.protected_paths)
        run.event("execution_capsule", task=task.model_dump(), model=settings.model,
                  execution_policy_id=result["execution_policy_id"],
                  policy={"check_ids": [check.id for check in policy.checks], "protected_paths": policy.protected_paths})
        with isolated_workspace(run, "trajectory-execution-") as parent:
            workspace = parent / "source"
            copy_sources(snapshot, workspace)
            session = ExecutionSession(run, workspace, policy, [*task.check_ids, *task.followup_check_ids],
                                       "" if is_patch else task.failure_signature)
            session.expected_snapshot = snapshot.snapshot_id
            if is_patch:
                baseline = session.run_checks(task.check_ids, stage="baseline")
                changed = apply_patch_to_copy(snapshot, workspace, task.patch, policy.protected_paths)
                session.expected_snapshot = Snapshot.capture(workspace, settings).snapshot_id
                result["patched_snapshot_id"] = session.expected_snapshot
                result["changed_paths"] = changed
                patched = session.run_checks(task.check_ids, stage="patched")
                healthy = all(record["status"] in {"passed", "failed"} for record in [*baseline, *patched])
                result["verdict"] = ("passed" if all(record["status"] == "passed" for record in patched)
                                     else "failed") if healthy else "inconclusive"
                result["baseline_also_failed"] = [record["check_id"] for record in baseline if record["status"] == "failed"]
                result["status"] = "completed" if healthy else "partial"
                if healthy and result["verdict"] == "failed" and task.analyze_failures:
                    result["analysis"] = _analysis(run, workspace, task, session, mode)
            else:
                records = []
                for _ in range(task.repetitions):
                    records.extend(session.run_checks(task.check_ids, stage="reproduction"))
                healthy = all(record["status"] in {"passed", "failed"} for record in records)
                by_check = {}
                for name in task.check_ids:
                    group = [record for record in records if record["check_id"] == name]
                    matches = [record["status"] == "failed" and record["failure_signature_matched"] for record in group]
                    by_check[name] = ("reproduced" if all(matches) else "intermittent" if any(matches)
                                      else "different_failure" if any(record["status"] == "failed" for record in group)
                                      else "not_reproduced") if all(record["status"] in {"passed", "failed"} for record in group) else "inconclusive"
                reproduction = next((value for value in ("intermittent", "reproduced", "different_failure")
                                     if value in by_check.values()), "not_reproduced") if healthy else "inconclusive"
                result["reproduction_by_check"] = by_check
                result["reproduction"] = reproduction
                result["verdict"] = reproduction
                result["root_cause_confirmed"] = False
                result["status"] = "partial"
                if reproduction in {"reproduced", "intermittent"}:
                    result["analysis"] = _analysis(run, workspace, task, session, mode)
                    result["status"] = result["analysis"]["status"]
                else:
                    result["diagnostics"].append("expected failure was not stably reproduced; no root cause is asserted")
            if result["analysis"] is not None and result["analysis"]["status"] != "completed":
                result["status"] = "partial"
            # Keep intermediate transcript paths private just as on the investigation MCP surface.
            if result["analysis"] is not None:
                result["analysis"] = {key: value for key, value in result["analysis"].items()
                                      if key not in {"mode", "log_path", "metrics"}}
            result["verification"]["execution_source_unchanged"] = (
                session.capture().snapshot_id == session.expected_snapshot)
    except BudgetExceeded as exc:
        result["status"] = "partial"
        result["diagnostics"].append(str(exc))
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        result["diagnostics"].append(detail)
        run.event("execution_error", error_type=type(exc).__name__)
        result["status"] = "partial" if session and session.records else "failed"
    finally:
        if session:
            result["check_runs"] = session.records
        try:
            unchanged = snapshot is not None and Snapshot.capture(root, settings).snapshot_id == snapshot.snapshot_id
        except Exception:
            unchanged = False
        result["verification"]["original_snapshot_unchanged"] = unchanged
        errors = []
        if not unchanged:
            errors.append("original source changed or could not be verified")
        if any(not record["source_unchanged"] for record in result["check_runs"]):
            errors.append("a check mutated its source copy")
        if (result["verification"].get("execution_source_unchanged") is False
                or (result["verdict"] == "passed" and result["verification"].get("execution_source_unchanged") is not True)):
            errors.append("final execution source snapshot could not be verified")
        if time.monotonic() - run.started > settings.max_seconds:
            errors.append("wall-clock budget exhausted")
        if run.metrics["total_tokens"] > settings.max_total_tokens:
            errors.append("reported token usage exceeded the run budget")
        if errors:
            if result["status"] == "completed":
                result["status"] = "partial"
            result["verdict"] = "inconclusive"
            result["diagnostics"].extend(errors)
        run.metrics["elapsed_seconds"] = round(time.monotonic() - run.started, 4)
        result["metrics"] = dict(run.metrics)
        run.event("result", result=result)
    return result
