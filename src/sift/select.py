"""Selection engine.

Governing principle: being wrong is fatal, being slow is survivable. Every
ambiguity below resolves toward running MORE tests. If you are ever tempted to
make a rule narrower to squeeze out more speed, don't.
"""

from __future__ import annotations

import fnmatch
from typing import Callable, Iterable, List, Optional, Set, Tuple

from . import adapters, annotate
from .gitdiff import Changes
from .model import Selection, CoverageMap

# path -> file content, or None if unreadable. Two of these (old-side,
# new-side) let select() consult annotate.exempt_body_range() without
# knowing anything about git itself -- cli.py supplies the real
# implementation; tests supply canned content.
SourceReader = Callable[[str], Optional[str]]

# A change to any of these can affect anything, in ways coverage cannot see.
# Only genuinely language-agnostic entries belong here -- the environment the
# suite runs in. Ecosystem files (dependency manifests, lockfiles, runner
# config) live on each adapter's LanguageProfile, so that adding a language
# also teaches the core which of its files are foundational.
ALWAYS_RUN_ALL = [
    "Dockerfile*",
    "docker-compose*.yml",
    ".github/**",
    ".gitlab-ci.yml",
    "Makefile",
]

# Paths that sit under an always-run glob but are still provably inert.
#
# `.github/**` is deliberately broad -- it catches workflow and action changes,
# which can alter how every test runs. But it also swallows issue templates and
# funding metadata, which no test runner reads. Those were already listed in
# BENIGN_ALWAYS below and it made no difference: always-run is evaluated first,
# so the entries were unreachable and an issue-template edit ran the full suite.
#
# This stays an explicit short list rather than "anything benign beats
# always-run", so that a future careless addition to BENIGN_ALWAYS cannot
# silently disable a safety rule. Each entry needs the same argument as
# BENIGN_ALWAYS: no test can observe this change. (pytest's default
# norecursedirs skips dotted directories, so even --doctest-glob='*.md' does
# not collect files under .github/.)
ALWAYS_RUN_EXCEPTIONS = [
    ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE*",
    ".github/FUNDING.yml",
    ".github/CODEOWNERS",
]


def _always_run_patterns() -> List[str]:
    patterns = list(ALWAYS_RUN_ALL)
    for profile in adapters.profiles():
        patterns.extend(profile.always_run_patterns)
    return patterns


def _test_file_patterns() -> List[str]:
    patterns: List[str] = []
    for profile in adapters.profiles():
        patterns.extend(profile.test_file_patterns)
    return patterns


def _source_extensions() -> Tuple[str, ...]:
    exts: List[str] = []
    for profile in adapters.profiles():
        exts.extend(profile.source_extensions)
    return tuple(exts)

# Files that cannot change a test outcome under any plausible configuration.
# A test runner will not execute them, and code will not read them at runtime.
#
# The bar for adding to this list: you must be able to argue that NO test can
# observe the change. "Probably fine" is not good enough -- everything here
# makes selection narrower, which is the direction that causes silent misses.
# Note in particular that data-ish extensions (.txt, .json, .yaml, .csv) are
# deliberately absent: tests read those as fixtures all the time.
BENIGN_ALWAYS = [
    # images
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico", "*.webp", "*.bmp",
    # repo furniture
    ".gitignore", ".gitattributes", ".editorconfig",
    "LICENSE*", "COPYING*", "NOTICE*", "AUTHORS*",
    ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE*",
    ".github/FUNDING.yml",
    ".github/CODEOWNERS",
]

# Documentation. Benign ONLY when the project does not run doctests -- with
# --doctest-glob a markdown file is executable code and a change to it can
# genuinely break a test.
BENIGN_DOCS = [
    "*.md", "*.rst", "*.adoc",
    "docs/*", "doc/*", "*/docs/*",
    "CHANGELOG*", "CONTRIBUTING*", "CODE_OF_CONDUCT*", "SECURITY*",
]


