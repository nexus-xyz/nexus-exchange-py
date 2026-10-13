#!/usr/bin/env python3
"""Tests for public_surface.py's guards and rendering (ENG-18798). Stdlib only.

No network and no installed SDK: every module listed here is built in the test, and main() is
pointed at those through sys.modules. public_api.sh runs this file with the interpreter it lists
the surface with, before it lists it, so a broken lister fails as a broken lister.

Run: python3 scripts/release_gate/test_public_surface.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import sysconfig
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
# By path, not by import: public_api.sh runs this with `python -I`, which leaves this
# directory off sys.path.
spec = importlib.util.spec_from_file_location("public_surface", HERE / "public_surface.py")
assert spec is not None and spec.loader is not None
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

PACKAGE = "fakepkg"


def module(name: str, source: str, *, file: str | None = None) -> types.ModuleType:
    """A module named `name` holding what `source` defines, as if imported from `file`."""
    mod = types.ModuleType(name)
    mod.__file__ = file or str(Path(sysconfig.get_paths()["purelib"]) / PACKAGE / "__init__.py")
    # A real-looking filename: dataclass and `exec` code says `<string>`, which from_source
    # skips, and these stand in for the package's own source.
    exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    return mod


@contextlib.contextmanager
def lister(*extra: str):
    """public_surface.py pointed at the fake package, on this interpreter's version."""
    with (
        mock.patch.object(S, "PACKAGE", PACKAGE),
        mock.patch.object(S, "MODULES", (PACKAGE, *extra)),
        mock.patch.object(S, "SNAPSHOT_PYTHON", sys.version_info[:2]),
    ):
        yield


def run_main() -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = S.main()
    return code, out.getvalue(), err.getvalue()


class Refusals(unittest.TestCase):
    def test_another_python_minor_is_refused_with_exit_2(self):
        other = (sys.version_info[0], sys.version_info[1] + 1)
        with mock.patch.object(S, "SNAPSHOT_PYTHON", other):
            code, out, err = run_main()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(f"run with Python {other[0]}.{other[1]}", err)

    def test_a_package_not_from_this_venv_is_refused_with_exit_2(self):
        # What `python3 public_surface.py` from the checkout would import: the source tree.
        fake = module(PACKAGE, "__all__ = []", file=str(HERE / "src" / PACKAGE / "__init__.py"))
        with lister(), mock.patch.dict(sys.modules, {PACKAGE: fake}):
            code, out, err = run_main()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("not from this venv's site-packages", err)


class Guards(unittest.TestCase):
    def test_a_listed_module_that_fails_to_import_is_one_missing_line(self):
        fake = module(PACKAGE, "__all__ = ['LIMIT']\nLIMIT = 5")
        # None in sys.modules makes import_module raise ImportError, as a deleted module would.
        with (
            lister(f"{PACKAGE}.gone"),
            mock.patch.dict(sys.modules, {PACKAGE: fake, f"{PACKAGE}.gone": None}),
        ):
            code, out, _ = run_main()
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn(f"{PACKAGE}.LIMIT constant: int = 5", lines)
        missing = [line for line in lines if line.startswith(f"{PACKAGE}.gone module MISSING: ")]
        self.assertEqual(len(missing), 1, out)

    def test_a_module_without_all_is_one_line_not_a_traceback(self):
        fake = module(PACKAGE, "LIMIT = 5")
        self.assertEqual(
            S.surface([fake]), [f"{PACKAGE} module has NO __all__, so none of its names are listed"]
        )

    def test_a_name_in_all_that_is_not_defined_is_one_missing_line(self):
        fake = module(PACKAGE, "__all__ = ['GONE']")
        self.assertEqual(S.surface([fake]), [f"{PACKAGE}.GONE MISSING: in __all__ but not defined"])

    def test_a_duplicate_in_all_is_flagged(self):
        fake = module(PACKAGE, "__all__ = ['LIMIT', 'LIMIT']\nLIMIT = 5")
        self.assertIn(f"{PACKAGE}.__all__ has duplicate names", S.surface([fake]))

    def test_a_property_whose_getter_has_no_signature_lists_as_unknown(self):
        source = (
            "def getter(self):\n"
            "    return 1\n"
            "getter.__signature__ = 'not a Signature'  # inspect.signature raises TypeError\n"
            "class Thing:\n"
            "    size = property(getter)\n"
            "__all__ = ['Thing']\n"
        )
        with lister():
            lines = S.surface([module(PACKAGE, source)])
        self.assertIn(f"{PACKAGE}.Thing.size property: ?", lines)


