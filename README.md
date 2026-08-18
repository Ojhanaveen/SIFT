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

It is built on one rule: **being wrong is fatal, being slow is survivable.**
Every ambiguity resolves toward running more tests. In practice that means sift
runs your whole suite more often than a demo would suggest — see
[what to actually expect](#what-to-actually-expect) for measured numbers before
you decide it is worth adopting.

> **Status: early.** Python (pytest) and JavaScript (Jest). The selection logic
> is tested and conservative, but you should run it in `--shadow` mode (below)
> before trusting it to skip anything.

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

The one exception is files that provably cannot affect a test: documentation,
images, and repo furniture like `LICENSE` and `.gitignore`. Those are ignored,
so a README edit doesn't cost you a full test run.

Data files are **not** on that list — tests read `.json`, `.yaml`, `.csv` and
`.txt` as fixtures all the time, so those still fail open. And if your project
runs doctests (`--doctest-modules` / `--doctest-glob`), sift detects it and
stops treating documentation as safe, because then your markdown really is
executable code.

## What to actually expect

Most tools like this show you their best case. Here is the measured one.

We replayed **17 real commits** from two upstream Python projects — building the
map at each commit's parent, checking the commit out, and running shadow mode.
That is exactly the position a team is in when a pull request lands.

| | Commits | Narrowed | Ran everything |
|---|---|---|---|
| `itsdangerous` (297 tests) | 12 | 1 | 11 |
| `flask` (492 tests) | 5 | 2 | 3 |

**sift ran the full suite for 14 of 17 commits.** That is not the number a
launch post would pick, and it is the number you should plan around.

The dominant cause is structural. In Python, `def` and `class` statements
execute at **import** time — the line that creates the function object runs when
the module loads, before any test starts. coverage.py attributes those lines to
no test, so sift cannot know which tests care, and it runs everything:

```python
class HMACAlgorithm(SigningAlgorithm):   # change this line -> full run
def loads(s: str | bytes) -> t.Any:      # or this one      -> full run
```

So **changing a function signature or a class declaration costs you a full
run.** Changing what's *inside* a function does not. Mature libraries do a
great deal of the former, which is why `itsdangerous` saw almost no benefit —
these numbers predate the one narrow exception below.

sift pays off when your changes are mostly to function bodies, and your suite is
slow enough that skipping most of it is worth caring about. It pays off least on
small, heavily-typed libraries with fast suites — where you did not need it
anyway.

**One narrow exception:** a single-line function signature that changes *only*
its type annotations — under `from __future__ import annotations`, no
decorators, nothing else about the signature different — no longer forces a
full run; sift selects whatever already covers the function body instead.
That covers the common case of widening or tightening a type hint. It does
not cover class declarations, multi-line signatures, or anything decorated
(route registration, validators, DI). See the limitations below for what this
still can't rule out.

### On "Missed failures: 0"

Be careful how much you read into that line when replaying merged history.
Merged commits are green by construction, so there are no failures available to
miss and a clean report is close to guaranteed. It shows sift is not
mis-selecting into an error; it does not prove sift would have caught a
regression.

The honest test is to **plant a bug and check sift still selects the test that
catches it**. That is the bar in [CONTRIBUTING.md](CONTRIBUTING.md) for new
adapters, and it is the one worth applying to your own repo.

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

## Use it in CI

This is what sift is for. The map lives in `.sift/maps/` and is never
committed — it would conflict on every merge — so CI has to carry it between
runs in the runner's cache.

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0          # required — see below

- uses: actions/cache/restore@v4
  with:
    path: .sift/maps
    key: sift-map-v1-${{ runner.os }}-${{ github.sha }}
    restore-keys: |
      sift-map-v1-${{ runner.os }}-

- run: sift run --shadow    # or `sift run` once you trust it

- uses: actions/cache/save@v4
  if: github.ref == 'refs/heads/main'
  with:
    path: .sift/maps
    key: sift-map-v1-${{ runner.os }}-${{ github.sha }}
```

Three things about that recipe are load-bearing:

**`fetch-depth: 0` is not optional.** The default checkout is shallow, so
`git log` stops after one commit and sift can never walk back to the commit its
map was built at. It then fails open and runs your whole suite on every build,
with a green tick and no complaint — you would just quietly get no benefit.
sift now prints a warning when it detects a shallow clone, but the fix is here.

**The commit SHA goes in the key; the branch does not.** Cache keys are
immutable, so a key that doesn't change per commit would be written once and
then never refresh. The `restore-keys` prefix falls back to the newest map from
any earlier run. Branch scoping is already handled for you — Actions lets a PR
read caches from its base branch — and putting the branch in the key would break
that fallback rather than help it.

**Save only on the default branch.** A cache written from a PR is scoped to that
PR, can't be read by anything else, and is evicted when it closes. It only burns
quota and pushes out maps that are actually being used.

Build the map on your default branch with `sift run --all`; pull requests then
restore it and diff against it. Older maps are pruned automatically, so the
cache doesn't grow without bound.

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

## Known limitations

- **Python and JavaScript (pytest, Jest).** Vitest, Go, Ruby, Rust and Java are
  open — see [CONTRIBUTING.md](CONTRIBUTING.md).
- **JS selection is per spec file, not per test.** `coverage.py` labels every
  line with the test that ran it; istanbul has no equivalent, so the Jest
  adapter traces one spec file at a time and attributes coverage to the file.
  Changing a line one test covers pulls in its whole spec. Coarser than Python,
  and the safe direction — a per-test reporter hook can narrow it later without
  changing the map format.
- **Most non-Python file changes still trigger a full run.** Documentation,
  images and repo furniture are ignored (see below), but any other unrecognised
  file — data, config, templates — fails open.
- **Non-code dependencies are invisible.** If a test reads a fixture JSON file,
  changing that file won't select it. Keep such files under an always-run rule.
- **The annotation-only exemption is a static check, not a proof about
  everything Python can do.** It looks at structure — decorators, argument
  shape, whether `from __future__ import annotations` is present — not at
  runtime behavior. Code that reflects on an *undecorated* function's
  `__annotations__` or via `typing.get_type_hints()` and behaves differently
  based on what it finds is a real, if unusual, way for this to be wrong. This
  is why the exemption is scoped as narrowly as it is — see
  `src/sift/annotate.py` for the full argument.
- Insertions select the lines either side of the insertion point, which
  over-selects slightly. Deliberate.

## Contributing

Adding a language means implementing one small class against a five-method
contract, then registering it. See [CONTRIBUTING.md](CONTRIBUTING.md) — Go,
Ruby, Rust, Java and Vitest are open.

## License

MIT. Provided as-is, without warranty. sift is conservative by design, but you
are responsible for your own test strategy.
