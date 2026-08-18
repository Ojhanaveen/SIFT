"""Diff parsing tests against real git repositories.

The critical property: hunks are reported using OLD-side line numbers, because
the map they will be looked up against was built at the base commit.
"""

import subprocess

import pytest

from sift.gitdiff import collect, head


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", ".")
    (tmp_path / "a.py").write_text("l1\nl2\nl3\nl4\nl5\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_modified_line_uses_old_numbering(repo):
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nCHANGED\nl3\nl4\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.modified["a.py"] == {2}


def test_insertion_flags_neighbouring_old_lines(repo):
    """A pure insertion has no old line of its own; we deliberately over-select
    the lines either side rather than risk selecting nothing."""
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nl2\nNEW\nl3\nl4\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.modified["a.py"] == {2, 3}


def test_offsets_do_not_shift_later_hunks(repo):
    """Two edits: the second must still be reported in OLD coordinates.

    If we used new-side numbers, the earlier insertion would shift this and we
    would look up the wrong line in the map.
    """
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nNEW\nNEW2\nl2\nl3\nl4\nCHANGED\n")
    changes = collect(base, cwd=str(repo))
    assert 5 in changes.modified["a.py"]


def test_untracked_file_is_detected(repo):
    """git diff ignores untracked files entirely; sift must not."""
    base = head(str(repo))
    (repo / "brand_new.py").write_text("x = 1\n")
    changes = collect(base, cwd=str(repo))
    assert "brand_new.py" in changes.added


def test_pycache_noise_is_ignored(repo):
    base = head(str(repo))
    cache = repo / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-312.pyc").write_bytes(b"\x00")
    changes = collect(base, cwd=str(repo))
    assert not any("pycache" in p for p in changes.all_paths)


def test_deleted_file_recorded(repo):
    base = head(str(repo))
    (repo / "a.py").unlink()
    changes = collect(base, cwd=str(repo))
    assert "a.py" in changes.deleted


def test_no_changes(repo):
    base = head(str(repo))
    changes = collect(base, cwd=str(repo))
    assert changes.is_empty()


# -- line_pairs: the exact-correspondence signal for annotate.py -----------


def test_a_single_line_replacement_records_the_line_pair(repo):
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nCHANGED\nl3\nl4\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.line_pairs["a.py"] == {2: 2}


def test_a_line_replacement_at_a_different_offset_maps_correctly(repo):
    """Insert lines well above the edit (a separate hunk) so old line 4
    becomes new line 6 -- the pair must reflect the shift, not assume line
    numbers are stable across the whole file."""
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nEXTRA-A\nEXTRA-B\nl2\nl3\nCHANGED\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.line_pairs["a.py"] == {4: 6}


def test_a_pure_insertion_records_no_line_pair(repo):
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nl2\nINSERTED\nl3\nl4\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.line_pairs.get("a.py", {}) == {}


def test_a_multi_line_replacement_records_no_line_pair(repo):
    """Two-for-two (or any shape other than 1-for-1) is ambiguous about which
    old line became which new line -- must not guess."""
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nCHANGED-A\nCHANGED-B\nl4\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.line_pairs.get("a.py", {}) == {}


def test_a_pure_deletion_records_no_line_pair(repo):
    base = head(str(repo))
    (repo / "a.py").write_text("l1\nl3\nl4\nl5\n")
    changes = collect(base, cwd=str(repo))
    assert changes.line_pairs.get("a.py", {}) == {}


# -- show(): reading a file's content at a commit ---------------------------


def test_show_reads_a_file_at_a_commit(repo):
    from sift.gitdiff import show
    base = head(str(repo))
    (repo / "a.py").write_text("changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change it")

    assert show(base, "a.py", cwd=str(repo)) == "l1\nl2\nl3\nl4\nl5\n"


def test_show_returns_none_for_a_path_absent_at_that_commit(repo):
    from sift.gitdiff import show
    base = head(str(repo))
    assert show(base, "never-existed.py", cwd=str(repo)) is None


def test_show_returns_none_for_an_unknown_commit(repo):
    from sift.gitdiff import show
    assert show("0" * 40, "a.py", cwd=str(repo)) is None
