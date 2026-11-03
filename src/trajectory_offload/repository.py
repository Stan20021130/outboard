"""Frozen source snapshots. No evaluated code, shell tools, or write operations."""

import ast
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess

from .contracts import Evidence, Settings


EXCLUDED_DIRS = {".git", ".codex", ".agents", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                 ".mypy_cache", ".ruff_cache", "dist", "build", ".trajectory"}
TEXT_SUFFIXES = {".py", ".pyi", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".json",
                 ".md", ".rst", ".txt", ".sql", ".sh", ".ps1", ".html", ".jinja",
                 ".jinja2", ".xml", ".csv"}
TEXT_NAMES = {"Dockerfile", "Makefile", "Procfile", ".gitignore", ".env.example"}
SECRET_NAMES = {"auth.json", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml"}


def safe_relative(value: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or ":" in value or "\x00" in value:
        raise ValueError("paths must be relative to the configured repository")
    normalized = path.as_posix()
    return "" if normalized == "." else normalized


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass
class Snapshot:
    root: Path
    snapshot_id: str
    files: dict[str, str]
    digests: dict[str, str]
    excluded: list[str]
    raw_files: dict[str, bytes]

    @classmethod
    def capture(cls, root: Path, settings: Settings):
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("repository must be a directory")
        try:
            if not any((parent / ".git").exists() for parent in [root, *root.parents]):
                raise FileNotFoundError("not a Git worktree")
            result = subprocess.run(
                [shutil.which("git") or "git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env={**{k: v for k, v in os.environ.items() if not k.startswith("CODEX_")},
                     "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
            )
            candidates = {p.decode("utf-8", "surrogateescape") for p in result.stdout.split(b"\0") if p}
        except (FileNotFoundError, subprocess.CalledProcessError):
            candidates = set()
            for directory, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not _is_reparse(Path(directory) / d))
                for name in names:
                    candidates.add((Path(directory) / name).relative_to(root).as_posix())
        files, digests, excluded, raw_files = {}, {}, [], {}
        total = 0
        for raw in sorted(candidates):
            relative = safe_relative(raw)
            parts = PurePosixPath(relative).parts
            name = parts[-1]
            if any(part in EXCLUDED_DIRS for part in parts) or name in SECRET_NAMES:
                continue
            if name.startswith(".env") and name != ".env.example":
                continue
            if Path(name).suffix.lower() not in TEXT_SUFFIXES and name not in TEXT_NAMES:
                continue
            path = root / relative
            if not path.exists():
                continue
            if not path.is_file():
                excluded.append(relative + ": not a regular file")
                continue
            if any(_is_reparse(parent) for parent in [path, *path.parents] if parent != root and parent.is_relative_to(root)):
                excluded.append(relative + ": symlink/reparse point")
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError("source escaped repository root")
            if path.stat().st_size > settings.max_file_bytes:
                raise ValueError(f"source file exceeds max_file_bytes: {relative}")
            data = path.read_bytes()
            total += len(data)
            if total > settings.max_snapshot_bytes or len(files) >= settings.max_files:
                raise ValueError("snapshot limit exceeded; narrow the configured repository or increase server limits")
            try:
                content = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                excluded.append(relative + ": not UTF-8")
                continue
            if "\x00" in content:
                excluded.append(relative + ": binary")
                continue
            files[relative] = content.replace("\r\n", "\n")
            raw_files[relative] = data
            digests[relative] = hashlib.sha256(data).hexdigest()
        digest = hashlib.sha256()
        for path, file_hash in sorted(digests.items()):
            digest.update(path.encode("utf-8") + b"\0" + file_hash.encode() + b"\n")
        for entry in excluded:
            digest.update(entry.encode("utf-8") + b"\n")
        return cls(root, digest.hexdigest(), files, digests, excluded, raw_files)

    def scoped(self, prefixes: list[str]) -> list[str]:
        normalized = [safe_relative(p).rstrip("/") for p in prefixes]
        return [p for p in self.files if not normalized or any(not s or p == s or p.startswith(s + "/") for s in normalized)]


class RepositoryTools:
    def __init__(self, snapshot: Snapshot, scope: list[str]):
        self.snapshot = snapshot
        self.paths = snapshot.scoped(scope)
        if not self.paths:
            raise ValueError("scope contains no supported source files")
        self.observed: dict[str, list[tuple[int, int]]] = {}
        self._nodes = None
        self._imports = None

    def import_edges(self) -> list[tuple[str, str, int]]:
        if self._imports is not None:
            return self._imports
        modules = {}
        canonical = {}
        for path in self.paths:
            if path.endswith(".py"):
                name = path[:-3].replace("/", ".").removesuffix(".__init__").removeprefix("src.")
                canonical[path] = name
                modules[name] = path
                if path.startswith("src/"):
                    modules["src." + name] = path
        edges = set()
        for path, module in canonical.items():
            try:
                tree = ast.parse(self.snapshot.files[path])
            except (SyntaxError, RecursionError):
                continue
            package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    parts = package.split(".") if package else []
                    prefix = parts[:len(parts) - node.level + 1] if node.level else []
                    name = ".".join([*prefix, *([node.module] if node.module else [])])
                    names = [name, *[".".join(filter(None, (name, alias.name))) for alias in node.names]]
                for name in names:
                    if name in modules:
                        edges.add((path, modules[name], node.lineno))
        self._imports = sorted(edges)
        return self._imports

    def impact_context(self, changed_paths: list[str], symbols: list[str], added_paths: list[str] | None = None) -> dict:
        changed = []
        for value in changed_paths:
            path = safe_relative(value)
            matches = [p for p in self.paths if p == path or p.startswith(path + "/")]
            if not matches and path not in (added_paths or []):
                raise ValueError(f"changed path is outside the task scope or snapshot: {path}")
            changed.extend(matches)
        changed = sorted(set(changed))
        if len(changed) > 20:
            raise ValueError("impact analysis accepts at most 20 changed source files")
        seeds = list(symbols)
        for path in changed:
            if path.endswith(".py"):
                try:
                    tree = ast.parse(self.snapshot.files[path])
                    seeds.extend(node.name for node in ast.iter_child_nodes(tree)
                                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
                except (SyntaxError, RecursionError):
                    pass
        seeds = list(dict.fromkeys(seeds))
        relations, reached, frontier = [], set(changed), set(changed)
        edges = self.import_edges()
        for depth in range(1, 3):
            following = set()
            for importer, target, line in edges:
                if target in frontier and importer not in reached:
                    if len(relations) >= 30:
                        break
                    relations.append({"importer": importer, "imports": target, "depth": depth,
                                      "evidence": self.excerpt(importer, line, line)})
                    following.add(importer)
            reached.update(following)
            frontier = following
        excerpts = []
        for path in changed[:8]:
            count = len(self.snapshot.files[path].splitlines())
            if count:
                excerpts.append(self.excerpt(path, 1, min(count, 60)))
        references = {symbol: {"definitions": self.definitions(symbol), **self.references(symbol)} for symbol in seeds[:8]}
        tests = sorted({p for p in reached if "/tests/" in "/" + p or Path(p).name.startswith("test_")}
                       | {ref["path"] for value in references.values() for ref in value["references"]
                          if "/tests/" in "/" + ref["path"] or Path(ref["path"]).name.startswith("test_")})
        return {"changed_paths": changed, "added_paths": added_paths or [], "symbols": seeds[:8],
                "import_dependents": relations, "symbol_evidence": references, "source_excerpts": excerpts,
                "candidate_tests": tests[:20], "scope": self.paths[:80], "scope_file_count": len(self.paths),
                "coverage_complete": False, "max_import_depth": 2,
                "truncated": len(seeds) > 8 or len(changed) > 8 or len(relations) >= 30 or len(tests) > 20
                             or any(value["truncated"] for value in references.values()),
                "method": "bounded static imports and lexical references; candidates are not a complete runtime impact set"}

    def prefetch_context(self, objective: str, leads: list[str], max_files: int = 8, max_lines: int = 360) -> dict:
        """Compile the recurring search -> nearby imports -> bounded reads sequence without model round trips."""
        stopwords = {"locate", "identify", "investigate", "source", "implementation", "related", "tests", "test",
                     "with", "from", "that", "this", "through", "including", "relevant", "code", "trace"}
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", objective + " " + " ".join(leads))
        terms = list(dict.fromkeys(t.casefold() for t in tokens if len(t) >= 4 and t.casefold() not in stopwords))[:24]
        scores = {}
        for path in self.paths:
            text = self.snapshot.files[path].casefold()
            score = sum((4 if term in path.casefold() else 1) * (3 if "_" in term else 1)
                        for term in terms if term in text or term in path.casefold())
            if score:
                scores[path] = score
        if not scores:
            return {"strategy": "bounded lexical/import prefetch", "excerpts": [], "truncated": False}
        selected = sorted(scores, key=lambda p: (-scores[p], p))[:4]
        neighbors = {}
        for path, target, _ in self.import_edges():
            neighbors.setdefault(path, set()).add(target)
            neighbors.setdefault(target, set()).add(path)
        queue = list(selected)
        while queue and len(selected) < max_files:
            path = queue.pop(0)
            for other in sorted(neighbors.get(path, []), key=lambda p: (-scores.get(p, 0), p)):
                if other not in selected:
                    selected.append(other)
                    queue.append(other)
                    if len(selected) == max_files:
                        break
        excerpts, remaining = [], max_lines
        truncated = len(scores) > max_files
        for path in selected:
            lines = self.snapshot.files[path].splitlines()
            if not lines or remaining <= 0:
                truncated |= bool(lines)
                continue
            if len(lines) <= min(80, remaining):
                ranges = [(1, len(lines))]
            else:
                hits = [n for n, line in enumerate(lines, 1) if any(term in line.casefold() for term in terms)]
                ranges = [(1, min(12, len(lines)))]
                for number in hits[:2]:
                    start, end = max(1, number - 6), min(len(lines), number + 14)
                    if not any(a <= start <= end <= b for a, b in ranges):
                        ranges.append((start, end))
                truncated = True
            for start, end in ranges:
                if remaining <= 0:
                    break
                end = min(end, start + remaining - 1)
                try:
                    excerpts.append(self.excerpt(path, start, end))
                    remaining -= end - start + 1
                except ValueError:
                    truncated = True
        return {"strategy": "bounded lexical/import prefetch; candidates are not proof of completeness",
                "anchors": terms, "excerpts": excerpts, "truncated": truncated}

    def excerpt(self, path: str, start_line: int, end_line: int) -> dict:
        path = safe_relative(path)
        if path not in self.paths:
            raise ValueError("file is outside the captured task scope")
        lines = self.snapshot.files[path].splitlines()
        if start_line < 1 or start_line > len(lines) or end_line < start_line or end_line - start_line >= 100:
            raise ValueError("invalid source range; start at an existing line and request at most 100 lines")
        end_line = min(end_line, len(lines))
        source = "\n".join(lines[start_line - 1:end_line])
        if len(source.encode("utf-8")) > 16000:
            raise ValueError("source excerpt exceeds 16000 bytes; request a smaller range")
        self.observed.setdefault(path, []).append((start_line, end_line))
        return {"path": path, "start_line": start_line, "end_line": end_line,
                "source": source}

    def list_files(self, query: str = "", limit: int = 80) -> dict:
        found = [p for p in self.paths if query.casefold() in p.casefold()]
        return {"files": found[:limit], "line_counts": {p: len(self.snapshot.files[p].splitlines()) for p in found[:limit]},
                "total_matches": len(found), "truncated": len(found) > limit}

    def search(self, queries: list[str], limit: int = 24) -> dict:
        if not queries or len(queries) > 8 or any(not q.strip() or len(q) > 160 for q in queries):
            raise ValueError("provide 1-8 nonempty literal queries, at most 160 characters each")
        if not 1 <= limit <= 60:
            raise ValueError("limit must be between 1 and 60")
        queries = [q.casefold() for q in queries]
        hits = []
        for path in self.paths:
            for number, line in enumerate(self.snapshot.files[path].splitlines(), 1):
                score = sum(q in line.casefold() for q in queries)
                if score:
                    hits.append((score, path, number))
        hits.sort(key=lambda h: (-h[0], h[1], h[2]))
        excerpts = [self.excerpt(path, number, number) for _, path, number in hits[:limit]]
        return {"matches": excerpts, "total_matches": len(hits), "truncated": len(hits) > limit}

    def definitions(self, symbol: str) -> list[dict]:
        self._check_symbol(symbol)
        found = [(path, node) for path, name, node, _ in self.symbol_nodes()
                 if name == symbol or name.endswith("." + symbol)]
        return [self.excerpt(path, node.lineno, min(node.end_lineno, node.lineno + 59))
                for path, node in found[:8]]

    @staticmethod
    def _check_symbol(symbol: str):
        if not symbol or not all(part.isidentifier() for part in symbol.split(".")):
            raise ValueError("symbol must be a Python name, optionally qualified")

    def symbol_nodes(self) -> list[tuple]:
        """Snapshot-local qualified AST index; never imports evaluated code."""
        if self._nodes is None:
            self._nodes = []
            def visit(path, node, parents):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = ".".join([*parents, node.name])
                    doc = node.body[0] if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str) else None
                    self._nodes.append((path, name, node, doc))
                    parents = [*parents, node.name]
                for child in ast.iter_child_nodes(node):
                    visit(path, child, parents)
            for path in self.paths:
                if path.endswith((".py", ".pyi")):
                    try:
                        module = path[:-3].replace("/", ".").removeprefix("src.").removesuffix(".__init__")
                        visit(path, ast.parse(self.snapshot.files[path]), [module])
                    except (SyntaxError, RecursionError):
                        continue
        return self._nodes

    def focused_context(self, objective: str, leads: list[str], symbols: list[str],
                        changed_paths: list[str], max_lines: int = 260, max_chars: int = 16000) -> dict:
        """Select executable anchor windows and whole small tests, without broad class tours."""
        stop = {"investigate", "identify", "implementation", "regression", "configuration", "behavior",
                "describe", "related", "source", "tests", "test", "existing", "static", "impact", "preserve",
                "public", "change", "changes", "before", "after", "which", "where", "with", "from", "that",
                "this", "should", "would", "could", "must", "does", "have", "into", "using", "through"}
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", " ".join([objective, *leads, *symbols]))
        terms = sorted({word.casefold() for word in words if len(word) >= 4 and word.casefold() not in stop},
                       key=lambda word: ("_" not in word, word))[:40]
        primary = {term for term in terms if "_" in term and not term.startswith("__")}
        lines = {path: self.snapshot.files[path].splitlines() for path in self.paths}
        docs, owners = {}, {}
        for path, _, node, doc in self.symbol_nodes():
            if doc:
                docs.setdefault(path, set()).update(range(doc.lineno, doc.end_lineno + 1))
            if not isinstance(node, ast.ClassDef):
                for number in range(node.lineno, node.end_lineno + 1):
                    owners[(path, number)] = node
        patterns = {term: re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I) for term in terms}
        hits, counts = [], dict.fromkeys(terms, 0)
        for path, source in lines.items():
            if not path.endswith((".py", ".pyi", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".json")):
                continue
            for number, line in enumerate(source, 1):
                if number in docs.get(path, ()) or line.lstrip().startswith("#"):
                    continue
                matched = {term for term, pattern in patterns.items() if pattern.search(line)}
                if primary and not matched.intersection(primary):
                    continue
                if matched:
                    hits.append((path, number, matched))
                    for term in matched:
                        counts[term] += 1
        objective_terms = {term.casefold() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", objective)}
        weights = {term: (6 if term in primary else 0.1 if primary else 1) * (2 if term in objective_terms else 1)
                   / (1 + math.log2(max(1, count)))
                   for term, count in counts.items()}
        candidates, density = {}, {}
        for path, number, matched in hits:
            node = owners.get((path, number))
            if node and node.end_lineno - node.lineno < 60:
                start, end = node.lineno, node.end_lineno
            else:
                start, end = max(1, number - 5), min(len(lines[path]), number + 12)
                if node:
                    start, end = max(start, node.lineno), min(end, node.end_lineno)
                # A long docstring immediately above the hit adds no executable evidence.
                while start < number and start in docs.get(path, ()):
                    start += 1
            key = (path, start, end)
            candidates.setdefault(key, set()).update(matched)
            density[key] = density.get(key, 0) + 1
        # Adjacent hits in one implementation become one useful block rather than
        # competing one-line windows (a signature alone rarely explains behavior).
        merged, merged_density = {}, {}
        previous = None
        for key in sorted(candidates):
            path, start, end = key
            if previous and previous[0] == path and start <= previous[2] + 1 and max(end, previous[2]) - previous[1] < 80:
                combined = (path, previous[1], max(end, previous[2]))
                terms_in_block = merged.pop(previous) | candidates[key]
                count = merged_density.pop(previous) + density[key]
            else:
                combined, terms_in_block, count = key, candidates[key], density[key]
            merged[combined], merged_density[combined], previous = terms_in_block, count, combined
        candidates, density = merged, merged_density
        selected, per_group, covered, seen, used_lines, used_chars = [], {False: 0, True: 0}, {False: {}, True: {}}, {}, 0, 0
        remaining = set(candidates)
        def test_path(path):
            return "/tests/" in "/" + path or Path(path).name.startswith("test_")
        def score(key):
            group = test_path(key[0])
            return (sum(weights[term] / (1 + 4 * covered[group].get(term, 0)) for term in candidates[key])
                    * (1 + math.log2(density[key])) + (0.3 if key[0] in changed_paths else 0))
        while remaining:
            path, start, end = min(remaining, key=lambda key: (-score(key), key))
            remaining.remove((path, start, end))
            is_test = test_path(path)
            if per_group[is_test] >= (4 if is_test else 6):
                remaining = {key for key in remaining if test_path(key[0]) != is_test}
                continue
            if any(a <= end and start <= b for a, b in seen.get(path, [])):
                continue
            source = "\n".join(lines[path][start - 1:end])
            if used_lines + end - start + 1 > max_lines or used_chars + len(source) > max_chars:
                continue
            selected.append(self.excerpt(path, start, end))
            seen.setdefault(path, []).append((start, end))
            per_group[is_test] += 1
            for term in candidates[(path, start, end)]:
                covered[is_test][term] = covered[is_test].get(term, 0) + 1
            used_lines += end - start + 1
            used_chars += len(source)
            remaining = {key for key in remaining if not (key[0] == path and key[1] <= end and start <= key[2])}
        return {"strategy": "bounded executable anchor windows and small test bodies; static candidates only",
                "anchors": terms, "excerpts": selected, "truncated": len(selected) < len(candidates),
                "source_lines": used_lines, "source_chars": used_chars}

    def references(self, symbol: str, limit: int = 30) -> dict:
        self._check_symbol(symbol)
        if not 1 <= limit <= 30:
            raise ValueError("reference limit must be between 1 and 30")
        symbol = symbol.rsplit(".", 1)[-1]
        pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
        hits = [(path, number) for path in self.paths
                for number, line in enumerate(self.snapshot.files[path].splitlines(), 1) if pattern.search(line)]
        return {"references": [self.excerpt(path, number, number) for path, number in hits[:limit]],
                "total_matches": len(hits), "truncated": len(hits) > limit,
                "method": "lexical identifier references, not a resolved call graph"}

    def inspect_symbols(self, symbols: list[str]) -> dict:
        if not 1 <= len(symbols) <= 4:
            raise ValueError("inspect 1-4 symbols per call")
        output = {}
        for symbol in symbols:
            self._check_symbol(symbol)
            definitions = []
            for path, name, node, doc in self.symbol_nodes():
                if name != symbol and not name.endswith("." + symbol):
                    continue
                entry = {"path": path, "symbol": name, "start_line": node.lineno, "end_line": node.end_lineno}
                if isinstance(node, ast.ClassDef):
                    entry["methods"] = [{"name": child.name, "start_line": child.lineno, "end_line": child.end_lineno}
                                        for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))][:24]
                    entry["definition"] = self.excerpt(path, node.lineno, node.lineno)
                elif doc:
                    entry["signature"] = self.excerpt(path, node.lineno, max(node.lineno, min(doc.lineno - 1, node.lineno + 59)))
                    if doc.end_lineno < node.end_lineno:
                        entry["body"] = self.excerpt(path, doc.end_lineno + 1, min(node.end_lineno, doc.end_lineno + 45))
                else:
                    entry["definition"] = self.excerpt(path, node.lineno, min(node.end_lineno, node.lineno + 59))
                definitions.append(entry)
                if len(definitions) == 4:
                    break
            references = self.references(symbol, limit=8)
            output[symbol] = {"definitions": definitions, **references}
        return output

    def validate_evidence(self, evidence: Evidence, require_observed: bool = True) -> str | None:
        try:
            path = safe_relative(evidence.path)
            if path not in self.paths:
                return "citation outside task scope"
            lines = self.snapshot.files[path].splitlines()
            if not 1 <= evidence.start_line <= evidence.end_line <= len(lines):
                return "citation has an invalid line range"
            if evidence.end_line - evidence.start_line >= 100:
                return "citation range is too broad"
            source = "\n".join(lines[evidence.start_line - 1:evidence.end_line])
            if evidence.quote not in source:
                return "citation quote is not present in the referenced source range"
            if require_observed and not all(any(a <= n <= b for a, b in self.observed.get(path, []))
                                            for n in range(evidence.start_line, evidence.end_line + 1)):
                return "citation includes source that was not observed through repository tools"
        except ValueError as exc:
            return str(exc)
        return None


