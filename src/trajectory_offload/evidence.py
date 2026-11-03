"""Runtime-owned citations: the model selects evidence, never transcribes source."""

from .contracts import Evidence


class EvidenceCatalog:
    def __init__(self):
        self.entries: dict[str, Evidence] = {}
        self.keys = {}
        self.lines = {}

    def encode(self, value):
        if isinstance(value, list):
            return [self.encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if {"path", "start_line", "end_line", "source"} <= value.keys():
            # Reuse previously delivered fragments even when read boundaries differ.
            # A returned old ID can cover surrounding lines, all already observed.
            previous, fresh, groups = set(), [], []
            for number, line in enumerate(value["source"].split("\n"), value["start_line"]):
                known = self.lines.get((value["path"], number))
                if known:
                    previous.update(known)
                    if fresh:
                        groups.append(fresh)
                        fresh = []
                else:
                    fresh.append((number, line))
            if fresh:
                groups.append(fresh)
            if previous:
                output = [{"id": identifier, "path": self.entries[identifier].path,
                           "start_line": self.entries[identifier].start_line, "end_line": self.entries[identifier].end_line,
                           "already_provided": True} for identifier in sorted(previous, key=lambda item: int(item[1:]))]
                for group in groups:
                    output.extend(self.encode({"path": value["path"], "start_line": group[0][0], "end_line": group[-1][0],
                                               "source": "\n".join(line for _, line in group)})["excerpts"])
                return {"excerpts": output}
            # Every fragment is an exact, observed substring, within the public quote limit.
            fragments, current, start = [], [], value["start_line"]
            for number, line in enumerate(value["source"].split("\n"), value["start_line"]):
                if current and len("\n".join([*current, line])) > 1800:
                    fragments.append((start, number - 1, "\n".join(current)))
                    current, start = [], number
                if len(line) > 1800:
                    fragments.extend((number, number, line[i:i + 1800]) for i in range(0, len(line), 1800))
                    start = number + 1
                else:
                    if not current:
                        start = number
                    current.append(line)
            if current:
                fragments.append((start, value["end_line"], "\n".join(current)))
            output = []
            for first, last, quote in fragments:
                if not quote.strip():
                    continue
                key = (value["path"], first, last, quote)
                existing = key in self.keys
                identifier = self.keys.setdefault(key, f"E{len(self.keys) + 1}")
                evidence = Evidence(path=value["path"], start_line=first, end_line=last, quote=quote)
                self.entries[identifier] = evidence
                for number in range(first, last + 1):
                    self.lines.setdefault((evidence.path, number), set()).add(identifier)
                item = {"id": identifier, "path": evidence.path, "start_line": first, "end_line": last}
                item.update({"already_provided": True} if existing else {"source": quote})
                output.append(item)
            return {"excerpts": output}
        return {key: self.encode(item) for key, item in value.items()}

    def expand(self, value):
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"id"}:
                identifier = value["id"]
                if not isinstance(identifier, str) or identifier not in self.entries:
                    raise ValueError(f"unknown observed evidence ID: {identifier!r}")
                return self.entries[identifier].model_dump()
            return {key: self.expand(item) for key, item in value.items()}
        return value


def citation_schema(schema: dict) -> dict:
    schema["$defs"]["Evidence"] = {
        "type": "object", "properties": {"id": {"type": "string", "description": "Observed evidence ID, for example E3"}},
        "required": ["id"], "additionalProperties": False,
    }
    return schema


def compact_evidence(result: dict) -> dict:
    """Lossless public projection: repeated citations become references to one table."""
    table, keys = {}, {}
    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {"path", "start_line", "end_line", "quote"}:
                key = (value["path"], value["start_line"], value["end_line"], value["quote"])
                identifier = keys.setdefault(key, f"E{len(keys) + 1}")
                table[identifier] = value
                return {"id": identifier}
            return {key: project(item) for key, item in value.items()}
        return value
    projected = project(result)
    projected["evidence"] = table
    return projected
