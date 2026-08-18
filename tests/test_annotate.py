"""The annotation-only exemption.

This is a NARROWING change to the correctness-critical path, so per the
project's governing rule the burden here is proving it cannot cause a silent
miss -- not just showing the happy path works. Most of these tests exist to
pin the boundary: the many ways this must refuse rather than guess.
"""

from sift.annotate import exempt_body_range

PREFIX = "from __future__ import annotations\n\n\n"


def src(*lines: str) -> str:
    return PREFIX + "\n".join(lines) + "\n"


# -- the happy path ----------------------------------------------------------


def test_annotation_only_change_is_exempted():
    old = src(
        "def add(a: int, b: int) -> int:",
        "    return a + b",
    )
    new = src(
        "def add(a: float, b: float) -> float:",
        "    return a + b",
    )
    # header is line 4 (3-line PREFIX + 1)
    got = exempt_body_range(old, new, old_lineno=4, new_lineno=4)
    assert got == (5, 5)


def test_widening_a_parameter_type_is_exempted():
    old = src("def handle(x: int) -> None:", "    pass")
    new = src("def handle(x: object) -> None:", "    pass")
    assert exempt_body_range(old, new, 4, 4) is not None


def test_adding_a_return_annotation_where_none_existed_is_exempted():
    old = src("def add(a, b):", "    return a + b")
    new = src("def add(a, b) -> int:", "    return a + b")
    assert exempt_body_range(old, new, 4, 4) is not None


def test_async_def_is_handled():
    old = src("async def fetch(url: str) -> bytes:", "    return b''")
    new = src("async def fetch(url: object) -> bytes:", "    return b''")
    assert exempt_body_range(old, new, 4, 4) is not None


def test_method_inside_a_class_is_handled():
    old = src(
        "class Widget:",
        "    def resize(self, w: int, h: int) -> None:",
        "        self.w, self.h = w, h",
    )
    new = src(
        "class Widget:",
        "    def resize(self, w: float, h: float) -> None:",
        "        self.w, self.h = w, h",
    )
    got = exempt_body_range(old, new, 5, 5)
    assert got == (6, 6)


# -- refuses: anything that changes behaviour --------------------------------


def test_a_new_parameter_is_not_exempted():
    old = src("def f(a: int) -> int:", "    return a")
    new = src("def f(a: int, b: int = 0) -> int:", "    return a")
    assert exempt_body_range(old, new, 4, 4) is None


def test_a_renamed_parameter_is_not_exempted():
    old = src("def f(a: int) -> int:", "    return a")
    new = src("def f(b: int) -> int:", "    return b")
    assert exempt_body_range(old, new, 4, 4) is None


def test_a_changed_default_value_is_not_exempted():
    old = src("def f(a: int = 1) -> int:", "    return a")
    new = src("def f(a: int = 2) -> int:", "    return a")
    assert exempt_body_range(old, new, 4, 4) is None


def test_a_renamed_function_is_not_exempted():
    old = src("def f(a: int) -> int:", "    return a")
    new = src("def g(a: int) -> int:", "    return a")
    assert exempt_body_range(old, new, 4, 4) is None


def test_sync_to_async_is_not_exempted():
    old = src("def f() -> None:", "    pass")
    new = src("async def f() -> None:", "    pass")
    assert exempt_body_range(old, new, 4, 4) is None


# -- refuses: decorators, the main realistic risk vector ---------------------


def test_a_decorator_on_the_old_side_refuses():
    old = src(
        "@app.route('/x')",
        "def handler(x: int) -> str:",
        "    return str(x)",
    )
    new = src(
        "@app.route('/x')",
        "def handler(x: float) -> str:",
        "    return str(x)",
    )
    assert exempt_body_range(old, new, 5, 5) is None


def test_a_decorator_added_on_the_new_side_refuses():
    old = src("def handler(x: int) -> str:", "    return str(x)")
    new = src(
        "@validate_arguments",
        "def handler(x: float) -> str:",
        "    return str(x)",
    )
    assert exempt_body_range(old, new, 4, 5) is None


def test_a_decorator_separated_by_a_blank_line_still_counts():
    old = src(
        "@app.route('/x')",
        "",
        "def handler(x: int) -> str:",
        "    return str(x)",
    )
    new = src(
        "@app.route('/x')",
        "",
        "def handler(x: float) -> str:",
        "    return str(x)",
    )
    assert exempt_body_range(old, new, 6, 6) is None


# -- refuses: class headers, out of scope entirely ---------------------------


def test_a_class_header_is_never_exempted():
    old = src("class Widget(Base):", "    pass")
    new = src("class Widget(Base, Generic[T]):", "    pass")
    assert exempt_body_range(old, new, 4, 4) is None


# -- refuses: multi-line signatures, missing __future__, unparseable ---------


def test_a_signature_that_does_not_close_on_this_line_refuses():
    old = "from __future__ import annotations\n\n\ndef f(\n    a: int,\n) -> int:\n    return a\n"
    new = "from __future__ import annotations\n\n\ndef f(\n    a: float,\n) -> int:\n    return a\n"
    assert exempt_body_range(old, new, 5, 5) is None


def test_missing_future_annotations_import_refuses():
    old = "def f(a: int) -> int:\n    return a\n"
    new = "def f(a: float) -> int:\n    return a\n"
    assert exempt_body_range(old, new, 1, 1) is None


def test_future_annotations_missing_on_only_one_side_refuses():
    old = "def f(a: int) -> int:\n    return a\n"
    new = src("def f(a: float) -> int:", "    return a")
    assert exempt_body_range(old, new, 1, 4) is None


def test_unparseable_source_refuses_rather_than_raising():
    old = src("def f(a: int) -> int:", "    return a")
    broken = "not python at all {{{"
    assert exempt_body_range(old, broken, 4, 1) is None
    assert exempt_body_range(broken, old, 1, 4) is None


def test_a_non_def_line_refuses():
    old = src("x: int = 1", "y: int = 2")
    new = src("x: float = 1", "y: int = 2")
    assert exempt_body_range(old, new, 4, 4) is None


def test_out_of_range_lineno_refuses_rather_than_raising():
    old = src("def f(a: int) -> int:", "    return a")
    new = src("def f(a: float) -> int:", "    return a")
    assert exempt_body_range(old, new, 999, 4) is None
    assert exempt_body_range(old, new, 4, 999) is None


# -- the body range itself ----------------------------------------------------


def test_body_range_covers_a_multi_line_body():
    old = src(
        "def f(a: int) -> int:",
        "    b = a + 1",
        "    c = b * 2",
        "    return c",
    )
    new = src(
        "def f(a: float) -> float:",
        "    b = a + 1",
        "    c = b * 2",
        "    return c",
    )
    assert exempt_body_range(old, new, 4, 4) == (5, 7)


def test_body_range_includes_nested_defs():
    """A superset is the safe direction: any test that covers a line inside
    the nested def is still a test that covers the outer function's body."""
    old = src(
        "def outer(a: int) -> int:",
        "    def inner():",
        "        return a",
        "    return inner()",
    )
    new = src(
        "def outer(a: float) -> float:",
        "    def inner():",
        "        return a",
        "    return inner()",
    )
    assert exempt_body_range(old, new, 4, 4) == (5, 7)


def test_a_one_line_body_still_produces_a_valid_range():
    old = src("def f(a: int) -> int:", "    return a")
    new = src("def f(a: float) -> float:", "    return a")
    assert exempt_body_range(old, new, 4, 4) == (5, 5)
