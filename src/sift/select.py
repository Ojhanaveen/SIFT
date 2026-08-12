"""Selection engine.

Governing principle: being wrong is fatal, being slow is survivable. Every
ambiguity below resolves toward running MORE tests. If you are ever tempted to
make a rule narrower to squeeze out more speed, don't.
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, List, Optional, Set

from .gitdiff import Changes
from .model import Selection, CoverageMap

# A change to any of these can affect anything, in ways coverage cannot see.
ALWAYS_RUN_ALL = [
    # dependency graph changed
    "requirements*.txt",
    "*/requirements*.txt",
    "poetry.lock",
    "Pipfile.lock",
    "pdm.lock",
    "uv.lock",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    # test harness config
    "conftest.py",
    "*/conftest.py",
    "tox.ini",
    "pytest.ini",
    ".coveragerc",
    # environment
    "Dockerfile*",
    "docker-compose*.yml",
    ".github/**",
    ".gitlab-ci.yml",
    "Makefile",
]

TEST_FILE_PATTERNS = ["test_*.py", "*_test.py", "*/test_*.py", "*/*_test.py"]


def _matches(path: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch("/" + path, "*/" + pat):
            return True
    return False


def is_test_file(path: str) -> bool:
    return _matches(path, TEST_FILE_PATTERNS)


def _analysable(path: str) -> bool:
    """Can this adapter reason about the file at all?"""
    return path.endswith(".py")


def select(
    changes: Changes,
    tmap: Optional[CoverageMap],
    all_tests: Optional[List[str]] = None,
) -> Selection:
    sel = Selection(tests=[], run_all=False)

    # ---- fail-open gates ------------------------------------------------

    if tmap is None:
        sel.run_all = True
        sel.reasons.append("no usable map found for any ancestor commit")
        return sel

    if changes.is_empty():
        sel.reasons.append("no changes since mapped commit")
        return sel

    for path in changes.all_paths:
        if _matches(path, ALWAYS_RUN_ALL):
            sel.run_all = True
            sel.reasons.append(f"{path} can affect any test (always-run rule)")
            return sel

    for path in changes.all_paths:
        if not _analysable(path):
            sel.run_all = True
            sel.reasons.append(f"{path} is not a file this adapter understands")
            return sel

    # A new source file has no history in the map, and without static import
    # analysis we cannot know who depends on it. v0 fails open here; v1 closes
    # this with an import graph.
    for path in changes.added:
        if _analysable(path) and not is_test_file(path):
            sel.run_all = True
            sel.reasons.append(f"{path} is new; no coverage history to select from")
            return sel

    for path in changes.deleted + changes.renamed:
        if _analysable(path):
            sel.run_all = True
            sel.reasons.append(f"{path} was deleted or renamed")
            return sel

    # ---- per-file selection ---------------------------------------------

    picked: Set[str] = set()

    for path, lines in changes.modified.items():
        if is_test_file(path):
            # The test file itself changed: run every test in it. Matching by
            # node-id prefix is safe because pytest ids start with the path.
            for test in tmap.tests:
                if test.split("::")[0] == path:
                    picked.add(test)
                    sel.add(test, f"its own test file {path} changed")
            continue

        if not tmap.covers_file(path):
            sel.run_all = True
            sel.reasons.append(f"{path} is not in the map; cannot rule any test out")
            return sel

        for lineno in sorted(lines):
            # Module-level code (imports, decorators, class bodies) runs at
            # import time and belongs to no single test. A change there can
            # affect every test that imports the module, so fail open.
            if tmap.is_module_level(path, lineno):
                sel.run_all = True
                sel.reasons.append(
                    f"{path}:{lineno} runs at import time, not inside a test"
                )
                return sel

            for test in tmap.tests_for(path, lineno):
                picked.add(test)
                sel.add(test, f"covers {path}:{lineno}")

    # New test files were excluded from the fail-open gate above, so add them.
    for path in changes.added:
        if is_test_file(path):
            sel.reasons.append(f"{path} is a new test file")
            if all_tests:
                for test in all_tests:
                    if test.split("::")[0] == path:
                        picked.add(test)
                        sel.add(test, "new test file")

    sel.tests = sorted(picked)
    return sel
