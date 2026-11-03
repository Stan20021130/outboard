"""Task-level MCP surface; repository and execution profiles are fixed at startup."""

from pathlib import Path
from dataclasses import replace
from typing import Any, Literal
import asyncio

from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

from .contracts import (CoverageAugmentTask, DiagnosisTask, ExecutionPolicy, FailureGroupingTask, InvestigationTask,
                        RegressionTask, Settings, VerificationTask)
from .repository import Snapshot, build_repo_recon
from .runtime import investigate
from .evidence import compact_evidence
from .sampling import MCPSamplingClient, has_sampling_capability


TOOL_DESCRIPTION = (
    "Investigate a bounded cross-file repository question: locate implementations, trace configuration, "
    "map a failing test to relevant code, or analyze a proposed change's static impact. "
    "Returns exact code evidence, related tests, snapshot_id and unresolved gaps. "
    "Use when multiple searches/reads are needed and the current context lacks the answer. "
    "A known-file single read is usually cheaper to do directly. Omit scope unless the paths are known; do not guess a src directory. "
    "Symbols accept qualified Python names, for example Context.__init__. "
    "Acceptance criteria must ask for static source evidence; use verify_patch for executed tests. "
    "Result format_version=3 stores exact citations once in an evidence table; report entries reference its IDs. "
    "completed means the investigator reports the static questions answered and programmatic source checks passed. "
    "Check verification.semantic_review_status separately: independent model review runs only when requested "
    "by semantic_review=true or required by the host. Unreviewed findings still need normal task validation. "
    "This tool never edits source or runs tests."
)


