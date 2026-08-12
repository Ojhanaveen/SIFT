"""Core data types.

The map is the central artefact: it records which test touched which line of
which file. Test ids are interned to integers because the raw ids repeat on
every line and the file would otherwise be enormous.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set

TestId = str

MAP_VERSION = 1


@dataclass
class CoverageMap:
    """test -> code mapping, captured at a specific commit."""

    commit: str
    adapter: str
    tests: List[TestId] = field(default_factory=list)
    # file -> line -> [index into self.tests]
    lines: Dict[str, Dict[int, List[int]]] = field(default_factory=dict)
    # file -> lines that executed with NO test context (import/module level).
    # A change to one of these cannot be attributed to any single test, so it
    # forces a full run. Missing this is a silent-miss bug.
    module_level: Dict[str, Set[int]] = field(default_factory=dict)

    def tests_for(self, path: str, lineno: int) -> Set[TestId]:
        by_line = self.lines.get(path)
        if not by_line:
            return set()
        return {self.tests[i] for i in by_line.get(lineno, ())}

    def covers_file(self, path: str) -> bool:
        return path in self.lines or path in self.module_level

    def is_module_level(self, path: str, lineno: int) -> bool:
        return lineno in self.module_level.get(path, ())

    # -- serialisation ----------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": MAP_VERSION,
                "commit": self.commit,
                "adapter": self.adapter,
                "tests": self.tests,
                "lines": {
                    f: {str(ln): ids for ln, ids in by_line.items()}
                    for f, by_line in self.lines.items()
                },
                "module_level": {
                    f: sorted(lns) for f, lns in self.module_level.items()
                },
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "CoverageMap":
        data = json.loads(raw)
        if data.get("version") != MAP_VERSION:
            raise ValueError(f"unsupported map version: {data.get('version')}")
        return cls(
            commit=data["commit"],
            adapter=data["adapter"],
            tests=data["tests"],
            lines={
                f: {int(ln): ids for ln, ids in by_line.items()}
                for f, by_line in data["lines"].items()
            },
            module_level={f: set(lns) for f, lns in data["module_level"].items()},
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json())

    @classmethod
    def read(cls, path: Path) -> "CoverageMap":
        return cls.from_json(path.read_text())


@dataclass
class Selection:
    """The outcome of a selection pass."""

    tests: List[TestId]
    run_all: bool
    reasons: List[str] = field(default_factory=list)
    # test id -> why it was picked, for `sift explain`
    why: Dict[TestId, List[str]] = field(default_factory=dict)

    def add(self, test: TestId, reason: str) -> None:
        self.why.setdefault(test, [])
        if reason not in self.why[test]:
            self.why[test].append(reason)
