"""select() wired to the annotation exemption end to end.

test_annotate.py pins the exemption logic itself in isolation; these tests
check the wiring -- that select() only takes the exemption when it has
everything it needs (line_pairs, both readers, a real body range), and falls
back to the ordinary fail-open behaviour the instant any of that is missing.
That fallback is the default the project cannot regress: no readers supplied
at all is the common case (most callers, and every existing test), and it must
behave exactly as before this feature existed.
"""

import pytest

from sift.gitdiff import Changes
from sift.model import CoverageMap
from sift.select import select

PREFIX = "from __future__ import annotations\n\n\n"


def src(*lines: str) -> str:
    return PREFIX + "\n".join(lines) + "\n"


OLD_SRC = src(
    "def add(a: int, b: int) -> int:",   # line 4
    "    return a + b",                  # line 5
)
NEW_SRC = src(
    "def add(a: float, b: float) -> float:",
    "    return a + b",
)


@pytest.fixture
def tmap():
    m = CoverageMap(commit="abc123", adapter="pytest")
    m.tests = ["tests/test_math.py::test_add", "tests/test_math.py::test_other"]
    # line 5 is the body; both tests happen to cover it here.
    m.lines = {"app/math.py": {5: [0, 1]}}
    m.module_level = {"app/math.py": {4}}
    return m


def _changes_with_pair(path, old_lineno, new_lineno):
    c = Changes()
    c.modified = {path: {old_lineno}}
    c.line_pairs = {path: {old_lineno: new_lineno}}
    return c


def _readers(old=OLD_SRC, new=NEW_SRC):
    return (lambda path: old), (lambda path: new)


# -- the exemption actually narrows selection through select() --------------


def test_annotation_only_signature_change_selects_body_tests(tmap):
    changes = _changes_with_pair("app/math.py", 4, 4)
    old_content, new_content = _readers()

    sel = select(changes, tmap, old_content=old_content, new_content=new_content)

    assert not sel.run_all
    assert set(sel.tests) == set(tmap.tests)
    assert any("signature changed only its type annotation" in r
              for reasons in sel.why.values() for r in reasons)


def test_a_real_behavioural_change_on_the_signature_line_still_fails_open(tmap):
    """Same wiring, but the new line adds a parameter -- exempt_body_range()
    must say no, and select() must fail open exactly as it always did."""
    changed_new_src = src(
        "def add(a: float, b: float, c: float = 0) -> float:",
        "    return a + b + c",
    )
    changes = _changes_with_pair("app/math.py", 4, 4)
    old_content, new_content = _readers(new=changed_new_src)

    sel = select(changes, tmap, old_content=old_content, new_content=new_content)

    assert sel.run_all
    assert any("runs at import time" in r for r in sel.reasons)


# -- the fallback: missing any ingredient must not silently narrow ----------


def test_no_readers_supplied_falls_back_to_ordinary_fail_open(tmap):
    """The default. Every caller before this feature existed, and most calls
    to select() today, pass no readers at all -- behaviour must be byte-for-
    byte the old fail-open path."""
    changes = _changes_with_pair("app/math.py", 4, 4)
    sel = select(changes, tmap)  # no old_content / new_content
    assert sel.run_all
    assert any("runs at import time" in r for r in sel.reasons)


def test_only_one_reader_supplied_falls_back(tmap):
    changes = _changes_with_pair("app/math.py", 4, 4)
    old_content, _ = _readers()
    sel = select(changes, tmap, old_content=old_content, new_content=None)
    assert sel.run_all


def test_missing_line_pair_falls_back(tmap):
    """No line_pairs entry means select() cannot be certain which new-side
    line corresponds to the old one -- e.g. a multi-line hunk. Must not guess
    that the line numbers still match."""
    c = Changes()
    c.modified = {"app/math.py": {4}}
    c.line_pairs = {}  # nothing recorded for this path/line
    old_content, new_content = _readers()

    sel = select(c, tmap, old_content=old_content, new_content=new_content)
    assert sel.run_all


def test_a_reader_returning_none_falls_back(tmap):
    """A file that can't be read (binary, deleted at that commit, whatever)
    must fail open, not silently skip the exemption check in a way that could
    later be mistaken for 'checked and safe'."""
    changes = _changes_with_pair("app/math.py", 4, 4)
    sel = select(changes, tmap, old_content=lambda p: None,
                new_content=lambda p: NEW_SRC)
    assert sel.run_all


def test_readers_are_not_called_when_nothing_is_module_level(tmap):
    """Cost discipline: paths that never hit a module-level line should never
    trigger a git show / file read at all."""
    calls = []

    def tracking_reader(path):
        calls.append(path)
        return OLD_SRC

    changes = Changes()
    changes.modified = {"app/math.py": {5}}  # line 5 is a body line, not module-level

    select(changes, tmap, old_content=tracking_reader, new_content=tracking_reader)
    assert calls == []
