# Contributing to sift

The most valuable contribution is **a new language adapter**. The core is
language-agnostic; everything language-specific lives behind one small
interface. If it takes you more than an afternoon to add a language, that's a
bug in our design — please tell us.

## The rule that governs every change

> **Being wrong is fatal. Being slow is survivable.**

If sift skips a test that would have caught a bug, a user loses trust and never
comes back. A PR that makes selection *narrower* (fewer tests) needs to prove it
cannot cause a miss. A PR that makes selection *wider* is easy to accept.

When in doubt: run more tests.

## Writing an adapter

Implement this. That's the whole contract:

```python
class MyAdapter:
    name = "jest"

    def detect(self) -> bool:
        """Does this repo use my framework?"""

    def discover(self) -> list[str]:
        """All test ids in the repo."""

    def trace(self, commit: str) -> CoverageMap:
        """Run the full suite with per-test coverage, return the map."""

    def run(self, tests: list[str] | None) -> int:
        """Run these test ids (None = all). Return the exit code."""

    def run_capture(self, tests) -> tuple[int, list[str]]:
        """Same, but also return the ids that failed (shadow mode needs this)."""
```

See `src/sift/adapters/pytest_adapter.py` for a worked example — it's ~140 lines.

### What makes a good adapter

1. **Test ids must be re-runnable.** Whatever `trace()` records must be something
   you can hand straight back to the runner. pytest node ids
   (`tests/test_x.py::test_y`) work perfectly. Don't invent your own scheme.
2. **Record import-time execution separately.** Code that runs when a module is
   loaded belongs to no single test. Put those lines in `map.module_level` — the
   core uses it to fail open. Getting this wrong causes silent misses.
3. **Paths must be repo-relative**, using the repo root as the base.
4. **A failing suite is still a valid map.** Don't throw away the trace because
   tests failed.

### Languages currently open

| Language | Framework | Coverage source | Status |
|---|---|---|---|
| Python | pytest | `coverage.py` contexts | ✅ done |
| JS/TS | Jest, Vitest | v8 / istanbul per-test | 🔴 open |
| Go | `go test` | `-coverprofile` per test | 🔴 open |
| Ruby | RSpec | SimpleCov | 🔴 open |
| Rust | `cargo test` | llvm-cov | 🔴 open |
| Java | JUnit | JaCoCo | 🔴 open |

Claim one by opening an issue first, so two people don't build the same thing.

## Running the tests

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
```

The tests in `tests/test_select.py` are mostly assertions that sift **fails
open**. If you break one, you have introduced a silent-miss bug — that is the
one category of change we won't merge.

## Testing an adapter for real

A unit test isn't enough. Prove it on a real repo:

1. Build a map, then introduce a genuine bug in a commonly-used function.
2. Run `sift run --shadow`.
3. `Missed failures` must be **0**.

Include that output in your PR. It's the only evidence that matters.

## Style

Standard library only in the core, where possible — sift installs into other
people's CI, and every dependency is a reason for someone not to adopt it.
