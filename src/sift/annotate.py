"""The one narrowing exemption sift makes to the module-level fail-open rule.

THE PROBLEM
-----------
In Python, `def` and `class` statements execute at import time -- the line
that creates the function object runs when the module loads, before any test
starts. coverage.py attributes that execution to no test context, so those
lines land in `map.module_level` and sift correctly fails open on them. That
is documented, measured (see #20), and the single largest reason sift ends up
running a full suite: changing a function's *signature* costs exactly as much
as changing its behaviour, even when the signature edit is only a type
annotation.

WHAT THIS EXEMPTS, AND WHY IT IS SAFE
--------------------------------------
Under `from __future__ import annotations`, annotation expressions are never
evaluated -- they are stored as strings. So a change confined to annotation
text cannot: raise at def time, change what gets bound to the function's
`__name__`, change its defaults, change its positional/keyword structure, or
change anything that executes when the function is later called. The only
thing it can change is the string sitting in `__annotations__`.

Given that, this module answers one narrow question: "does this specific,
single-line signature edit differ from before ONLY in annotation text?" It
says yes only when ALL of the following hold, and says no (falls back to
fail-open) the instant any of them doesn't:

  * The touched hunk replaces exactly one line with exactly one line. Multi-
    line signatures are out of scope entirely -- there is no page in this
    module that tries to reconstruct a signature spread across several diff
    hunks, because getting that wrong is exactly the kind of subtlety this
    project treats as fatal.
  * Both the old and new line are, on their own, a complete `def`/`async def`
    header -- opens with `def`/`async def`, closes with `:` on the same line.
  * Neither version has a decorator immediately above it. Decorators are the
    realistic way annotations affect runtime behaviour (validators, DI
    frameworks, dataclass-like processing on the class, route registration).
    Excluding anything decorated removes the biggest class of counterexample.
  * `class` headers are never exempted. A class statement doesn't have
    parameter/return annotations in the same sense, and base classes/keyword
    args (metaclass=, Generic[T], ...) are not something this module reasons
    about at all.
  * Everything about the two signatures is identical once annotations are
    stripped: same name, same parameter names/order/defaults, same
    positional-only/keyword-only structure, same decorator list (empty).
  * `from __future__ import annotations` is present in both versions of the
    file, so annotations were never evaluated at def time in either.

RESIDUAL RISK, STATED PLAINLY
------------------------------
This is a static, structural check, not a proof about everything Python can
do. Code that reflects on an UNDECORATED function's `__annotations__` or via
`typing.get_type_hints()` at runtime -- and behaves differently based on the
string it finds -- is a real, if unusual, way for this exemption to be wrong.
No decorator-based analysis can catch that, because there is no decorator to
see. This is documented here, in the README's limitations, and is why the
scope above is kept as narrow as it is rather than widened for more savings.
"""

from __future__ import annotations

import ast
import copy
from typing import Optional, Tuple

FUTURE_ANNOTATIONS = "from __future__ import annotations"


def exempt_body_range(
    old_src: str, new_src: str, old_lineno: int, new_lineno: int
) -> Optional[Tuple[int, int]]:
    """The old-side line range to attribute tests from instead of failing
    open, or None if this edit does not qualify for the exemption.

    The returned range is the function's body in the OLD file (map lookups
    are always old-side). Callers union whatever tests already cover those
    lines -- exactly the tests that would have been selected had the change
    been confined to the body instead of the signature.
    """
    if not _has_future_annotations(old_src) or not _has_future_annotations(new_src):
        return None

    old_lines = old_src.splitlines()
    new_lines = new_src.splitlines()
    if not (1 <= old_lineno <= len(old_lines)) or not (1 <= new_lineno <= len(new_lines)):
        return None

    old_header = old_lines[old_lineno - 1]
    new_header = new_lines[new_lineno - 1]

    old_node = _parse_single_line_def(old_header)
    new_node = _parse_single_line_def(new_header)
    if old_node is None or new_node is None:
        return None

    if _has_decorator_above(old_lines, old_lineno) or _has_decorator_above(new_lines, new_lineno):
        return None

    if not _same_ignoring_annotations(old_node, new_node):
        return None

    body_range = _function_body_range(old_src, old_lineno)
    if body_range is None:
        return None
    return body_range


def _has_future_annotations(src: str) -> bool:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
        elif isinstance(node, (ast.Expr,)):
            continue  # module docstring: __future__ imports may follow it
        else:
            break  # anything else means __future__ import, if any, is past
    return False


def _parse_single_line_def(line: str) -> Optional[ast.AST]:
    """Parse one line as a complete, standalone function header.

    Deliberately isolated from the rest of the file: it cannot see decorators
    (those are always on their own preceding lines, so decorator_list is
    always empty here -- callers must check for decorators separately) and it
    refuses anything whose signature is not fully closed on this one line.
    """
    src = line.strip()
    if not (src.startswith("def ") or src.startswith("async def ")):
        return None
    if not src.endswith(":"):
        return None  # signature continues onto another line
    try:
        tree = ast.parse(src + "\n    pass\n")
    except SyntaxError:
        return None
    if len(tree.body) != 1:
        return None
    node = tree.body[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    return node


def _has_decorator_above(lines, lineno: int) -> bool:
    """Is the nearest non-blank, non-comment line above `lineno` a decorator?

    Conservative on purpose: anything we can't confidently classify -- an
    IndexError from a decorator at the very top of the file, for instance --
    is treated as "yes, there is a decorator" so the exemption is refused
    rather than granted on a guess.
    """
    i = lineno - 2  # zero-indexed line before the header
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            i -= 1
            continue
        return stripped.startswith("@")
    return False


def _same_ignoring_annotations(a: ast.AST, b: ast.AST) -> bool:
    """Structurally identical once every annotation is stripped out.

    Comparing ast.dump() output after stripping catches everything else that
    matters: function name, decorator list (both required empty by the
    caller), argument order and names, defaults, positional-only/keyword-only
    markers, vararg/kwarg names, async-ness (different node types never
    compare equal). Only the annotation and return-type nodes are removed
    before comparing.
    """
    if type(a) is not type(b):
        return False
    return ast.dump(_strip_annotations(a)) == ast.dump(_strip_annotations(b))


def _strip_annotations(node: ast.AST) -> ast.AST:
    node = copy.deepcopy(node)
    node.returns = None
    args = node.args
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        for a in group:
            a.annotation = None
    if args.vararg:
        args.vararg.annotation = None
    if args.kwarg:
        args.kwarg.annotation = None
    node.body = [ast.Pass()]
    node.lineno = node.end_lineno = 0
    node.col_offset = node.end_col_offset = 0
    return node


def _function_body_range(src: str, header_lineno: int) -> Optional[Tuple[int, int]]:
    """The (start, end) line range of the function whose header is at
    `header_lineno`, found by walking the real file rather than the isolated
    single-line parse -- so nested functions, decorators counted correctly,
    and the true body extent (which can include further nested defs) all
    come from the file's actual structure.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == header_lineno:
            end = getattr(node, "end_lineno", None)
            if end is None or end <= header_lineno:
                return None
            return (header_lineno + 1, end)
    return None
