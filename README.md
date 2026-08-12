# sift

**Run only the tests your diff actually affects.**

```
$ sift run
map from 03b69945 (0 commit(s) back), 1 file(s) changed
▸ selected 3 of 11 tests (27.3%)
```

Most teams run their whole test suite on every commit, because they have no way
to know which tests matter. sift records which test touches which line of code,
then uses your diff to run only the tests that could possibly break.

> **Status: v0, early.** Python/pytest only. The selection logic is tested and
> conservative, but you should run it in `--shadow` mode (below) before trusting
> it to skip anything.

---

## Safe by default, fast when confident

The dangerous failure for a tool like this is skipping a test that would have
caught a bug. So sift resolves every ambiguity by **running more tests**:

- Changed a dependency file, CI config, `conftest.py`, or `Dockerfile`? Runs everything.
- Changed a file the map has never seen? Runs everything.
- Changed a line that executes at *import* time, outside any test? Runs everything.
- Added, deleted, or renamed a source file? Runs everything.
- No map available for any ancestor commit? Runs everything.

It is designed to be boringly conservative. Speed is what's left over.

## Try it without risk

Shadow mode runs your **full** suite, skipping nothing, and reports what it
*would* have done:

```
$ sift run --shadow

Shadow report
  Would have run:     3 / 11
  Would have skipped: 8
  Full suite took:    4m 12s
  Est. time saved:    ~3m 03s
  ⚠  Missed failures: 0
```

That last line is sift grading its own homework. Run shadow mode for a couple of
weeks; if `Missed failures` stays at 0 on your codebase, you have your own
evidence that it's safe to switch on.

## Install

```bash
pip install -e ".[pytest]"
```

## Use

```bash
sift run --all       # run everything, build the map (do this first)
sift run             # run only affected tests
sift run --shadow    # run everything, report what would have been skipped
sift status          # how fresh is the map?
sift explain <test>  # why was this test selected or skipped?
```

```
$ sift explain "tests/test_cart.py::test_subtotal"
tests/test_cart.py::test_subtotal is selected because:
  · covers app/math_ops.py:10
```

## How it works

1. `sift run --all` runs your suite under `coverage.py` dynamic contexts, which
   label every executed line with the pytest node id that ran it. That gives a
   `test → file:line` map, stored in `.sift/` keyed by the commit it was built at.
2. `sift run` diffs your working tree against **the nearest ancestor commit that
   has a map** — not `HEAD~1`, which would miss everything in between — and
   looks up which tests touched the changed lines.
3. Safety rules run first and can override any of it with "run everything".

Because the map is built from real execution, transitive dependencies come free:
change a utility function and the high-level tests that call it indirectly are
selected, with no import analysis required.

Line numbers are looked up on the **old side** of each diff hunk, since the map
was built against the base commit. Using new-side numbers would read the wrong
lines and select confidently wrong tests.

## Known limitations (v0)

- **Python/pytest only.** The adapter boundary exists so other languages can be
  added — see [CONTRIBUTING.md](CONTRIBUTING.md). JS/TS is the next target.
- **Any non-Python file change triggers a full run.** That includes README edits.
  Correct, but blunt; a benign-path allowlist is the top v1 issue.
- **Non-code dependencies are invisible.** If a test reads a fixture JSON file,
  changing that file won't select it. Keep such files under an always-run rule.
- **No CI cache integration yet**, so the map doesn't persist between CI runs.
- Insertions select the lines either side of the insertion point, which
  over-selects slightly. Deliberate.

## Contributing

Adding a language means implementing one small class with six methods. See
[CONTRIBUTING.md](CONTRIBUTING.md) — Go, Ruby, Rust and JS/TS are all open.

## License

MIT. Provided as-is, without warranty. sift is conservative by design, but you
are responsible for your own test strategy.
