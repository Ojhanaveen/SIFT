"""Map persistence, pruning, and the shallow-clone trap.

Pruning exists because in CI the whole .sift/maps directory is cached, so an
unbounded pile of maps becomes an unbounded cache. The property that matters is
that pruning can only ever cost time -- it must never leave a run selecting a
*smaller* set of tests than it would have before.
"""

import subprocess

import pytest

from sift import store
from sift.gitdiff import head, is_shallow
from sift.model import CoverageMap


def git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


def commit(repo, text):
    (repo / "a.py").write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", text)
    return head(str(repo))


def a_map(sha):
    return CoverageMap(commit=sha, adapter="pytest", tests=["t::one"],
                       lines={"a.py": {1: [0]}})


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main", ".")
    commit(tmp_path, "one")
    return tmp_path


# -- pruning ---------------------------------------------------------------


def test_save_keeps_only_the_most_recent_maps(repo):
    shas = [head(str(repo))]
    for i in range(store.KEEP_MAPS + 3):
        shas.append(commit(repo, f"line{i}"))
    for sha in shas:
        store.save(repo, a_map(sha))

    kept = set(store.stored_commits(repo))
    assert len(kept) == store.KEEP_MAPS
    # The survivors are the newest ones -- i.e. closest to HEAD.
    assert kept == set(shas[-store.KEEP_MAPS:])


def test_prune_drops_commits_outside_head_ancestry_first(repo):
    base = head(str(repo))
    git(repo, "checkout", "-q", "-b", "side")
    orphan = commit(repo, "side work")
    git(repo, "checkout", "-q", "main")
    on_main = commit(repo, "main work")

    for sha in (base, orphan, on_main):
        store.save(repo, a_map(sha))

    store.prune(repo, keep=2)
    kept = set(store.stored_commits(repo))
    assert orphan not in kept, "a map off HEAD's ancestry is useless here"
    assert kept == {base, on_main}


def test_pruning_never_shrinks_a_selection(repo):
    """The safety property. After pruning, find_nearest either returns the same
    map or none at all -- it must never return a map *newer* than the best one,
    which would diff a shorter range and select fewer tests."""
    base = head(str(repo))
    store.save(repo, a_map(base))
    newer = commit(repo, "two")

    before, _ = store.find_nearest(repo)
    store.prune(repo, keep=1)
    after, _ = store.find_nearest(repo)

    assert before is not None and before.commit == base
    assert after is None or after.commit == base
    assert newer not in store.stored_commits(repo)


def test_prune_on_empty_store_is_a_no_op(repo):
    assert store.prune(repo) == []


# -- the shallow-clone trap ------------------------------------------------


def test_full_clone_is_not_shallow(repo):
    assert is_shallow(str(repo)) is False


def test_shallow_clone_cannot_reach_an_older_map(repo, tmp_path):
    """The exact CI failure this work exists to prevent: the map is present in
    the cache, but a fetch-depth:1 checkout truncates history so the ancestor
    walk never reaches it. sift fails open and the build stays green while the
    cache silently buys nothing."""
    base = head(str(repo))
    for i in range(3):
        commit(repo, f"later{i}")

    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)],
        check=True, capture_output=True,
    )
    assert is_shallow(str(shallow)) is True

    # The cache restores the map for `base`, which is a real ancestor of HEAD.
    store.save(shallow, a_map(base))
    found, distance = store.find_nearest(shallow)

    assert found is None and distance == -1
    assert store.stored_commits(shallow) == [base], "the map is right there"
