"""Byte-exact source copies shared by evaluation and execution tasks."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import time


@contextmanager
def isolated_workspace(run, prefix: str):
    base = Path(tempfile.gettempdir()).resolve()
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    try:
        yield directory
    finally:
        try:
            if directory.resolve() != directory or directory.parent != base or not directory.name.startswith(prefix):
                raise OSError("temporary directory identity changed; refusing recursive cleanup")
            shutil.rmtree(directory)
        except OSError as exc:
            # ponytail: sandbox cache ACLs can retain temp files; report for host cleanup.
            run.metrics["cleanup_retained_path"] = str(directory)
            run.event("cleanup_warning", path=str(directory), error_type=type(exc).__name__)
        run.metrics["elapsed_seconds"] = round(time.monotonic() - run.started, 4)


def copy_sources(snapshot, target: Path, scope: list[str] | None = None):
    target.mkdir(parents=True, exist_ok=True)
    for relative in snapshot.scoped(scope or []):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(snapshot.raw_files[relative])