def create_server(root: Path, settings: Settings, mode: str = "specialized", client=None,
                  policy: ExecutionPolicy | None = None) -> FastMCP:
    root = root.resolve(strict=True)
    if settings.log_dir.resolve().is_relative_to(root):
        raise ValueError("log_dir must be outside the investigated repository")
    server = FastMCP("outboard")

    @server.tool(description="Get current supported-source snapshot ID, static repo reconnaissance, "
                 "and available check profiles without a model call. Use for instant zero-token cold start.",
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def get_repository_state() -> dict[str, Any]:
        snapshot = await asyncio.to_thread(Snapshot.capture, root, settings)
        recon = build_repo_recon(snapshot)
        return {"snapshot_id": snapshot.snapshot_id, "source_file_count": len(snapshot.files),
                "recon": recon,
                "execution_enabled": policy is not None,
                "checks": [{"id": check.id, "argv": check.argv, "timeout_seconds": check.timeout_seconds,
                            "environment_keys": sorted(check.env)} for check in policy.checks] if policy else [],
                "execution_isolation": "temporary supported-source copy; not an OS sandbox"}

    @server.tool(name="investigate_repository", description=TOOL_DESCRIPTION, structured_output=True,
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=False, openWorldHint=False))
    async def investigate_repository(
        objective: str,
        task_type: Literal["localize", "trace_config", "test_failure", "change_impact"] = "localize",
        scope: list[str] | None = None,
        known_evidence: list[str] | None = None,
        known_sources: list[dict[str, Any]] | None = None,
        remaining_criteria: list[int] | None = None,
        expected_snapshot_id: str | None = None,
        acceptance_criteria: list[str] | None = None,
        changed_paths: list[str] | None = None,
        symbols: list[str] | None = None,
        patch: str = "",
        semantic_review: bool = False,
        ctx: Context = None,
    ) -> dict[str, Any]:
        effective_client = client
        if ctx is not None and ctx.session is not None:
            try:
                if has_sampling_capability(ctx.session):
                    effective_client = MCPSamplingClient(ctx.session, loop=asyncio.get_running_loop())
            except Exception:
                pass
        task = InvestigationTask(objective=objective, task_type=task_type, scope=scope or [],
                                 known_evidence=known_evidence or [],
                                 known_sources=known_sources or [],
                                 remaining_criteria=remaining_criteria,
                                 expected_snapshot_id=expected_snapshot_id,
                                 acceptance_criteria=acceptance_criteria or [],
                                 changed_paths=changed_paths or [], symbols=symbols or [], patch=patch)
        runtime_settings = replace(settings, semantic_verification=True) if semantic_review else settings
        result = await asyncio.to_thread(investigate, root, task, runtime_settings, mode, effective_client)
        # The caller sees the same contract in both experimental arms, without a variant label
        # or a path inviting it to reload the investigator's intermediate transcript.
        return {"format_version": 3, **compact_evidence({key: value for key, value in result.items()
                                                      if key not in {"mode", "log_path"}})}

    @server.tool(description="Group at least two caller-supplied test failures or JUnit failures by exact symptom. "
                 "Optionally analyze cross-group relationships as unconfirmed hypotheses; no tests are run and "
                 "the supplied observations are not independently verified. For source-backed analysis set "
                 "include_source_evidence=true; default analyzes logs only. Requires current snapshot_id.",
                 structured_output=True, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def cluster_test_failures(snapshot_id: str, failures: list[dict[str, Any]] | None = None,
                                    junit_xml: str = "", analyze_relationships: bool = True,
                                    include_source_evidence: bool = False,
                                    ctx: Context = None) -> dict[str, Any]:
        effective_client = client
        if ctx is not None and ctx.session is not None:
            try:
                if has_sampling_capability(ctx.session):
                    effective_client = MCPSamplingClient(ctx.session, loop=asyncio.get_running_loop())
            except Exception:
                pass
        from .specialized import cluster_test_failures as cluster
        task = FailureGroupingTask(snapshot_id=snapshot_id, failures=failures or [], junit_xml=junit_xml,
                                   analyze_relationships=analyze_relationships,
                                   include_source_evidence=include_source_evidence)
        return await asyncio.to_thread(cluster, root, task, settings, effective_client)

    if policy is not None:
        from .execution import execute_task

        @server.tool(description="Construct a new pytest regression test from explicit expected behavior, "
                     "then check marked assertion failure before the source patch and success after it. "
                     "Requires a predeclared fixed pytest regression profile and a current snapshot_id. "
                     "Supply exactly one of source_patch (exact unified diff) or source_edits (list of {path,old,new}; "
                     "each old string must occur exactly once). Prefer source_edits for simple changes. "
                     "Omit scope unless necessary; if supplied, include relevant tests as well as source files. "
                     "Returns a tests-only patch; differential success does not certify semantic correctness. "
                     "Executes generated tests and trusted repository code in temporary source copies, not an OS sandbox.",
                     structured_output=True, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
        async def construct_regression_test(objective: str, expected_behavior: str, snapshot_id: str,
                                            test_file: str, check_id: str, source_patch: str = "",
                                            source_edits: list[dict[str, str]] | None = None,
                                            scope: list[str] | None = None,
                                            ctx: Context = None) -> dict[str, Any]:
            effective_client = client
            if ctx is not None and ctx.session is not None:
                try:
                    if has_sampling_capability(ctx.session):
                        effective_client = MCPSamplingClient(ctx.session, loop=asyncio.get_running_loop())
                except Exception:
                    pass
            from .specialized import construct_regression_test as construct
            task = RegressionTask(objective=objective, expected_behavior=expected_behavior, snapshot_id=snapshot_id,
                                  source_patch=source_patch, source_edits=source_edits or [], test_file=test_file,
                                  check_id=check_id, scope=scope or [])
            return await asyncio.to_thread(construct, root, task, settings, policy, effective_client)

        @server.tool(description="Augment test coverage for uncovered conditional branches in a target source file. "
                     "Uses deterministic AST branch analysis or supplied branches, generates edge-case tests, "
                     "and verifies single-test execution in a sandbox. Requires current snapshot_id.",
                     structured_output=True, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
        async def augment_test_coverage(objective: str, snapshot_id: str, target_path: str,
                                        test_file: str, check_id: str,
                                        branches: list[dict[str, Any]] | None = None,
                                        scope: list[str] | None = None,
                                        ctx: Context = None) -> dict[str, Any]:
            effective_client = client
            if ctx is not None and ctx.session is not None:
                try:
                    if has_sampling_capability(ctx.session):
                        effective_client = MCPSamplingClient(ctx.session, loop=asyncio.get_running_loop())
                except Exception:
                    pass
            from .specialized import augment_coverage_edge_cases as augment
            task = CoverageAugmentTask(objective=objective, snapshot_id=snapshot_id, target_path=target_path,
                                       test_file=test_file, check_id=check_id, branches=branches or [],
                                       scope=scope or [])
            return await asyncio.to_thread(augment, root, task, settings, policy, effective_client)

        @server.tool(description="Deterministic sandbox gate to verify a patch against check IDs from get_repository_state "
                     "without LLM overhead. Runs baseline and patched checks in a temporary source copy and reports verdict. "
                     "Requires current snapshot_id. completed describes execution, verdict describes the patch.",
                     structured_output=True, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
        async def verify_patch(objective: str, snapshot_id: str, patch: str, check_ids: list[str],
                               followup_check_ids: list[str] | None = None, analyze_failures: bool = False) -> dict[str, Any]:
            task = VerificationTask(objective=objective, snapshot_id=snapshot_id, patch=patch, check_ids=check_ids,
                                    followup_check_ids=followup_check_ids or [], analyze_failures=analyze_failures)
            result = await asyncio.to_thread(execute_task, root, task, settings, policy, mode, client)
            return {key: value for key, value in result.items() if key not in {"log_path", "mode"}}

        @server.tool(description="Reproduce a literal failure signature with predeclared check IDs, then investigate "
                     "source and choose allowed follow-up checks. Requires current snapshot_id; returns reproduction "
                     "status, evidence and diagnostic hypotheses. Runs trusted code in a temporary source copy, not an OS sandbox.",
                     structured_output=True, annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
        async def diagnose_failure(objective: str, snapshot_id: str, check_ids: list[str], failure_signature: str,
                                   followup_check_ids: list[str] | None = None, repetitions: int = 2,
                                   known_evidence: list[str] | None = None) -> dict[str, Any]:
            task = DiagnosisTask(objective=objective, snapshot_id=snapshot_id, check_ids=check_ids,
                                 failure_signature=failure_signature, followup_check_ids=followup_check_ids or [],
                                 repetitions=repetitions, known_evidence=known_evidence or [])
            result = await asyncio.to_thread(execute_task, root, task, settings, policy, mode, client)
            return {key: value for key, value in result.items() if key not in {"log_path", "mode"}}

    return server

