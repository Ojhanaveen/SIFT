"""Map persistence and the nearest-ancestor walk.

Maps are keyed by the commit they were built at. On a later run we walk back
through HEAD's ancestry to find the newest commit we hold a map for, and diff
from there. Diffing from HEAD~1 instead would silently miss every change made
between the map's commit and the previous one.

Maps live in .sift/ and must NOT be committed -- they would conflict on every
merge. In CI they belong in the runner's cache: cache the whole .sift/maps
directory, and this module's ancestor walk does the rest.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .gitdiff import GitError, ancestors
from .model import CoverageMap

SIFT_DIR = ".sift"
MAPS_DIR = "maps"

# How many maps to keep on disk. Every `--all` writes one, and in CI the whole
# directory is cached, so without a cap the cache grows forever. Keeping a few
# means a run whose immediate ancestor map was pruned can still match an older
# one further back instead of falling all the way through to a full run.
KEEP_MAPS = 5


def maps_path(root: Path) -> Path:
    return root / SIFT_DIR / MAPS_DIR


def path_for(root: Path, commit: str) -> Path:
    return maps_path(root) / f"{commit}.json"


def save(root: Path, tmap: CoverageMap) -> Path:
    dest = path_for(root, tmap.commit)
    tmap.write(dest)
    _ensure_ignored(root)
    prune(root)
    return dest


def prune(root: Path, keep: int = KEEP_MAPS, max_walk: int = 200) -> List[str]:
    """Drop all but the `keep` most recent maps. Returns the commits removed.

    Ordering is by position in HEAD's ancestry, newest first. Maps for commits
    that are not ancestors of HEAD -- left behind by another branch, or by a
    branch that has since been rebased away -- sort last and go first.

    Pruning is always safe: the worst a missing map can do is send a later run
    through the fail-open path and rebuild, which costs time, never accuracy.
    Never prune toward a *smaller* selection.
    """
    try:
        walk = ancestors(limit=max_walk, cwd=str(root))
    except GitError:
        # No usable git history to rank by. Keeping every map costs disk and
        # nothing else, whereas guessing an order could delete the one map a
        # later run needed. Pruning is an optimisation; never let it fail a run.
        return []

    order = {sha: i for i, sha in enumerate(walk)}
    ranked = sorted(stored_commits(root), key=lambda sha: order.get(sha, max_walk + 1))

    removed = []
    for sha in ranked[keep:]:
        try:
            path_for(root, sha).unlink()
        except OSError:
            continue
        removed.append(sha)
    return removed


def stored_commits(root: Path) -> List[str]:
    d = maps_path(root)
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.json")]


def find_nearest(root: Path, max_walk: int = 200) -> Tuple[Optional[CoverageMap], int]:
    """Newest ancestor of HEAD that we hold a map for.

    Returns (map, distance_in_commits). distance 0 means the map matches HEAD.
    (None, -1) means no usable map -- caller must run everything.
    """
    have = set(stored_commits(root))
    if not have:
        return None, -1

    for distance, sha in enumerate(ancestors(limit=max_walk, cwd=str(root))):
        if sha in have:
            try:
                return CoverageMap.read(path_for(root, sha)), distance
            except (ValueError, OSError):
                continue  # corrupt or wrong-version map: keep walking
    return None, -1


def _ensure_ignored(root: Path) -> None:
    """Never let a map get committed -- without touching a tracked file.

    This writes to .git/info/exclude, not .gitignore. .gitignore is committed,
    so appending to it puts a modification in the user's diff that they never
    asked for, dirties the working tree, and can block a checkout outright.
    sift runs inside other people's repositories; it has no business editing
    their files to make its own life easier.

    .git/info/exclude does the same job, is local to the clone, and is
    invisible to git status. If it cannot be located -- an unusual layout, or
    a permission problem -- we say nothing and move on: the cost is a map that
    could be committed by an unlucky `git add -A`, which is recoverable, while
    corrupting someone's ignore rules is not.
    """
    exclude = _exclude_file(root)
    if exclude is None:
        return

    line = f"/{SIFT_DIR}/"
    try:
        existing = exclude.read_text() if exclude.exists() else ""
        if line in existing.split():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"{line}\n")
    except OSError:
        return


def _exclude_file(root: Path) -> Optional[Path]:
    """Locate .git/info/exclude, coping with worktrees and submodules.

    In a linked worktree `.git` is a file containing `gitdir: <path>`, and the
    exclude file lives under the main repository's common directory.
    """
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git / "info" / "exclude"
    if dot_git.is_file():
        try:
            content = dot_git.read_text().strip()
        except OSError:
            return None
        if not content.startswith("gitdir:"):
            return None
        target = Path(content.split(":", 1)[1].strip())
        if not target.is_absolute():
            target = (root / target).resolve()
        # A worktree's gitdir is <common>/worktrees/<name>; excludes are shared
        # from the common dir.
        if target.parent.name == "worktrees":
            target = target.parent.parent
        return target / "info" / "exclude"
    return None