class Values(unittest.TestCase):
    def test_constants_and_class_attributes_list_their_value(self):
        source = (
            "LIMIT_MAX = 1000\n"
            "NAME = 'nexus'\n"
            "class Error(Exception):\n"
            "    transient = False\n"
            "__all__ = ['LIMIT_MAX', 'NAME', 'Error']\n"
        )
        with lister():
            lines = S.surface([module(PACKAGE, source)])
        self.assertIn(f"{PACKAGE}.LIMIT_MAX constant: int = 1000", lines)
        self.assertIn(f"{PACKAGE}.NAME constant: str = 'nexus'", lines)
        self.assertIn(f"{PACKAGE}.Error.transient attribute: bool = False", lines)

    def test_values_that_move_every_release_are_listed_by_type_only(self):
        source = (
            "__version__ = '1.2.3'\n"
            "DEFAULT_USER_AGENT = 'fakepkg/1.2.3'\n"
            "DEFAULT_API_VERSION = 'v0.8.1'\n"
            "__all__ = ['__version__', 'DEFAULT_USER_AGENT', 'DEFAULT_API_VERSION']\n"
        )
        self.assertEqual(
            S.surface([module(PACKAGE, source)]),
            [
                f"{PACKAGE}.DEFAULT_API_VERSION constant: str",
                f"{PACKAGE}.DEFAULT_USER_AGENT constant: str",
                f"{PACKAGE}.__version__ constant: str",
            ],
        )

    def test_sets_and_dicts_render_sorted_whatever_the_hash_seed(self):
        # String hashing is randomised per process, so iteration order is not the same twice.
        self.assertEqual(
            S.rendered(frozenset({"trades", "book", "candles"})),
            "frozenset({'book', 'candles', 'trades'})",
        )
        self.assertEqual(S.rendered({"b", "a"}), "{'a', 'b'}")
        self.assertEqual(S.rendered(frozenset()), "frozenset()")
        self.assertEqual(S.rendered(set()), "set()")
        self.assertEqual(
            S.rendered({"z": frozenset({"y", "x"}), "a": [2, 1], "m": ("one",)}),
            "{'a': [2, 1], 'm': ('one',), 'z': frozenset({'x', 'y'})}",
        )

    def test_an_object_renders_without_its_memory_address(self):
        text = S.rendered(object())
        self.assertEqual(text, "<object object>")

    def test_dropping_a_set_member_moves_the_line(self):
        before = module(PACKAGE, "CHANNELS = frozenset({'a', 'b'})\n__all__ = ['CHANNELS']")
        after = module(PACKAGE, "CHANNELS = frozenset({'a'})\n__all__ = ['CHANNELS']")
        self.assertNotEqual(S.surface([before]), S.surface([after]))

    def test_a_very_long_value_is_listed_by_digest(self):
        fake = module(PACKAGE, f"BLOB = {'x' * 500!r}\n__all__ = ['BLOB']")
        [line] = S.surface([fake])
        self.assertRegex(
            line, rf"^{PACKAGE}\.BLOB constant: str = <502 chars, sha256 [0-9a-f]{{64}}>$"
        )


class Bases(unittest.TestCase):
    def test_a_private_base_class_is_named_on_the_public_class(self):
        # Documents the current behaviour (review P10): the class line names a private base as
        # it names any other, and the base's members are listed on the public class.
        source = (
            "class _Core:\n"
            "    def ping(self) -> None: ...\n"
            "class Client(_Core):\n"
            "    pass\n"
            "__all__ = ['Client']\n"
        )
        with lister():
            lines = S.surface([module(PACKAGE, source)])
        self.assertIn(f"{PACKAGE}.Client class(_Core)", lines)
        self.assertIn(f"{PACKAGE}.Client.ping method(self) -> None", lines)


if __name__ == "__main__":
    unittest.main()
