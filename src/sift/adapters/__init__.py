"""Language/framework adapters.

Adding a language means implementing one small class and adding it to REGISTRY.
See base.py for the contract and CONTRIBUTING.md for the walkthrough.

The registry has two jobs. It picks the runner for `sift run`, and it tells the
core which file-level knowledge is in play during selection. The second job
matters even for adapters that are not the active runner: a Python repo that
grows a `package-lock.json` should still fail open on it, and it only does so
because the registered profiles say that file can affect anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Type

from .base import Adapter, LanguageProfile
from .jest_adapter import JestAdapter
from .pytest_adapter import PytestAdapter

# Order matters only for ties: the first adapter that detects wins.
REGISTRY: List[Type] = [PytestAdapter, JestAdapter]

__all__ = ["Adapter", "LanguageProfile", "REGISTRY", "detect", "profiles",
           "JestAdapter", "PytestAdapter"]


def detect(root: Path, args: Optional[Sequence[str]] = None):
    """The first registered adapter that recognises this repo, or None."""
    for cls in REGISTRY:
        candidate = cls(root, args)
        try:
            if candidate.detect():
                return candidate
        except OSError:
            continue  # a broken adapter must never take the whole run down
    return None


def profiles() -> List[LanguageProfile]:
    """Every registered adapter's file knowledge.

    Deliberately every adapter, not just the detected one. Selection consults
    the union, and each profile can only ever force MORE tests to run, so a
    profile that does not apply to this repo costs nothing but a full run in
    the rare case its patterns match.
    """
    return [cls.profile for cls in REGISTRY]
