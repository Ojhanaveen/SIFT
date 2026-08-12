"""sift CLI."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

from . import store
from .adapters.pytest_adapter import PytestAdapter
from .config import doctests_enabled
from .gitdiff import GitError, collect, head, is_dirty, is_shallow
from .model import Selection
from .select import select

DIM, BOLD, GREEN, YELLOW, RED, RESET = (
    "\033[2m", "\033[1m", "\033[32m", "\033[33m", "\033[31m", "\033[0m"
)


def _root() -> Path:
    return Path.cwd()


def _adapter(root: Path, extra: List[str]) -> PytestAdapter:
    return PytestAdapter(root, extra)


def _fmt_secs(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m}m {sec:02d}s" if m else f"{sec}s"


def _warn_if_shallow(root: Path) -> None:
    """A shallow clone defeats the ancestor walk without failing anything.

    This is the default state of a CI checkout, so it is the most likely reason
    for "sift is cached but still runs everything". Say it loudly -- a silent
    green build that never selects anything is worse than an error.
    """
    if not is_shallow(str(root)):
        return
    print(f"{YELLOW}⚠  shallow clone: history is truncated, so sift cannot walk "
          f"back to a stored map.{RESET}")
    print(f"   {DIM}Every run will fall back to the full suite. In GitHub "
          f"Actions set:{RESET}")
    print(f"   {DIM}  - uses: actions/checkout@v4{RESET}")
    print(f"   {DIM}    with: {{ fetch-depth: 0 }}{RESET}")


def _print_selection(sel: Selection, total: Optional[int]) -> None:
    if sel.ignored:
        shown = ", ".join(sel.ignored[:3])
        more = f" +{len(sel.ignored) - 3} more" if len(sel.ignored) > 3 else ""
        print(f"{DIM}ignored {len(sel.ignored)} non-code file(s): {shown}{more}{RESET}")
    if sel.run_all:
        print(f"{YELLOW}▸ running everything{RESET}")
        for r in sel.reasons[:5]:
            print(f"  {DIM}· {r}{RESET}")
        return
    n = len(sel.tests)
    if total:
        pct = 100.0 * n / total if total else 100.0
        print(f"{GREEN}▸ selected {BOLD}{n}{RESET}{GREEN} of {total} tests "
              f"({pct:.1f}%){RESET}")
    else:
        print(f"{GREEN}▸ selected {BOLD}{n}{RESET}{GREEN} tests{RESET}")


# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    root = _root()
    adapter = _adapter(root, args.pytest_args)
    sha = head(str(root))
    _warn_if_shallow(root)

    if args.all:
        print(f"{DIM}building map at {sha[:8]}…{RESET}")
        started = time.time()
        tmap = adapter.trace(sha)
        dest = store.save(root, tmap)
        print(f"{GREEN}▸ mapped {len(tmap.tests)} tests across "
              f"{len(tmap.lines)} files in {_fmt_secs(time.time() - started)}{RESET}")
        print(f"  {DIM}{dest.relative_to(root)}{RESET}")
        return 0

    tmap, distance = store.find_nearest(root)
    if tmap is None:
        print(f"{YELLOW}▸ no map found — run `sift run --all` first{RESET}")
        return adapter.run(None)

    if is_dirty(str(root)):
        print(f"{DIM}(working tree has uncommitted changes; they are included){RESET}")

    changes = collect(tmap.commit, cwd=str(root))
    sel = select(changes, tmap, ignore_docs=not doctests_enabled(root))
    total = len(tmap.tests)

    print(f"{DIM}map from {tmap.commit[:8]} ({distance} commit(s) back), "
          f"{len(changes.all_paths)} file(s) changed{RESET}")

    if args.shadow:
        return _shadow(adapter, sel, total)

    _print_selection(sel, total)
    if sel.run_all:
        return adapter.run(None)
    if not sel.tests:
        print(f"{GREEN}▸ nothing affected — 0 tests to run{RESET}")
        return 0
    return adapter.run(sel.tests)


def _shadow(adapter: PytestAdapter, sel: Selection, total: int) -> int:
    """Run everything; report what would have been skipped, and whether that
    would have hidden a real failure. This is how a team earns confidence
    before letting sift skip anything for real."""
    print(f"{DIM}shadow mode: running the full suite, skipping nothing{RESET}\n")
    started = time.time()
    code, failed = adapter.run_capture(None)
    elapsed = time.time() - started

    selected = set(sel.tests)
    would_skip = 0 if sel.run_all else max(total - len(selected), 0)
    missed = [] if sel.run_all else [t for t in failed if t not in selected]

    print(f"\n{BOLD}Shadow report{RESET}")
    print(f"  Would have run:     {total if sel.run_all else len(selected)} / {total}")
    print(f"  Would have skipped: {would_skip}")
    if total and not sel.run_all:
        saved = elapsed * (would_skip / total)
        print(f"  Full suite took:    {_fmt_secs(elapsed)}")
        print(f"  Est. time saved:    ~{_fmt_secs(saved)}")
    if missed:
        print(f"  {RED}⚠  Missed failures: {len(missed)}{RESET}")
        for t in missed[:10]:
            print(f"     {RED}{t}{RESET}")
        print(f"\n  {RED}These failed but sift would NOT have run them.{RESET}")
        print(f"  {RED}This is a selection bug — please report it.{RESET}")
    else:
        print(f"  {GREEN}⚠  Missed failures: 0{RESET}")
    return code


def cmd_status(args: argparse.Namespace) -> int:
    root = _root()
    _warn_if_shallow(root)
    tmap, distance = store.find_nearest(root)
    if tmap is None:
        held = store.stored_commits(root)
        if held:
            # Maps on disk but none reachable: almost always a shallow clone or
            # a rebased branch. Distinguish it from having no cache at all.
            print(f"{len(held)} map(s) stored, but none is an ancestor of HEAD "
                  f"— run `sift run --all`")
        else:
            print("no map stored — run `sift run --all`")
        return 1
    changes = collect(tmap.commit, cwd=str(root))
    print(f"{BOLD}sift status{RESET}")
    print(f"  map commit:   {tmap.commit[:8]} ({distance} commit(s) behind HEAD)")
    print(f"  adapter:      {tmap.adapter}")
    print(f"  tests mapped: {len(tmap.tests)}")
    print(f"  files mapped: {len(tmap.lines)}")
    print(f"  changed since:{len(changes.all_paths)} file(s)")
    print(f"  maps on disk: {len(store.stored_commits(root))} "
          f"(keeping {store.KEEP_MAPS})")
    if distance > 50:
        print(f"  {YELLOW}map is getting stale; consider `sift run --all`{RESET}")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    root = _root()
    tmap, _ = store.find_nearest(root)
    if tmap is None:
        print("no map stored — run `sift run --all`")
        return 1
    changes = collect(tmap.commit, cwd=str(root))
    sel = select(changes, tmap, ignore_docs=not doctests_enabled(root))

    target = args.test
    if sel.run_all:
        print(f"{YELLOW}every test is selected:{RESET}")
        for r in sel.reasons:
            print(f"  · {r}")
        return 0
    if target in sel.why:
        print(f"{GREEN}{target} is selected because:{RESET}")
        for r in sel.why[target]:
            print(f"  · {r}")
    elif target in tmap.tests:
        print(f"{DIM}{target} is skipped: it covers none of the changed lines.{RESET}")
    else:
        print(f"{YELLOW}{target} is not in the map.{RESET}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sift", description="Run only the tests your diff actually affects."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="select and run affected tests")
    run.add_argument("--all", action="store_true",
                     help="run the full suite and rebuild the map")
    run.add_argument("--shadow", action="store_true",
                     help="run everything, but report what would have been skipped")
    run.add_argument("pytest_args", nargs="*", help="extra args passed to pytest")
    run.set_defaults(func=cmd_run)

    st = sub.add_parser("status", help="show map freshness")
    st.set_defaults(func=cmd_status)

    ex = sub.add_parser("explain", help="why was a test selected or skipped?")
    ex.add_argument("test")
    ex.set_defaults(func=cmd_explain)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GitError as exc:
        print(f"{RED}git error: {exc}{RESET}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
