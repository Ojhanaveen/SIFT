"""The adapter boundary.

CONTRIBUTING.md promises that adding a language is one small class and an
afternoon. These tests are what makes that promise checkable: the registry has
to actually pick the adapter, and the core must not carry knowledge that only
one ecosystem could satisfy.

The governing rule applies to the boundary too. A profile may only ever cause
MORE tests to run, so registering an adapter for a language a repo does not
use has to be harmless.
"""

import pytest

from sift import adapters, select as select_mod
from sift.adapters.base import Adapter, LanguageProfile
from sift.adapters.pytest_adapter import PytestAdapter
from sift.gitdiff import Changes
from sift.model import CoverageMap
from sift.select import select


# -- the contract ----------------------------------------------------------


def test_pytest_adapter_satisfies_the_protocol(tmp_path):
    assert isinstance(PytestAdapter(tmp_path), Adapter)


def test_every_registered_adapter_satisfies_the_protocol(tmp_path):
    for cls in adapters.REGISTRY:
        assert isinstance(cls(tmp_path), Adapter), f"{cls.__name__} is incomplete"


def test_every_registered_adapter_declares_a_profile():
    for cls in adapters.REGISTRY:
        assert isinstance(cls.profile, LanguageProfile)
        assert cls.profile.source_extensions, f"{cls.__name__} claims no source files"


# -- detection -------------------------------------------------------------


def test_detect_finds_pytest_from_a_marker_file(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    found = adapters.detect(tmp_path)
    assert found is not None and found.name == "pytest"


def test_detect_returns_none_for_an_unrecognised_repo(tmp_path):
    (tmp_path / "main.go").write_text("package main\n")
    assert adapters.detect(tmp_path) is None


def test_detect_passes_extra_args_through(tmp_path):
    (tmp_path / "conftest.py").write_text("")
    found = adapters.detect(tmp_path, ["-k", "smoke"])
    assert found.extra_args == ["-k", "smoke"]


def test_a_raising_adapter_does_not_take_the_run_down(tmp_path, monkeypatch):
    """A broken third-party adapter must not be able to break sift for
    everyone else -- it should just fail to detect."""

    class Exploding(PytestAdapter):
        name = "boom"

        def detect(self):
            raise OSError("disk gone")

    monkeypatch.setattr(adapters, "REGISTRY", [Exploding, PytestAdapter])
    (tmp_path / "conftest.py").write_text("")
    found = adapters.detect(tmp_path)
    assert found is not None and found.name == "pytest"


# -- the core no longer hardcodes Python -----------------------------------


FAKE = LanguageProfile(
    source_extensions=(".ts",),
    test_file_patterns=("*.spec.ts",),
    always_run_patterns=("package-lock.json",),
)


class FakeAdapter(PytestAdapter):
    name = "fake"
    profile = FAKE


@pytest.fixture
def with_fake(monkeypatch):
    monkeypatch.setattr(adapters, "REGISTRY", [PytestAdapter, FakeAdapter])


def _map():
    return CoverageMap(commit="a" * 40, adapter="pytest",
                       tests=["t::one"], lines={"app/x.py": {1: [0]}})


def _changed(path):
    c = Changes()
    c.modified[path] = {1}
    return c


def test_a_registered_profile_contributes_its_always_run_files(with_fake):
    """Before the boundary existed, package-lock.json was simply unknown to
    the core -- there was nowhere for a JS adapter to declare it."""
    sel = select(_changed("package-lock.json"), _map())
    assert sel.run_all
    assert any("package-lock.json" in r for r in sel.reasons)


def test_an_unregistered_ecosystems_lockfile_still_fails_open(with_fake):
    """Nothing declares Gemfile.lock, so it is unanalysable -- which is the
    other safety net, and equally a full run."""
    sel = select(_changed("Gemfile.lock"), _map())
    assert sel.run_all


def test_registering_a_language_does_not_narrow_another(with_fake):
    """Adding the fake TS adapter must not change how a Python diff is
    treated. Profiles may widen; they may never narrow."""
    sel = select(_changed("app/x.py"), _map())
    assert not sel.run_all
    assert sel.tests == ["t::one"]


def test_a_source_file_with_no_map_entry_still_fails_open(with_fake):
    """.ts is now 'analysable', but the map has never seen this file, so the
    second safety net has to catch it."""
    sel = select(_changed("app/thing.ts"), _map())
    assert sel.run_all


def test_an_empty_registry_makes_everything_fail_open(monkeypatch):
    """The degenerate case. With no adapters at all the core knows nothing
    about any file, so it must run everything rather than reason from a map
    it has no business trusting."""
    monkeypatch.setattr(adapters, "REGISTRY", [])
    sel = select(_changed("app/x.py"), _map())
    assert sel.run_all
    assert select_mod._source_extensions() == ()


def test_test_file_patterns_come_from_profiles(with_fake):
    assert select_mod.is_test_file("app/cart.spec.ts")
    assert select_mod.is_test_file("tests/test_math.py")
    assert not select_mod.is_test_file("app/cart.ts")
