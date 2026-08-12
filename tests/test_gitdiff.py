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
