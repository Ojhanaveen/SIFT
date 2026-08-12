"""pytest adapter.

Builds the map using coverage.py's dynamic contexts. `pytest-cov --cov-context=test`
labels every measured line with the pytest node id that executed it, which is
exactly the mapping we need -- and the node ids are directly re-runnable.

Contexts arrive suffixed with the test phase (`|setup`, `|run`, `|teardown`);
we strip that. The empty context means "executed with no test active", i.e.
import time -- recorded separately because it forces a full run.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from ..model import CoverageMap, TestId


class PytestAdapter:
    name = "pytest"

    def __init__(self, root: Path, args: Optional[Sequence[str]] = None):
        self.root = root
        self.extra_args = list(args or [])

    # -- detection ---------------------------------------------------------

    def detect(self) -> bool:
        markers = ["pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py"]
        if any((self.root / m).exists() for m in markers):
            return True
        return any(self.root.glob("test*/**/test_*.py")) or any(
            self.root.glob("test_*.py")
        )

    # -- discovery ---------------------------------------------------------

    def discover(self) -> List[TestId]:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "-p", "no:cacheprovider", *self.extra_args],
            cwd=self.root, capture_output=True, text=True,
        )
        tests = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if "::" in line and not line.startswith("="):
                tests.append(line.split(" ")[0])
        return tests

    # -- tracing -----------------------------------------------------------

    def trace(self, commit: str) -> CoverageMap:
        """Run the full suite under per-test coverage and build the map."""
        data_file = self.root / ".sift" / ".coverage-trace"
        data_file.parent.mkdir(parents=True, exist_ok=True)
        for stale in data_file.parent.glob(".coverage-trace*"):
            stale.unlink()

        env = dict(os.environ, COVERAGE_FILE=str(data_file))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest",
             "--cov=.", "--cov-context=test", "--cov-report=", "--cov-branch",
             "-p", "no:cacheprovider", *self.extra_args],
            cwd=self.root, env=env, text=True,
        )
        if proc.returncode not in (0, 1):  # 1 == some tests failed, map is still valid
            raise RuntimeError(f"pytest exited {proc.returncode} while tracing")

        return self._build_map(data_file, commit)

    def _build_map(self, data_file: Path, commit: str) -> CoverageMap:
        from coverage import CoverageData

        data = CoverageData(basename=str(data_file))
        data.read()

        tmap = CoverageMap(commit=commit, adapter=self.name)
        index = {}

        for abs_path in data.measured_files():
            try:
                rel = str(Path(abs_path).resolve().relative_to(self.root.resolve()))
            except ValueError:
                continue  # outside the repo (site-packages) -- not our concern
            if rel.startswith(".sift" + os.sep):
                continue

            for lineno, contexts in data.contexts_by_lineno(abs_path).items():
                for ctx in contexts:
                    node = ctx.split("|")[0]
                    if not node:
                        tmap.module_level.setdefault(rel, set()).add(lineno)
                        continue
                    if node not in index:
                        index[node] = len(tmap.tests)
                        tmap.tests.append(node)
                    tmap.lines.setdefault(rel, {}).setdefault(lineno, [])
                    idx = index[node]
                    if idx not in tmap.lines[rel][lineno]:
                        tmap.lines[rel][lineno].append(idx)

        return tmap

    # -- running -----------------------------------------------------------

    def run(self, tests: Optional[Sequence[TestId]] = None) -> int:
        if tests is not None and not tests:
            return 0
        args = self._run_args(tests)
        return subprocess.run(args, cwd=self.root).returncode

    def run_capture(self, tests: Optional[Sequence[TestId]] = None):
        """Run and also report which test ids failed.

        Shadow mode needs the failure list to answer the only question that
        matters: would sift have skipped a test that actually failed?
        """
        args = self._run_args(tests) + ["--tb=no", "-q"]
        proc = subprocess.run(args, cwd=self.root, capture_output=True, text=True)
        sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)

        failed = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            for prefix in ("FAILED ", "ERROR "):
                if line.startswith(prefix):
                    node = line[len(prefix):].split(" - ")[0].strip()
                    if node:
                        failed.append(node)
        return proc.returncode, failed

    def _run_args(self, tests: Optional[Sequence[TestId]]) -> List[str]:
        args = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
        args.extend(self.extra_args)
        if tests is not None:
            args.extend(tests)
        return args
