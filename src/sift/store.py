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

from .gitdiff import ancestors
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
    order = {sha: i for i, sha in enumerate(ancestors(limit=max_walk, cwd=str(root)))}
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
    """Never let a map get committed."""
    gitignore = root / ".gitignore"
    line = f"{SIFT_DIR}/"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if line not in existing.split():
        with gitignore.open("a") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"{line}\n")