def _matches(path: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch("/" + path, "*/" + pat):
            return True
    return False


def is_test_file(path: str) -> bool:
    return _matches(path, _test_file_patterns())


def _analysable(path: str) -> bool:
    """Can any registered adapter reason about this file line-by-line?

    Anything else fails open. Note that answering True is not a promise the
    file is understood -- a path with a known extension but no entry in the
    map still forces a full run further down. Both answers are safe; this one
    just decides which safety net catches it.
    """
    return path.endswith(_source_extensions())


def is_benign(path: str, ignore_docs: bool = True) -> bool:
    """Provably incapable of affecting a test outcome."""
    if _matches(path, BENIGN_ALWAYS):
        return True
    return ignore_docs and _matches(path, BENIGN_DOCS)


def _strip_benign(changes: Changes, ignore_docs: bool) -> Tuple[Changes, List[str]]:
    """Drop benign paths so they don't trip the fail-open gates."""
    kept = Changes()
    kept.line_pairs = changes.line_pairs  # carried through, not rebuilt
    dropped: List[str] = []

    for path, lines in changes.modified.items():
        if is_benign(path, ignore_docs):
            dropped.append(path)
        else:
            kept.modified[path] = lines

    for attr in ("added", "deleted", "renamed"):
        for path in getattr(changes, attr):
            if is_benign(path, ignore_docs):
                dropped.append(path)
            else:
                getattr(kept, attr).append(path)

    return kept, dropped


def _annotation_exemption(
    path: str,
    lineno: int,
    changes: Changes,
    old_content: Optional[SourceReader],
    new_content: Optional[SourceReader],
    src: Callable[[str, str], Optional[str]],
) -> Optional[Tuple[int, int]]:
    """Old-side body range to select from instead of failing open, or None.

    Requires exact certainty about which new-side line corresponds to this
    old-side line -- only present for hunks that replace one line with one
    line (see Changes.line_pairs). Any other shape means "don't know" and
    this returns None, which is the same as not having the exemption at all.
    """
    if old_content is None or new_content is None:
        return None
    new_lineno = changes.line_pairs.get(path, {}).get(lineno)
    if new_lineno is None:
        return None
    old_src = src(path, "old")
    new_src = src(path, "new")
    if old_src is None or new_src is None:
        return None
    return annotate.exempt_body_range(old_src, new_src, lineno, new_lineno)


def select(
    changes: Changes,
    tmap: Optional[CoverageMap],
    all_tests: Optional[List[str]] = None,
    ignore_docs: bool = True,
    old_content: Optional[SourceReader] = None,
    new_content: Optional[SourceReader] = None,
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

    # Always-run rules are checked against the ORIGINAL change set, before
    # anything is filtered out, so a benign pattern can never mask one.
    always_run = _always_run_patterns()
    for path in changes.all_paths:
        if _matches(path, ALWAYS_RUN_EXCEPTIONS):
            continue
        if _matches(path, always_run):
            sel.run_all = True
            sel.reasons.append(f"{path} can affect any test (always-run rule)")
            return sel

    changes, benign = _strip_benign(changes, ignore_docs)
    if benign:
        sel.ignored = sorted(benign)
    if changes.is_empty():
        sel.reasons.append(
            f"only non-code files changed ({len(benign)}): nothing to run"
        )
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
    # Lazily fetched and cached per path -- only paid for files that actually
    # hit a module-level line, and only once per file even if several of its
    # lines do.
    src_cache: dict = {}

    def _src(path: str, side: str) -> Optional[str]:
        key = (path, side)
        if key not in src_cache:
            reader = old_content if side == "old" else new_content
            src_cache[key] = reader(path) if reader else None
        return src_cache[key]

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
            # affect every test that imports the module, so fail open --
            # UNLESS this exact edit qualifies for the one narrow exemption
            # in annotate.py: a single-line, undecorated function signature
            # that changed only its type annotation. See annotate.py for the
            # full safety argument and its stated residual risk.
            if tmap.is_module_level(path, lineno):
                body_range = _annotation_exemption(
                    path, lineno, changes, old_content, new_content, _src
                )
                if body_range is None:
                    sel.run_all = True
                    sel.reasons.append(
                        f"{path}:{lineno} runs at import time, not inside a test"
                    )
                    return sel
                start, end = body_range
                for body_line in range(start, end + 1):
                    for test in tmap.tests_for(path, body_line):
                        picked.add(test)
                        sel.add(
                            test,
                            f"{path}:{lineno} signature changed only its type "
                            f"annotation; selected via its body",
                        )
                continue

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