def build_repo_recon(snapshot: Snapshot) -> dict:
    """Pure static, deterministic repository reconnaissance.
    0 model calls, 0 token consumption, <1ms execution.
    """
    paths = list(snapshot.files)
    manifest_candidates = [
        "pyproject.toml", "setup.cfg", "setup.py", "requirements.txt",
        "package.json", "Cargo.toml", "go.mod", "Makefile", "Dockerfile"
    ]
    manifests = [p for p in manifest_candidates if p in snapshot.files]

    src_dirs = set()
    test_dirs = set()
    test_files = []
    py_files = 0
    total_lines = 0

    for path in paths:
        parts = PurePosixPath(path).parts
        if path.endswith(".py"):
            py_files += 1
            total_lines += len(snapshot.files[path].splitlines())

        if any(part in {"tests", "test", "testing"} for part in parts[:-1]):
            test_dirs.add(parts[0] if parts[0] in {"tests", "test"} else "/".join(parts[:2]))
            if parts[-1].startswith("test_") or parts[-1].endswith("_test.py"):
                test_files.append(path)
        elif not any(part in {"docs", "examples", "benchmarks", "scripts", "bin"} for part in parts):
            if parts[0] == "src" and len(parts) > 1:
                src_dirs.add("src/" + parts[1])
            elif len(parts) > 1 and parts[-1].endswith(".py"):
                src_dirs.add(parts[0])

    key_entry_points = []
    for candidate in [
        "src/__init__.py", "cli.py", "main.py", "core.py", "app.py",
        "manage.py", "wsgi.py", "asgi.py"
    ]:
        for path in paths:
            if path.endswith("/" + candidate) or path == candidate:
                key_entry_points.append(path)

    framework = "unknown"
    if any("pytest" in snapshot.files.get(m, "") for m in manifests) or test_files:
        framework = "pytest"
    elif any("unittest" in snapshot.files.get(m, "") for m in manifests):
        framework = "unittest"

    return {
        "manifests": manifests,
        "source_layout": {
            "source_roots": sorted(src_dirs)[:8],
            "test_dirs": sorted(test_dirs)[:4],
            "framework": framework,
            "sample_tests": sorted(test_files)[:6],
        },
        "entry_points": sorted(set(key_entry_points))[:8],
        "stats": {
            "total_files": len(paths),
            "python_files": py_files,
            "approx_python_lines": total_lines,
        }
    }
