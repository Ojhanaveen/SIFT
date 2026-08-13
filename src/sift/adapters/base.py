"""The adapter contract.

This is the contributor surface, and it is deliberately tiny: a stranger should
be able to add a language in an afternoon. Everything language-specific lives
behind it; the core knows about git, lines, and safety rules, and nothing else.

An adapter is two things:

* a **runner** -- detect the framework, discover tests, trace them into a map,
  and run a subset (the methods below);
* a **profile** -- the handful of file-level facts the core needs in order to
  classify a diff before any adapter is invoked.

The profile exists because selection has to reason about paths it may have no
map for. Which extensions are source? Which paths are test files? Which files
are so foundational that any change to them means "run everything"? Without
the profile the core would need `path.endswith(".py")` baked into it, which is
exactly the thing that makes a second language impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:  # pragma: no cover - Protocol lives elsewhere before 3.8
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore

from ..model import CoverageMap, TestId


@dataclass(frozen=True)
class LanguageProfile:
    """What the core needs to know about one ecosystem's files.

    Every field widens behaviour or leaves it unchanged; none of them can make
    selection narrower on its own. `always_run` forces full runs, and a path
    that matches no `source_extensions` is treated as unanalysable, which also
    forces a full run. Adding a language therefore cannot introduce a silent
    miss in another one.
    """

    # Files this adapter can reason about line-by-line. Anything else fails open.
    source_extensions: Tuple[str, ...] = ()
    # Paths that are themselves tests.
    test_file_patterns: Tuple[str, ...] = ()
    # Ecosystem files whose change can affect any test at all: dependency
    # manifests and lockfiles, runner config, test-harness bootstrap.
    always_run_patterns: Tuple[str, ...] = ()


@runtime_checkable
class Adapter(Protocol):
    """What a language adapter must implement.

    `pytest_adapter.PytestAdapter` is the worked example, at ~150 lines.
    """

    name: str
    profile: LanguageProfile

    def detect(self) -> bool:
        """Does this repo use my framework?"""

    def discover(self) -> List[TestId]:
        """Every test id in the repo."""

    def trace(self, commit: str) -> CoverageMap:
        """Run the full suite under per-test coverage and return the map.

        Test ids must be re-runnable: whatever is recorded here gets handed
        straight back to `run()`. Lines that execute at import time belong to
        no test and must go in `map.module_level`, which forces a full run --
        missing that is the subtlest silent-miss bug in the design. A failing
        suite still yields a valid map; do not discard it.
        """

    def run(self, tests: Optional[Sequence[TestId]] = None) -> int:
        """Run these test ids (None means all). Return the process exit code."""

    def run_capture(
        self, tests: Optional[Sequence[TestId]] = None
    ) -> Tuple[int, List[TestId]]:
        """Same as run(), but also return the ids that failed.

        Shadow mode needs the failure list to answer the only question that
        matters: would sift have skipped a test that actually failed?
        """
