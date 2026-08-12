"""Doctest detection.

If this returns False for a repo that does run doctests, sift will treat
markdown as benign and can skip a test that a docs edit broke. False positives
only cost speed. The asymmetry is intentional.
"""

from sift.config import doctests_enabled


def test_no_config_means_no_doctests(tmp_path):
    assert doctests_enabled(tmp_path) is False


def test_plain_config_means_no_doctests(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -q\n")
    assert doctests_enabled(tmp_path) is False


def test_doctest_modules_in_pytest_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --doctest-modules\n")
    assert doctests_enabled(tmp_path) is True


def test_doctest_glob_in_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--doctest-glob=*.md"\n'
    )
    assert doctests_enabled(tmp_path) is True


def test_doctest_in_setup_cfg(tmp_path):
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\naddopts = --doctest-modules\n")
    assert doctests_enabled(tmp_path) is True


def test_doctest_in_tox_ini(tmp_path):
    (tmp_path / "tox.ini").write_text("[pytest]\ndoctest_optionflags = ELLIPSIS\n")
    assert doctests_enabled(tmp_path) is True


def test_only_the_relevant_files_are_scanned(tmp_path):
    """A stray mention of doctest in unrelated files must not trip it."""
    (tmp_path / "README.md").write_text("we do not use --doctest-modules here\n")
    assert doctests_enabled(tmp_path) is False
