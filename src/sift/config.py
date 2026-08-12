"""Repo configuration probing.

Right now this exists for one reason: deciding whether documentation files are
safe to ignore. If a project runs doctests, its .md and .rst files are
executable code and must not be treated as benign.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

# Files that can switch doctest collection on.
PYTEST_CONFIG_FILES = [
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
]

DOCTEST_MARKERS = (
    "--doctest-modules",
    "--doctest-glob",
    "doctest_namespace",
    "doctest_optionflags",
    "doctest_encoding",
)


def doctests_enabled(root: Path) -> bool:
    """Does this repo appear to run doctests?

    Deliberately crude: a plain substring scan of the pytest config files. A
    false positive only costs speed (docs stop being treated as benign); a
    false negative would let us skip a test that a markdown edit broke. When
    the choice is between slow and wrong, take slow.
    """
    for name in PYTEST_CONFIG_FILES:
        path = root / name
        if not path.exists():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            return True  # unreadable config: assume the risky case
        if any(marker in text for marker in DOCTEST_MARKERS):
            return True
    return False


def doctest_globs(root: Path) -> List[str]:
    """Best-effort list of doc files pulled into the suite by --doctest-glob.

    Unused for now; kept so the allowlist can later be narrowed to exactly the
    files a project actually collects rather than disabled wholesale.
    """
    globs: List[str] = []
    for name in PYTEST_CONFIG_FILES:
        path = root / name
        if not path.exists():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            if "--doctest-glob" in line:
                _, _, rest = line.partition("--doctest-glob")
                rest = rest.lstrip("= ").strip().strip("\"'")
                token = rest.split()[0].strip("\"',") if rest else ""
                if token:
                    globs.append(token)
    return globs
