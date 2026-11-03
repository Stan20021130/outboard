"""Strict UTF-8 unified patches: exact context, bounded paths, no fuzzy application."""

from dataclasses import dataclass
import re

from .repository import EXCLUDED_DIRS, SECRET_NAMES, TEXT_NAMES, TEXT_SUFFIXES, Snapshot, safe_relative
from pathlib import Path


@dataclass
class PatchFile:
    old: str | None
    new: str | None
    hunks: list


def _path(header: str, prefix: str) -> str | None:
    value = header.split("\t", 1)[0]
    if value == "/dev/null":
        return None
    # ponytail: quoted Git paths and renames need a fuller diff parser; reject explicitly.
    if not value.startswith(prefix) or value.startswith('"'):
        raise ValueError("use an unquoted unified diff with a/ and b/ paths")
    value = safe_relative(value[len(prefix):])
    path = Path(value)
    if (not value or any(part in EXCLUDED_DIRS for part in path.parts) or path.name in SECRET_NAMES
            or (path.name.startswith(".env") and path.name != ".env.example")
            or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES)):
        raise ValueError("patch path is outside the supported source surface")
    return value


def parse_patch(patch: str) -> list[PatchFile]:
    if len(patch.encode("utf-8")) > 200000:
        raise ValueError("patch exceeds 200000 bytes")
    lines = patch.replace("\r\n", "\n").splitlines(keepends=True)
    files, index = [], 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("--- "):
            if (line.startswith(("diff --git ", "index ", "new file mode 100644", "deleted file mode 100644"))
                    or not line.strip()):
                index += 1
                continue
            raise ValueError("unsupported patch metadata or content")
        old = _path(line[4:].rstrip("\n"), "a/")
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("patch file lacks a +++ header")
        new = _path(lines[index][4:].rstrip("\n"), "b/")
        if (old is None and new is None) or (old and new and old != new):
            raise ValueError("patch renames are not supported")
        entry = PatchFile(old, new, [])
        index += 1
        while index < len(lines) and lines[index].startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[index])
            if not match:
                raise ValueError("invalid unified hunk header")
            start, count, target, target_count = (int(value) if value is not None else 1 for value in match.groups())
            index += 1
            body, old_count, new_count = [], 0, 0
            while old_count < count or new_count < target_count:
                if index >= len(lines) or not lines[index] or lines[index][0] not in " +-":
                    raise ValueError("truncated or invalid patch hunk")
                item = lines[index]
                old_count += item[0] in " -"
                new_count += item[0] in " +"
                body.append(item)
                index += 1
                if index < len(lines) and lines[index].startswith("\\ No newline at end of file"):
                    body[-1] = body[-1].removesuffix("\n")
                    index += 1
            if old_count != count or new_count != target_count:
                raise ValueError("patch hunk counts do not match")
            entry.hunks.append((start, count, target, target_count, body))
        if not entry.hunks:
            raise ValueError("each patched file must contain text hunks")
        files.append(entry)
    paths = [entry.new or entry.old for entry in files]
    if not files or len(set(paths)) != len(paths) or len(files) > 40:
        raise ValueError("patch must contain 1-40 distinct source files")
    return files


def patched_files(snapshot: Snapshot, patch: str, protected_paths: list[str]) -> dict[str, bytes | None]:
    """Return changed bytes; None marks a file deletion."""
    changes = {}
    prefixes = [safe_relative(path) for path in protected_paths]
    for entry in parse_patch(patch):
        path = entry.new or entry.old
        if entry.old and path not in snapshot.raw_files:
            raise ValueError(f"patch source is not in the snapshot: {path}")
        if entry.old is None and path in snapshot.raw_files:
            raise ValueError(f"patch addition already exists: {path}")
        is_test = Path(path).name.startswith("test_") or Path(path).name.endswith("_test.py")
        if entry.old and (is_test or any(not prefix or path == prefix or path.startswith(prefix + "/") for prefix in prefixes)):
            raise ValueError(f"patch changes an existing protected test/source: {path}")
        raw = snapshot.raw_files.get(path, b"")
        source = raw.decode("utf-8-sig").replace("\r\n", "\n").splitlines(keepends=True)
        output, position = [], 0
        for start, count, target, target_count, body in entry.hunks:
            offset = start - 1 if count else start
            if offset < position or offset > len(source):
                raise ValueError("patch hunks overlap or start outside the source")
            output.extend(source[position:offset])
            position = offset
            expected_target = target - 1 if target_count else target
            if len(output) != expected_target:
                raise ValueError("patch new-line coordinates are inconsistent")
            for item in body:
                if item[0] in " -":
                    if position >= len(source) or source[position] != item[1:]:
                        raise ValueError(f"patch context does not match exactly: {path}")
                    position += 1
                if item[0] in " +":
                    output.append(item[1:])
        output.extend(source[position:])
        if entry.new is None:
            if output:
                raise ValueError("deletion patch does not remove the entire file")
            changes[path] = None
        else:
            text = "".join(output)
            if b"\r\n" in raw:
                text = text.replace("\n", "\r\n")
            changes[path] = (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + text.encode("utf-8")
    return changes


def apply_patch_to_copy(snapshot: Snapshot, workspace: Path, patch: str, protected_paths: list[str]) -> list[str]:
    changes = patched_files(snapshot, patch, protected_paths)
    for relative, data in changes.items():
        path = workspace / relative
        if data is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return list(changes)
