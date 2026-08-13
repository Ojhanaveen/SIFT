"""Jest adapter.

WHY THIS LOOKS DIFFERENT FROM THE PYTHON ONE
--------------------------------------------
`coverage.py` has dynamic contexts, so pytest gets per-*test* attribution for
free: every measured line already knows the node id that executed it. Istanbul
and V8 have no equivalent. Coverage accumulates across a whole run and there is
nothing in the output saying which test touched which line.

So this adapter traces one spec file at a time and attributes everything that
run covered to the spec file. A test id here is a **spec file path**, not an
individual test.

That is coarser than Python, and deliberately so. It over-selects -- changing a
line covered by one test in a spec pulls in the whole spec -- and over-selecting
is the survivable direction. Being wrong is fatal; being slow is not. A future
reporter-hook implementation can narrow this without changing the map format,
because ids stay re-runnable either way.

ABOUT module_level
------------------
It stays empty here, and that is correct rather than an omission. Python needs
it because coverage.py can report lines that ran with *no test active*, which
belong to no test and must force a full run. Jest gives every spec file a fresh
module registry, so imports re-execute per spec: import-time code is genuinely
attributed to each spec that triggers it. A module no spec imports gets no
coverage at all, is therefore absent from the map, and fails open on the
"not in the map" gate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..model import CoverageMap, TestId
from .base import LanguageProfile

JAVASCRIPT = LanguageProfile(
    source_extensions=(".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"),
    test_file_patterns=(
        "*.test.js", "*.test.jsx", "*.test.ts", "*.test.tsx",
        "*.spec.js", "*.spec.jsx", "*.spec.ts", "*.spec.tsx",
        "__tests__/*", "*/__tests__/*",
    ),
    always_run_patterns=(
        # dependency graph changed
        "package.json",
        "*/package.json",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "npm-shrinkwrap.json",
        # test harness / build config -- any of these can change what the
        # runner does with every file in the repo
        "jest.config.*",
        "jest.setup.*",
        "vitest.config.*",
        "babel.config.*",
        ".babelrc*",
        "tsconfig*.json",
        "*/tsconfig*.json",
    ),
)

CONFIG_FILES = (
    "jest.config.js", "jest.config.cjs", "jest.config.mjs",
    "jest.config.ts", "jest.config.json",
)


class JestAdapter:
    name = "jest"
    profile = JAVASCRIPT

    def __init__(self, root: Path, args: Optional[Sequence[str]] = None):
        # Resolved eagerly: jest runs with cwd=root and resolves --runTestsByPath
        # against it, so handing it a relative path silently doubles the prefix
        # and every spec "fails to run" with ENOENT.
        self.root = Path(root).resolve()
        self.extra_args = list(args or [])

    # -- detection ---------------------------------------------------------

    def detect(self) -> bool:
        if any((self.root / c).exists() for c in CONFIG_FILES):
            return True
        pkg = self.root / "package.json"
        if not pkg.exists():
            return False
        try:
            data = json.loads(pkg.read_text())
        except (ValueError, OSError):
            return False  # unreadable package.json: not ours to claim
        if "jest" in data:
            return True
        for section in ("dependencies", "devDependencies"):
            if "jest" in (data.get(section) or {}):
                return True
        return False

    # -- discovery ---------------------------------------------------------

    def discover(self) -> List[TestId]:
        proc = self._jest("--listTests", capture=True)
        specs = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            rel = self._relative(line)
            if rel:
                specs.append(rel)
        return sorted(specs)

    # -- tracing -----------------------------------------------------------

    def trace(self, commit: str) -> CoverageMap:
        """One jest run per spec file, coverage attributed to that spec.

        Slow by construction -- N spec files means N processes. Correctness is
        the product; speed here is a later optimisation.
        """
        tmap = CoverageMap(commit=commit, adapter=self.name)
        index: Dict[str, int] = {}

        for spec in self.discover():
            with tempfile.TemporaryDirectory() as tmp:
                proc = self._jest(
                    "--coverage",
                    "--coverageReporters=json",
                    f"--coverageDirectory={tmp}",
                    "--runTestsByPath", str(self.root / spec),
                    capture=True,
                )
                # returncode 1 means tests failed; the coverage is still valid
                # and a failing suite must still produce a usable map.
                report = Path(tmp) / "coverage-final.json"
                if not report.exists():
                    continue
                try:
                    data = json.loads(report.read_text())
                except ValueError:
                    continue
            self._absorb(data, spec, tmap, index)

        return tmap

    def _absorb(self, data: dict, spec: TestId, tmap: CoverageMap,
                index: Dict[str, int]) -> None:
        """Fold one istanbul coverage-final.json into the map."""
        for abs_path, entry in data.items():
            rel = self._relative(abs_path)
            if rel is None:
                continue  # outside the repo (node_modules) -- not our concern

            covered = _covered_lines(entry)
            if not covered:
                continue

            if spec not in index:
                index[spec] = len(tmap.tests)
                tmap.tests.append(spec)
            idx = index[spec]

            by_line = tmap.lines.setdefault(rel, {})
            for lineno in covered:
                ids = by_line.setdefault(lineno, [])
                if idx not in ids:
                    ids.append(idx)

    # -- running -----------------------------------------------------------

    def run(self, tests: Optional[Sequence[TestId]] = None) -> int:
        if tests is not None and not tests:
            return 0
        args = self._run_args(tests)
        return subprocess.run(args, cwd=self.root).returncode

    def run_capture(
        self, tests: Optional[Sequence[TestId]] = None
    ) -> Tuple[int, List[TestId]]:
        """Run, and report which spec files failed.

        Jest writes its machine-readable report to stdout under --json and its
        human-readable progress to stderr, so we parse the former and pass the
        latter through.
        """
        args = self._run_args(tests) + ["--json"]
        proc = subprocess.run(args, cwd=self.root, capture_output=True, text=True)
        if proc.stderr:
            sys.stderr.write(proc.stderr)

        failed: List[TestId] = []
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            # No parseable report: we cannot tell what failed. Claiming "nothing
            # failed" here would let shadow mode report a clean bill it has not
            # earned, so say everything failed instead and stay pessimistic.
            return proc.returncode, list(tests or self.discover())

        for result in report.get("testResults", []):
            if result.get("status") == "failed":
                rel = self._relative(result.get("name", ""))
                if rel:
                    failed.append(rel)
        return proc.returncode, failed

    # -- plumbing ----------------------------------------------------------

    def _run_args(self, tests: Optional[Sequence[TestId]]) -> List[str]:
        args = self._jest_argv()
        args.extend(self.extra_args)
        if tests is not None:
            args.append("--runTestsByPath")
            args.extend(str(self.root / t) for t in tests)
        return args

    def _jest_argv(self) -> List[str]:
        npx = shutil.which("npx")
        if npx is None:
            raise RuntimeError(
                "jest adapter needs Node.js on PATH (npx was not found)"
            )
        return [npx, "--no-install", "jest", "--ci"]

    def _jest(self, *flags: str, capture: bool = False):
        args = self._jest_argv() + list(flags)
        return subprocess.run(
            args, cwd=self.root, text=True,
            capture_output=capture,
            env=dict(os.environ, CI="1"),
        )

    def _relative(self, path: str) -> Optional[str]:
        if not path:
            return None
        try:
            rel = Path(path).resolve().relative_to(self.root.resolve())
        except ValueError:
            return None
        rel_str = str(rel)
        if rel_str.startswith("node_modules" + os.sep):
            return None
        return rel_str


def _covered_lines(entry: dict) -> set:
    """Line numbers actually executed, from one file's istanbul entry.

    Istanbul reports per *statement*, each with a start and end line, plus a
    hit count. A statement spanning several lines marks all of them, which
    slightly over-attributes for multi-line expressions -- the safe direction.
    """
    statements = entry.get("statementMap") or {}
    counts = entry.get("s") or {}
    lines = set()
    for key, loc in statements.items():
        if not counts.get(key):
            continue
        try:
            start = int(loc["start"]["line"])
            end = int(loc.get("end", {}).get("line", start) or start)
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            start, end = end, start
        lines.update(range(start, end + 1))
    return lines
