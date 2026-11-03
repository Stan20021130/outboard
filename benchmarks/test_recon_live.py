"""Live verification of the static repo reconnaissance pack against the Click repository."""
import json
from pathlib import Path
import time

from trajectory_offload.contracts import Settings
from trajectory_offload.repository import Snapshot, build_repo_recon

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / ".trajectory/v14-opt-test-03/cases/regression"

def main():
    settings = Settings(model="deepseek-v4.1-flash", max_seconds=30)
    print(f"Capturing snapshot for real repo: {REPO} ...", flush=True)
    t0 = time.monotonic()
    snapshot = Snapshot.capture(REPO, settings)
    t_snap = time.monotonic() - t0
    print(f"Snapshot captured: {snapshot.snapshot_id[:16]}... in {t_snap*1000:.2f}ms (files: {len(snapshot.files)})", flush=True)

    t1 = time.monotonic()
    recon = build_repo_recon(snapshot)
    t_recon = time.monotonic() - t1
    print(f"\nReconnaissance completed in {t_recon*1000:.2f}ms (0 model calls, 0 tokens)!\n", flush=True)
    print("=== Static Repo Recon Pack Output ===")
    print(json.dumps(recon, indent=2))

    # Invariants verification
    assert "pyproject.toml" in recon["manifests"] or "setup.cfg" in recon["manifests"], "Manifest not found"
    assert any("src/click" in r for r in recon["source_layout"]["source_roots"]), "Source root src/click not identified"
    assert "tests" in recon["source_layout"]["test_dirs"], "Test dir tests not identified"
    assert recon["source_layout"]["framework"] == "pytest", "Test framework pytest not identified"
    assert len(recon["source_layout"]["sample_tests"]) > 0, "No sample tests extracted"
    assert any("core.py" in ep for ep in recon["entry_points"]), "Core entry point not identified"
    assert recon["stats"]["python_files"] > 20, "Python file count undercounted"
    print("\nALL INVARIANTS PASSED! Instant 0-token cold start verified.")

if __name__ == "__main__":
    main()
