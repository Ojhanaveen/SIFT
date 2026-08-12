"""Selection tests.

Most of these assert that sift FAILS OPEN. That is the point: a test here
failing means sift might silently skip a test that mattered, which is the one
bug class that would make the tool worse than useless.
"""

import pytest

from sift.gitdiff import Changes
from sift.model import CoverageMap
from sift.select import select


@pytest.fixture
def tmap():
    m = CoverageMap(commit="abc123", adapter="pytest")
    m.tests = [
        "tests/test_math.py::test_add",
        "tests/test_math.py::test_mul",
        "tests/test_cart.py::test_subtotal",
    ]
    m.lines = {
        "app/math.py": {2: [0], 6: [1, 2]},
        "app/cart.py": {4: [2]},
    }
    m.module_level = {"app/cart.py": {1}}
    return m


def _changes(modified=None, **kw):
    c = Changes()
    c.modified = {k: set(v) for k, v in (modified or {}).items()}
    for key, val in kw.items():
        setattr(c, key, val)
    return c


# -- fail-open cases --------------------------------------------------------


def test_no_map_runs_everything():
    sel = select(_changes({"app/math.py": [2]}), None)
    assert sel.run_all


def test_dependency_file_runs_everything(tmap):
    sel = select(_changes({"requirements.txt": [1]}), tmap)
    assert sel.run_all


def test_pyproject_runs_everything(tmap):
    sel = select(_changes({"pyproject.toml": [3]}), tmap)
    assert sel.run_all


def test_conftest_runs_everything(tmap):
    sel = select(_changes({"tests/conftest.py": [1]}), tmap)
    assert sel.run_all


def test_ci_config_runs_everything(tmap):
    sel = select(_changes({".github/workflows/ci.yml": [10]}), tmap)
    assert sel.run_all


def test_unknown_file_type_runs_everything(tmap):
    sel = select(_changes({"app/schema.sql": [1]}), tmap)
    assert sel.run_all


def test_new_source_file_runs_everything(tmap):
    sel = select(_changes(added=["app/brand_new.py"]), tmap)
    assert sel.run_all


def test_deleted_file_runs_everything(tmap):
    sel = select(_changes(deleted=["app/math.py"]), tmap)
    assert sel.run_all


def test_unmapped_file_runs_everything(tmap):
    """A file the map has never seen could be covered by anything."""
    sel = select(_changes({"app/never_seen.py": [3]}), tmap)
    assert sel.run_all


def test_module_level_line_runs_everything(tmap):
    """Import-time code belongs to no single test, so it must fail open.

    Missing this is the subtlest silent-miss bug in the whole design.
    """
    sel = select(_changes({"app/cart.py": [1]}), tmap)
    assert sel.run_all
    assert "import time" in " ".join(sel.reasons)


# -- actual selection -------------------------------------------------------


def test_selects_only_tests_covering_the_line(tmap):
    sel = select(_changes({"app/math.py": [2]}), tmap)
    assert not sel.run_all
    assert sel.tests == ["tests/test_math.py::test_add"]


def test_selects_transitive_dependents(tmap):
    """Coverage gives transitivity for free: a cart test that runs math.py:6
    is selected when math.py:6 changes, without any import analysis."""
    sel = select(_changes({"app/math.py": [6]}), tmap)
    assert set(sel.tests) == {
        "tests/test_math.py::test_mul",
        "tests/test_cart.py::test_subtotal",
    }


def test_changed_test_file_runs_its_own_tests(tmap):
    sel = select(_changes({"tests/test_math.py": [3]}), tmap)
    assert set(sel.tests) == {
        "tests/test_math.py::test_add",
        "tests/test_math.py::test_mul",
    }


def test_no_changes_selects_nothing(tmap):
    sel = select(_changes(), tmap)
    assert not sel.run_all
    assert sel.tests == []


def test_explanations_are_recorded(tmap):
    sel = select(_changes({"app/math.py": [2]}), tmap)
    why = sel.why["tests/test_math.py::test_add"]
    assert any("app/math.py:2" in r for r in why)


# -- benign path allowlist --------------------------------------------------
#
# Every test below narrows selection, so each one is a place a silent miss
# could be introduced. Treat failures here as correctness bugs, not style.


def test_markdown_alone_runs_nothing(tmap):
    sel = select(_changes({"README.md": [1]}), tmap)
    assert not sel.run_all
    assert sel.tests == []
    assert "README.md" in sel.ignored


def test_docs_directory_is_benign(tmap):
    sel = select(_changes({"docs/guide/intro.md": [4]}), tmap)
    assert not sel.run_all
    assert sel.tests == []


def test_images_are_benign(tmap):
    sel = select(_changes({"assets/logo.png": [1]}), tmap)
    assert not sel.run_all
    assert sel.tests == []


def test_license_is_benign(tmap):
    sel = select(_changes({"LICENSE": [1]}), tmap)
    assert not sel.run_all
    assert sel.tests == []


def test_benign_file_does_not_mask_a_real_change(tmap):
    """A docs edit alongside a code edit must not hide the code edit."""
    sel = select(_changes({"README.md": [1], "app/math.py": [2]}), tmap)
    assert not sel.run_all
    assert sel.tests == ["tests/test_math.py::test_add"]


def test_benign_file_does_not_mask_an_always_run_rule(tmap):
    """Always-run rules are evaluated before filtering, so a benign path in
    the same commit can never suppress them."""
    sel = select(_changes({"README.md": [1], "requirements.txt": [2]}), tmap)
    assert sel.run_all


def test_doctests_make_markdown_dangerous(tmap):
    """With --doctest-glob a markdown file IS executable code. When doctests
    are on, docs stop being benign and we fall back to running everything."""
    sel = select(_changes({"README.md": [1]}), tmap, ignore_docs=False)
    assert sel.run_all


def test_images_stay_benign_even_with_doctests(tmap):
    """Doctests can execute markdown; they cannot execute a PNG."""
    sel = select(_changes({"assets/logo.png": [1]}), tmap, ignore_docs=False)
    assert not sel.run_all
    assert sel.tests == []


def test_data_files_are_not_benign(tmap):
    """Fixture data is read by tests at runtime. These must keep failing open,
    or we silently skip tests whose input changed."""
    for path in ("tests/fixtures/data.json", "config.yaml", "seed.csv",
                 "expected_output.txt"):
        sel = select(_changes({path: [1]}), tmap)
        assert sel.run_all, f"{path} must not be treated as benign"
