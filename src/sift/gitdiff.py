"""Git diff parsing.

THE IMPORTANT DETAIL
--------------------
The map was built at commit `base`. Its line numbers therefore refer to the
files *as they were at base*. So when we diff base..HEAD we must look up the
**old side** of each hunk (the `-a,b` part), not the new side. Using new-side
line numbers against an old map silently reads the wrong lines and produces
confidently wrong selections.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Optional[str] = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def head(cwd: Optional[str] = None) -> str:
    return _git("rev-parse", "HEAD", cwd=cwd).strip()


def ancestors(limit: int = 200, cwd: Optional[str] = None) -> List[str]:
    """Commits from HEAD backwards, newest first."""
    out = _git("rev-list", f"--max-count={limit}", "HEAD", cwd=cwd)
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_dirty(cwd: Optional[str] = None) -> bool:
    return bool(_git("status", "--porcelain", cwd=cwd).strip())


def show(commit: str, path: str, cwd: Optional[str] = None) -> Optional[str]:
    """A file's content at a commit, or None if it can't be read.

    None covers everything that can go wrong -- binary files, a path that
    didn't exist at that commit, encoding failures -- because every caller of
    this treats "can't read it" as "don't know" and falls back to the safe
    default, never as an error to propagate.
    """
    try:
        proc = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=cwd, capture_output=True, text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_shallow(cwd: Optional[str] = None) -> bool:
    """True if this clone has a truncated history.

    Matters more than it looks. CI checkouts are shallow by default
    (`actions/checkout` uses fetch-depth: 1), which leaves `rev-list HEAD`
    returning a single commit. The nearest-ancestor walk then never matches a
    restored map, sift fails open, and the build stays green while the cache
    silently does nothing at all. Callers should say so out loud.
    """
    return _git("rev-parse", "--is-shallow-repository", cwd=cwd).strip() == "true"


# Build artefacts and caches: derived from source we already track, so they
# cannot independently affect a test outcome. Everything else that is
# untracked is treated as a new file and fails open.
UNTRACKED_NOISE = (
    "*.pyc", "*.pyo", "__pycache__/*", "*/__pycache__/*",
    ".sift/*", "*.egg-info/*", ".DS_Store",
)


def untracked(cwd: Optional[str] = None) -> List[str]:
    """New files git has not been told about yet.

    `git diff` does not report these at all. Without this, creating a new
    module or a new requirements.txt would be completely invisible to
    selection -- a silent miss.
    """
    import fnmatch

    out = _git("ls-files", "--others", "--exclude-standard", cwd=cwd)
    paths = []
    for line in out.splitlines():
        path = line.strip()
        if not path:
            continue
        if any(fnmatch.fnmatch(path, pat) for pat in UNTRACKED_NOISE):
            continue
        paths.append(path)
    return paths


@dataclass
class Changes:
    """What changed between the base commit and the working tree."""

    # path (as at base) -> old-side line numbers touched
    modified: Dict[str, Set[int]] = field(default_factory=dict)
    added: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    renamed: List[str] = field(default_factory=list)
    # path -> {old_lineno: new_lineno}, populated ONLY for hunks that replace
    # exactly one line with exactly one line. That shape is unambiguous: the
    # old line became the new line, with nothing inserted or removed around
    # it. Any messier hunk (multi-line signatures, lines added/removed nearby)
    # is deliberately left out here rather than guessed at -- callers that
    # need certainty about old<->new correspondence use this map and treat
    # its absence as "don't know", not "assume line numbers still match".
    line_pairs: Dict[str, Dict[int, int]] = field(default_factory=dict)

    @property
    def all_paths(self) -> List[str]:
        return sorted(
            set(self.modified) | set(self.added) | set(self.deleted) | set(self.renamed)
        )

    def is_empty(self) -> bool:
        return not self.all_paths


def _name_status(base: str, cwd: Optional[str] = None) -> List[tuple]:
    out = _git("diff", "--name-status", "-M", base, cwd=cwd)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        rows.append(tuple(parts))
    return rows


def collect(base: str, cwd: Optional[str] = None) -> Changes:
    """Diff base..working-tree, returning old-side line numbers for edits."""
    changes = Changes()

    for row in _name_status(base, cwd=cwd):
        status, paths = row[0], row[1:]
        code = status[0]
        if code == "A":
            changes.added.append(paths[0])
        elif code == "D":
            changes.deleted.append(paths[0])
        elif code == "R":
            # old path matters for map lookup, new path for reporting
            changes.renamed.append(paths[0])
            if len(paths) > 1:
                changes.renamed.append(paths[1])
        # M / C / T fall through to hunk parsing below

    # -U0 gives exact hunk boundaries with no context lines, so we don't
    # over-select from unchanged neighbours.
    out = _git("diff", "--unified=0", "--no-color", "-M", base, cwd=cwd)

    current: Optional[str] = None
    for line in out.splitlines():
        if line.startswith("--- "):
            raw = line[4:].strip()
            current = None if raw == "/dev/null" else raw[2:] if raw.startswith("a/") else raw
            continue
        if line.startswith("+++ "):
            continue
        m = HUNK.match(line)
        if not m or current is None:
            continue

        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        new_start = int(m.group(3))
        new_count = int(m.group(4)) if m.group(4) is not None else 1

        touched = changes.modified.setdefault(current, set())
        if old_count == 1 and new_count == 1:
            changes.line_pairs.setdefault(current, {})[old_start] = new_start
        if old_count == 0:
            # Pure insertion: there is no old line to look up. The insertion
            # point sits between old_start and old_start+1, so we treat both
            # as touched. This is a heuristic -- it is deliberately generous,
            # because under-selecting is the failure mode that loses trust.
            touched.add(old_start)
            touched.add(old_start + 1)
        else:
            touched.update(range(old_start, old_start + old_count))

    # Untracked files are invisible to `git diff`, so fold them in as added.
    for path in untracked(cwd=cwd):
        if path not in changes.added:
            changes.added.append(path)

    # Files recorded only as added have no old-side content to map.
    for path in changes.added:
        changes.modified.pop(path, None)

    return changes
