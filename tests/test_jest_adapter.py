"""Jest adapter.

Split deliberately. The istanbul-parsing and detection logic is pure and runs
everywhere; the end-to-end trace needs Node and a populated fixture, so it
skips when those are absent rather than making the whole suite require npm.
"""

import json
import shutil
from pathlib import Path

import pytest

from sift.adapters import jest_adapter
from sift.adapters.base import Adapter
from sift.adapters.jest_adapter import JestAdapter, _covered_lines


def _fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures" / "jsproj"


needs_jest = pytest.mark.skipif(
    shutil.which("npx") is None or not (_fixture_dir() / "node_modules").exists(),
    reason="needs Node and `npm install` in tests/fixtures/jsproj",
)


# -- contract --------------------------------------------------------------


def test_satisfies_the_adapter_protocol(tmp_path):
    assert isinstance(JestAdapter(tmp_path), Adapter)


# -- detection -------------------------------------------------------------


def test_detects_jest_in_dev_dependencies(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"jest": "^29.0.0"}})
    )
    assert JestAdapter(tmp_path).detect()


def test_detects_a_jest_config_file(tmp_path):
    (tmp_path / "jest.config.js").write_text("module.exports = {};")
    assert JestAdapter(tmp_path).detect()


def test_detects_a_jest_key_in_package_json(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"jest": {"verbose": True}}))
    assert JestAdapter(tmp_path).detect()


def test_does_not_claim_a_node_project_without_jest(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"mocha": "^10.0.0"}})
    )
    assert not JestAdapter(tmp_path).detect()


def test_unreadable_package_json_is_not_claimed(tmp_path):
    """Better to decline than to claim a repo and then fail to run it."""
    (tmp_path / "package.json").write_text("{ this is not json")
    assert not JestAdapter(tmp_path).detect()


# -- istanbul parsing ------------------------------------------------------


def test_covered_lines_ignores_unexecuted_statements():
    entry = {
        "statementMap": {
            "0": {"start": {"line": 2}, "end": {"line": 2}},
            "1": {"start": {"line": 6}, "end": {"line": 6}},
        },
        "s": {"0": 3, "1": 0},
    }
    assert _covered_lines(entry) == {2}


def test_covered_lines_spans_multi_line_statements():
    """A statement covering lines 4-7 marks all of them. That over-attributes
    slightly, which is the safe direction."""
    entry = {
        "statementMap": {"0": {"start": {"line": 4}, "end": {"line": 7}}},
        "s": {"0": 1},
    }
    assert _covered_lines(entry) == {4, 5, 6, 7}


def test_covered_lines_survives_a_malformed_entry():
    entry = {
        "statementMap": {"0": {"start": {}}, "1": {"start": {"line": 9}}},
        "s": {"0": 1, "1": 1},
    }
    assert _covered_lines(entry) == {9}


def test_paths_outside_the_repo_are_dropped(tmp_path):
    a = JestAdapter(tmp_path)
    assert a._relative(str(tmp_path / "src" / "x.js")) == "src/x.js"
    assert a._relative("/somewhere/else/y.js") is None
    assert a._relative(str(tmp_path / "node_modules" / "z" / "i.js")) is None


# -- failure reporting -----------------------------------------------------


def test_unparseable_report_is_treated_as_everything_failing(tmp_path, monkeypatch):
    """Shadow mode's whole value is the missed-failures line. If we cannot tell
    what failed, reporting an empty failure list would award a clean bill that
    was never earned -- so be pessimistic instead."""
    class Proc:
        returncode = 1
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(jest_adapter.subprocess, "run", lambda *a, **k: Proc())
    monkeypatch.setattr(JestAdapter, "_jest_argv", lambda self: ["npx", "jest"])

    code, failed = JestAdapter(tmp_path).run_capture(["a.test.js", "b.test.js"])
    assert code == 1
    assert failed == ["a.test.js", "b.test.js"]


# -- end to end ------------------------------------------------------------


@needs_jest
def test_trace_attributes_a_shared_function_to_every_spec_that_reaches_it():
    """The property the whole tool rests on. cart.test.js never mentions
    math.js, but it calls add() through cart.js -- so a change to add() must
    select it. Coverage sees that for free; import analysis would not."""
    tmap = JestAdapter(_fixture_dir()).trace("a" * 40)

    assert sorted(tmap.tests) == ["src/cart.test.js", "src/math.test.js"]

    # line 2 is `return a + b` inside add()
    assert tmap.tests_for("src/math.js", 2) == {
        "src/cart.test.js", "src/math.test.js"
    }
    # line 6 is `return a - b` inside subtract(), which only math.test.js reaches
    assert tmap.tests_for("src/math.js", 6) == {"src/math.test.js"}


@needs_jest
def test_discover_returns_rerunnable_repo_relative_ids():
    specs = JestAdapter(_fixture_dir()).discover()
    assert specs == ["src/cart.test.js", "src/math.test.js"]
