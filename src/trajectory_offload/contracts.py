"""Public task/result contracts and runtime-owned settings."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import os
import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnownSource(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    expected_quote: str = Field(default="", max_length=1800)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.end_line - self.start_line + 1 > 100:
            raise ValueError("known source range must not exceed 100 lines")
        return self


class InvestigationTask(Contract):
    objective: str = Field(min_length=1, max_length=6000)
    task_type: Literal["localize", "trace_config", "test_failure", "change_impact"] = "localize"
    scope: list[str] = Field(default_factory=list, max_length=20)
    known_evidence: list[str] = Field(default_factory=list, max_length=20)
    known_sources: list[KnownSource] = Field(default_factory=list, max_length=12)
    remaining_criteria: list[int] | None = None
    expected_snapshot_id: str | None = Field(default=None, max_length=64)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=12)
    changed_paths: list[str] = Field(default_factory=list, max_length=20)
    symbols: list[str] = Field(default_factory=list, max_length=12)
    patch: str = Field(default="", max_length=200000)

    @model_validator(mode="after")
    def nonempty_inputs(self):
        self.objective = self.objective.strip()
        if not self.objective:
            raise ValueError("objective must describe an investigation")
        for values in (self.scope, self.known_evidence, self.acceptance_criteria):
            if any(not value.strip() or len(value) > 6000 for value in values):
                raise ValueError("task entries must be nonempty and at most 6000 characters")
        if not self.acceptance_criteria:
            self.acceptance_criteria = [self.objective]
        if sum(ks.end_line - ks.start_line + 1 for ks in self.known_sources) > 260:
            raise ValueError("known_sources total lines must not exceed 260 lines")
        if self.remaining_criteria is not None:
            if any(idx < 0 or idx >= len(self.acceptance_criteria) for idx in self.remaining_criteria):
                raise ValueError("remaining_criteria indices must be valid zero-based indices of acceptance_criteria")
            if len(set(self.remaining_criteria)) != len(self.remaining_criteria):
                raise ValueError("remaining_criteria must not contain duplicate indices")
        if any(not symbol or not all(part.isidentifier() for part in symbol.split(".")) for symbol in self.symbols):
            raise ValueError("symbols must be Python names, optionally qualified (for example Context.__init__)")
        if self.task_type == "change_impact" and not (self.changed_paths or self.symbols or self.patch):
            raise ValueError("change_impact requires changed_paths, symbols or a unified patch")
        if self.task_type != "change_impact" and (self.changed_paths or self.symbols or self.patch):
            raise ValueError("change inputs require task_type=change_impact")
        return self


class Evidence(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=1800)


class Finding(Contract):
    claim: str = Field(min_length=1, max_length=800)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class Relation(Contract):
    source: str
    target: str
    kind: str
    status: Literal["confirmed", "hypothesis"]
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class RelatedTest(Contract):
    path: str
    behavior: str
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class CriterionCoverage(Contract):
    criterion_index: int = Field(ge=0)
    status: Literal["supported", "unresolved"]
    finding_indices: list[int]
    relation_indices: list[int]
    test_indices: list[int]
    explanation: str = Field(default="", max_length=300)


class InvestigationReport(Contract):
    status: Literal["completed", "partial", "failed"]
    summary: str = Field(default="", max_length=1000)
    findings: list[Finding] = Field(max_length=16)
    relations: list[Relation] = Field(max_length=16)
    related_tests: list[RelatedTest] = Field(max_length=10)
    criteria_coverage: list[CriterionCoverage] = Field(max_length=12)
    unresolved_questions: list[str] = Field(max_length=20)


class SupportReview(Contract):
    supported: bool
    unsupported_claims: list[str]
    criteria_supported: list[int]
    notes: list[str]


class BenchmarkCase(Contract):
    id: str = Field(min_length=1, max_length=120)
    repo: str = Field(min_length=1)
    task: InvestigationTask
    expected_files: list[str] = Field(default_factory=list)
    repair_objective: str | None = None
    test_command: list[str] = Field(default_factory=list)
    require_offload: bool = False


class CheckSpec(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    argv: list[str] = Field(min_length=1, max_length=40)
    timeout_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)
    env: dict[str, str] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def bounded_command(self):
        if any(not arg or "\x00" in arg or len(arg) > 6000 for arg in self.argv):
            raise ValueError("check argv entries must be nonempty, bounded strings")
        if any(not key.isidentifier() or "\x00" in value or len(value) > 6000 for key, value in self.env.items()):
            raise ValueError("invalid check environment")
        if any(key.upper() in {"PYTHONPATH", "PYTHONHOME", "OPENAI_API_KEY"} for key in self.env):
            raise ValueError("check environment cannot override runtime paths or contain API keys")
        return self


class ExecutionPolicy(Contract):
    checks: list[CheckSpec] = Field(min_length=1, max_length=30)
    protected_paths: list[str] = Field(default_factory=lambda: ["tests"])

    @model_validator(mode="after")
    def unique_checks(self):
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("check IDs must be unique")
        return self


class VerificationTask(Contract):
    objective: str = Field(min_length=1, max_length=6000)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch: str = Field(min_length=1, max_length=200000)
    check_ids: list[str] = Field(min_length=1, max_length=4)
    followup_check_ids: list[str] = Field(default_factory=list, max_length=8)
    analyze_failures: bool = True


class DiagnosisTask(Contract):
    objective: str = Field(min_length=1, max_length=6000)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    check_ids: list[str] = Field(min_length=1, max_length=4)
    followup_check_ids: list[str] = Field(default_factory=list, max_length=8)
    failure_signature: str = Field(min_length=1, max_length=1000)
    repetitions: int = Field(default=2, ge=2, le=3)
    known_evidence: list[str] = Field(default_factory=list, max_length=20)


class SourceEdit(Contract):
    path: str = Field(min_length=1, max_length=500)
    old: str = Field(min_length=1, max_length=200000)
    new: str = Field(max_length=200000)


class RegressionTask(Contract):
    objective: str = Field(min_length=1, max_length=6000)
    expected_behavior: str = Field(min_length=1, max_length=6000)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_file: str = Field(min_length=1, max_length=500)
    check_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    source_patch: str = Field(default="", max_length=200000)
    source_edits: list[SourceEdit] = Field(default_factory=list, max_length=8)
    scope: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def meaningful_description(self):
        self.objective = self.objective.strip()
        self.expected_behavior = self.expected_behavior.strip()
        if not self.objective or not self.expected_behavior:
            raise ValueError("objective and expected_behavior must not be blank")
        if bool(self.source_patch) == bool(self.source_edits):
            raise ValueError("provide exactly one of source_patch or source_edits")
        return self


class UncoveredBranch(Contract):
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1)
    condition: str = Field(min_length=1, max_length=500)
    target_branch: Literal["true", "false", "exception"] = "true"


class CoverageAugmentTask(Contract):
    objective: str = Field(min_length=1, max_length=6000)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_path: str = Field(min_length=1, max_length=500)
    test_file: str = Field(min_length=1, max_length=500)
    branches: list[UncoveredBranch] = Field(default_factory=list, max_length=8)
    check_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    scope: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def meaningful_task(self):
        self.objective = self.objective.strip()
        if not self.objective:
            raise ValueError("objective must not be blank")
        return self


class FailureObservation(Contract):
    test_id: str = Field(min_length=1, max_length=1000)
    kind: Literal["failure", "error"]
    message: str = Field(default="", max_length=4000)
    traceback: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def meaningful_id(self):
        self.test_id = self.test_id.strip()
        if not self.test_id:
            raise ValueError("test_id must not be blank")
        return self


class FailureGroupingTask(Contract):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    failures: list[FailureObservation] = Field(default_factory=list, max_length=60)
    junit_xml: str = Field(default="", max_length=250000)
    analyze_relationships: bool = True
    include_source_evidence: bool = False

    @model_validator(mode="after")
    def one_input(self):
        if bool(self.failures) == bool(self.junit_xml):
            raise ValueError("provide exactly one of failures or junit_xml")
        return self


@dataclass(frozen=True)
class Settings:
    model: str
    reasoning_effort: str = "medium"
    max_model_calls: int = 8
    max_tool_calls: int = 24
    max_total_tokens: int = 80000
    max_output_tokens: int = 5000
    max_seconds: float = 240
    max_files: int = 20000
    max_snapshot_bytes: int = 64 * 1024 * 1024
    max_file_bytes: int = 2 * 1024 * 1024
    max_process_calls: int = 12
    max_process_seconds: float = 60
    max_process_output_bytes: int = 128 * 1024
    semantic_verification: bool = False
    prefetch_context: bool = True
    log_dir: Path = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "TrajectoryOffload" / "runs"

    def __post_init__(self):
        if not self.model.strip():
            raise ValueError("set --model or TRAJECTORY_MODEL to match the main agent")
        if self.reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ValueError("unsupported reasoning effort")
        if min(self.max_model_calls, self.max_tool_calls, self.max_total_tokens,
               self.max_output_tokens, self.max_seconds, self.max_files,
               self.max_snapshot_bytes, self.max_file_bytes, self.max_process_calls,
               self.max_process_seconds, self.max_process_output_bytes) <= 0:
            raise ValueError("all runtime limits must be positive")
        if not math.isfinite(self.max_seconds) or not math.isfinite(self.max_process_seconds):
            raise ValueError("time limits must be finite")
        if self.semantic_verification and self.max_model_calls < 2:
            raise ValueError("semantic verification requires at least two model calls")

