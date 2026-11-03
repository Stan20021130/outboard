---
name: repository-investigator
description: Offload bounded repository investigation, patch verification, failure diagnosis, regression-test construction, or multi-failure grouping to the trajectory tools when their task boundaries fit.
---

# Trajectory Coding Subagents (Streamlined Architecture)

## 1. Core Tooling Topology

The offload architecture strictly aligns agent freedom with task entropy across **Three Golden Scenarios** and **One Deterministic Gate**:

### The Core Specialized Scenarios (Orchestrated Subagents)
1. **Multi-Failure Triage (`cluster_test_failures`)**:
   - **When to Use**: When facing multiple test failures, tracebacks, or a JUnit XML report.
   - **What it Does**: Parses structured observations, clusters identical symptoms into groups, and synthesizes cross-group common root-cause hypotheses with 0 test re-runs. Reduces token context by >80%.
2. **Regression Sandbox Differential Construction (`construct_regression_test`)**:
   - **When to Use**: When a bug fix is ready and a new regression test file is required to prevent reintroduction.
   - **What it Does**: Generates minimal test assertions in an isolated temporary sandbox, verifying that the new test fails with `TRAJECTORY_REGRESSION` on buggy source and passes cleanly on fixed source.
3. **Cross-File Dependency & Config Tracing (`investigate_repository`)**:
   - **When to Use**: For tracing configuration chains across files (`task_type="trace_config"`) or analyzing change impact on public symbols and callers (`task_type="change_impact"`).
   - **What it Does**: Batches static AST and reference lookups across boundaries, returning a compact MCP Format 3 evidence table.
4. **Coverage-Gap Edge-Case Augmentation (`augment_test_coverage`)**:
   - **When to Use**: When critical conditional branches (e.g. `if/else`, edge boundaries) lack test coverage.
   - **What it Does**: Uses deterministic AST branch identification or caller-provided branches, synthesizes targeted boundary assertions, and verifies single-test execution in a sandbox.

### Deterministic Capabilities (Zero LLM Overhead)
- **Instant Repo Reconnaissance (`get_repository_state` -> `recon`)**:
  - **What it Does**: Pure in-memory static AST and manifest parser (0 LLM, 0 Token, <1ms). Instantly extracts `manifests`, `source_roots`, `test_dirs`, `framework`, and `entry_points` to eliminate cold-start exploration.
- **Patch Verification Gate (`verify_patch`)**:
  - **What it Does**: A pure deterministic sandbox runner (`analyze_failures=False` by default). Applies a unified patch to a clean checkout copy and executes baseline/patched check IDs. Incurs 0 model calls and 0 prompt tokens.

### Strict Anti-Patterns (Prohibited Delegations)
- **NO Low-Entropy Offloads**: NEVER delegate simple, single-file searches or obvious code lookups (`localize`, `test_failure`). Use native, immediate workspace tools (`read_file`, `search_code`). Coordinating a subagent for low-entropy lookups produces pure latency overhead.
- **NO High-Entropy Exploration Offloads**: NEVER offload open-ended, non-reproducible dynamic bug chasing to subagents. High-entropy exploration requires main agent autonomy and direct inspection.

## 2. Interaction Workflow Protocol (v0.5-lite)

To maintain high repair quality while preventing token compounding and latency spikes, follow this three-phase interaction lifecycle:

### Phase 1: Precision Offload & Lightweight Delegation
- **When to Delegate**: Delegate bounded multi-step questions (tracing configuration chains, discovering affected callers/callees across files, mapping test assertions). A single known file read or facts already present in context must be handled directly by the main agent without offloading.
- **Avoid Unfocused Local Roaming**: Do not execute wide-ranging glob/read loops before delegating. Once anchors or relevant paths are identified, delegate immediately.
- **Lightweight Handoff Contract**:
  - `known_sources`: Pass already-read excerpts (`path`, `start_line`, `end_line`, optional `expected_quote`) to prevent redundant re-fetching (limit: at most 12 ranges, ≤ 100 lines per range, total ≤ 260 lines).
  - `remaining_criteria`: Pass zero-based indices of criteria that strictly require new investigation.
  - Keep `acceptance_criteria` purely static and factual. Never include criteria demanding runtime test execution from the read-only investigator.
  - `expected_snapshot_id`: Use to reject stale context when working across multiple state changes.

### Phase 2: Zero-Redundancy Handoff Consumption (Anti-Latency Protocol)
- **MCP Format 3 Evidence Consumption**:
  - Evidence references point to the top-level `evidence` table containing exact paths, lines and verbatim quotes.
  - **STRICT PROHIBITION: Zero Redundant Reads**: Do NOT reissue local `grep`, `search`, or `read_file` calls on files and symbols already established in the evidence table. Subagent evidence is fully validated; redundant reads incur a severe "double-inference penalty" that completely erodes offload latency savings.
  - **Direct Atomic Repair**: Proceed immediately to formulating and applying the fix using the exact line numbers and quotes provided.
  - If core static code criteria are supported, proceed to repair. Only issue a targeted follow-up if a genuine source gap was reported in `unresolved_questions`.

### Phase 3: Tiered Verification Protocol (Differential Sandbox vs. Final Delivery Gate)
To achieve both sub-second feedback during development and zero-defect assurance at delivery, follow the strict two-tier verification boundary:

1. **Layer 1: Sandbox Differential Verification (Single Target Test Only)**
   - **Scope**: Executed within isolated temporary workspaces (e.g. `construct_regression_test`).
   - **STRICT PROHIBITION: No Full-Suite Execution in Sandboxes**: Sandboxes must ONLY run the newly generated target test file (`pytest -q {test_file}`). Running the entire repository test suite in sandboxes wastes 30-60 seconds running thousands of unrelated tests.
   - **Differential Goal**: Verify that the new test fails with `TRAJECTORY_REGRESSION` on buggy source and cleanly passes on fixed source.

2. **Layer 2: Main Workspace Final Delivery Gate (Single Full Regression)**
   - **Scope**: Executed in the active main workspace by the main agent.
   - **Focused Iterative Checks**: During iterative adjustments in the main workspace, run ONLY the target test case (e.g. `pytest tests/test_specific.py`).
   - **Single Final Full Regression**:
     1. Complete all source and regression test edits.
     2. Confirm target focused test passes in milliseconds.
     3. Execute the full test suite (`pytest -q --color=no tests`) EXACTLY ONCE as the final gate check before delivery.
     4. If full regression fails, inspect only the failing signatures, patch locally, verify with the focused test, and then rerun the final full gate once.

## 3. Execution Tools and Sandbox Constraints

- Execution tools (`verify_patch`, `diagnose_failure`, `construct_regression_test`) execute trusted code in a temporary source copy using host dependencies. They do not provide OS-level sandboxing or network isolation.
- For `verify_patch`, `status=completed` indicates execution finished; require `verdict=passed` across required check IDs before accepting a patch. The original checkout remains the main agent's responsibility; integrate accepted patches separately.
- Independent review (`semantic_review=true`) is optional and defaults to `false`. An unrequested review status (`not_requested`) is normal and must not trigger repeated calls.
- Final repair decisions, architectural alignment, and final validation remain the sole responsibility of the main agent.

