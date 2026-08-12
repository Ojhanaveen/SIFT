"""CLI behaviour that users read as a status report.

These are messaging tests, which normally would not earn their keep -- except
that the no-map path is the one place sift runs the full suite while *looking*
like it made a decision. Getting that wording wrong teaches people the tool is
doing nothing for them.
"""

import argparse

import pytest

from sift import cli
from sift.model import Selection


class StubAdapter:
    """Records what the CLI asked to run, without running anything."""

    def __init__(self):
        self.ran_with = "not called"

    def run(self, tests):
        self.ran_with = tests
        return 0


@pytest.fixture
def no_map(monkeypatch, tmp_path):
    adapter = StubAdapter()
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_adapter", lambda root, extra: adapter)
    monkeypatch.setattr(cli, "head", lambda cwd=None: "0" * 40)
    monkeypatch.setattr(cli, "is_shallow", lambda cwd=None: False)
    monkeypatch.setattr(cli.store, "find_nearest", lambda root: (None, -1))
    return adapter


def run_cmd(**flags):
    args = argparse.Namespace(all=False, shadow=False, pytest_args=[])
    for k, v in flags.items():
        setattr(args, k, v)
    return cli.cmd_run(args)


def test_no_map_runs_everything(no_map, capsys):
    assert run_cmd() == 0
    assert no_map.ran_with is None, "None means 'the whole suite'"
    out = capsys.readouterr().out
    assert "running everything" in out
    assert "sift run --all" in out


def test_shadow_without_a_map_says_it_cannot_compare(no_map, capsys):
    """The wart this test exists for: --shadow with no map used to print a
    generic notice and run the full suite, which is indistinguishable from a
    shadow report showing zero savings."""
    assert run_cmd(shadow=True) == 0
    assert no_map.ran_with is None
    out = capsys.readouterr().out
    assert "nothing to compare against" in out
    # It must NOT look like a real shadow report.
    assert "Would have skipped" not in out
    assert "Missed failures" not in out


def test_plain_run_does_not_mention_shadow(no_map, capsys):
    run_cmd()
    assert "compare against" not in capsys.readouterr().out


# -- the shadow report must explain a forced full run ----------------------


class RecordingAdapter(StubAdapter):
    def run_capture(self, tests):
        self.ran_with = tests
        return 0, []


def test_shadow_report_gives_the_reason_for_a_full_run(capsys):
    """"Ran 44/44, skipped 0" with no reason is indistinguishable from "sift
    analysed your diff and found nothing skippable". A team reading that for
    two weeks concludes the tool does nothing, when a safety rule fired."""
    sel = Selection(tests=[], run_all=True,
                    reasons=["conftest.py can affect any test (always-run rule)"])
    cli._shadow(RecordingAdapter(), sel, total=44)

    out = capsys.readouterr().out
    assert "Would have skipped: 0" in out
    assert "safety rule forced a full run" in out
    assert "conftest.py can affect any test" in out


def test_shadow_report_truncates_a_long_reason_list(capsys):
    sel = Selection(tests=[], run_all=True,
                    reasons=[f"reason {i}" for i in range(9)])
    cli._shadow(RecordingAdapter(), sel, total=44)

    out = capsys.readouterr().out
    assert "reason 4" in out
    assert "reason 5" not in out
    assert "+4 more" in out


def test_shadow_report_stays_quiet_when_selection_was_real(capsys):
    """A genuine narrow selection must not be dressed up as a safety trip."""
    sel = Selection(tests=["t::a", "t::b"], run_all=False)
    cli._shadow(RecordingAdapter(), sel, total=44)

    out = capsys.readouterr().out
    assert "safety rule" not in out
    assert "Would have skipped: 42" in out
