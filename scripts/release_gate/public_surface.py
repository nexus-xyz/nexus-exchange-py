#!/usr/bin/env python3
"""List the public surface of the INSTALLED ``nexus_exchange`` (ENG-18798).

``scripts/release_gate/public_api.sh`` builds the wheel the way ``release.yml`` does, installs it
into a clean venv, and runs this file there with ``python -I``. It refuses to run against anything
but that install, so the listing is what a ``pip install nexus-exchange`` user gets, not the source
tree.

The surface is the ``__all__`` of each module in ``MODULES``: the package itself, and
``nexus_exchange.ccxt_adapter``, which the README tells users to import from directly. One sorted
line per item, ``<path> <kind><detail>``:

* every name with its kind (class, dataclass, enum, exception, function, type alias,
  constant);
* a class's constructor signature, and its public members: dataclass fields, enum members,
  methods, properties, class attributes, and the attributes its ``__init__`` assigns on
  ``self``. Members a package base class defines are listed on the subclass too, so a change to
  ``ApiError`` also shows on ``RestrictedJurisdictionError``;
* a constant's type, not its value: ``__version__`` and ``DEFAULT_USER_AGENT`` change on every
  release, so listing values would fail every release PR.

So a reshaped type fails the check, not only a dropped name.

Rendering depends on the Python minor version (how ``inspect`` formats a signature, which
dunders a class carries), so the snapshot is generated with one pinned version, the one
``.github/workflows/pre-publish.yml`` installs. They move together. Stdlib only.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import functools
import importlib
import inspect
import re
import sys
import sysconfig
import textwrap
import typing
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PACKAGE = "nexus_exchange"
# Every module a user is told to import from. A public module missing here is invisible to the
# check: deleting it would pass (ENG-18798 review).
MODULES = (PACKAGE, f"{PACKAGE}.ccxt_adapter")
SNAPSHOT_PYTHON = (3, 12)

# A default whose repr carries a memory address (a sentinel `object()`) differs on every run.
ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")

# Hand-written dunders are listed (`__enter__` is why `with Client()` works), except these:
# `__init__` is the constructor line instead, and the rest are hooks the class machinery calls,
# not something a caller does. Generated dunders are skipped by `from_source`.
SKIPPED_DUNDERS = {"__init__", "__post_init__", "__init_subclass__", "__class_getitem__"}


class _Text:
    """Renders as the text it holds, so `inspect` formats a signature with our strings."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


def annotation(value: Any) -> str:
    # Every module uses `from __future__ import annotations`, so these are the source text.
    return value if isinstance(value, str) else inspect.formatannotation(value)


def default(value: Any) -> str:
    return ADDRESS.sub("", repr(value))


def signature(obj: Any) -> str | None:
    """`(params) -> return`, or None for what has no signature (an exception with no `__init__`)."""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    params = [
        p.replace(
            annotation=p.empty if p.annotation is p.empty else _Text(annotation(p.annotation)),
            default=p.empty if p.default is p.empty else _Text(default(p.default)),
        )
        for p in sig.parameters.values()
    ]
    ret = sig.return_annotation
    return str(
        sig.replace(
            parameters=params,
            return_annotation=sig.empty if ret is sig.empty else _Text(annotation(ret)),
        )
    )


def is_ours(obj: Any) -> bool:
    return (getattr(obj, "__module__", None) or "").split(".")[0] == PACKAGE


def type_name(cls: type) -> str:
    if is_ours(cls) or cls.__module__ == "builtins":
        return cls.__qualname__
    return f"{cls.__module__}.{cls.__qualname__}"


def from_source(func: Any) -> bool:
    """Written in this package's source. `dataclass` generates code from `<string>`, and its
    slots helpers live in `dataclasses` itself."""
    code = getattr(inspect.unwrap(func), "__code__", None)
    return is_ours(func) and code is not None and not code.co_filename.startswith("<")


def self_attributes(init: Any) -> dict[str, str]:
    """Public attributes an `__init__` assigns on `self`, with the annotation when it has one."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(init)))
    except (OSError, TypeError, SyntaxError):
        return {}
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        note = ""
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets, note = [node.target], ast.unparse(node.annotation)
        for target in targets:
            for leaf in ast.walk(target):
                if (
                    isinstance(leaf, ast.Attribute)
                    and isinstance(leaf.value, ast.Name)
                    and leaf.value.id == "self"
                    and not leaf.attr.startswith("_")
                ):
                    found.setdefault(leaf.attr, note)
    return found


def member(path: str, cls: type, owner: type, name: str, raw: Any) -> str | None:
    """One line for one attribute of `owner` (a package class in `cls`'s MRO), or None."""
    if isinstance(raw, staticmethod):
        return f"{path}.{name} staticmethod{signature(raw.__func__)}"
    if isinstance(raw, classmethod):
        return f"{path}.{name} classmethod{signature(getattr(cls, name))}"
    if isinstance(raw, (property, functools.cached_property)):
        getter = raw.fget if isinstance(raw, property) else raw.func
        kind = type(raw).__name__
        if isinstance(raw, property) and raw.fset is not None:
            kind += "(settable)"
        ret = inspect.signature(getter).return_annotation
        return f"{path}.{name} {kind}: {'?' if ret is inspect.Signature.empty else annotation(ret)}"
    if inspect.isfunction(raw):
        if name.startswith("__") and not from_source(raw):
            return None
        kind = "async method" if inspect.iscoroutinefunction(raw) else "method"
        if inspect.isasyncgenfunction(raw):
            kind = "async generator method"
        return f"{path}.{name} {kind}{signature(raw)}"
    if name.startswith("_"):
        return None
    if isinstance(raw, type):
        return f"{path}.{name} nested class"
    notes = owner.__dict__.get("__annotations__", {})
    kind = annotation(notes[name]) if name in notes else type(raw).__name__
    return f"{path}.{name} attribute: {kind}"


def class_lines(path: str, cls: type) -> Iterator[str]:
    bases = ", ".join(type_name(b) for b in cls.__bases__ if b is not object)
    if issubclass(cls, enum.Enum):
        kind = "enum"
    elif dataclasses.is_dataclass(cls):
        kind = "dataclass(frozen)" if cls.__dataclass_params__.frozen else "dataclass"
    elif issubclass(cls, BaseException):
        kind = "exception"
    else:
        kind = "class"
    yield f"{path} {kind}" + (f"({bases})" if bases else "")

    seen: set[str] = set()
    if issubclass(cls, enum.Enum):
        for member_name, value in cls.__members__.items():
            seen.add(member_name)
            yield f"{path}.{member_name} member = {value.value!r}"
    elif (constructor := signature(cls)) is not None:
        yield f"{path}{constructor}"
    if dataclasses.is_dataclass(cls):
        for field in dataclasses.fields(cls):
            seen.add(field.name)
            value = ""
            if field.default is not dataclasses.MISSING:
                value = f" = {default(field.default)}"
            elif field.default_factory is not dataclasses.MISSING:
                value = " = <factory>"
            yield f"{path}.{field.name} field: {annotation(field.type)}{value}"

    # Nearest definition wins, as attribute lookup does. Package classes only: what `str`,
    # `Enum` or `Exception` bring is theirs, and differs between Python versions.
    ours = [klass for klass in cls.__mro__ if is_ours(klass)]
    for klass in ours:
        for attr, raw in vars(klass).items():
            if attr in seen or attr in SKIPPED_DUNDERS:
                continue
            if attr.startswith("_") and not (attr.startswith("__") and attr.endswith("__")):
                continue
            seen.add(attr)
            line = member(path, cls, klass, attr, raw)
            if line:
                yield line
        for attr, note in klass.__dict__.get("__annotations__", {}).items():
            if attr not in seen and not attr.startswith("_") and "ClassVar" not in str(note):
                seen.add(attr)
                yield f"{path}.{attr} attribute: {annotation(note)}"
    for klass in ours:
        if "__init__" in vars(klass) and from_source(vars(klass)["__init__"]):
            for attr, note in sorted(self_attributes(vars(klass)["__init__"]).items()):
                if attr not in seen:
                    seen.add(attr)
                    yield f"{path}.{attr} attribute{': ' + note if note else ''} (set in __init__)"


def surface(modules: list[Any]) -> list[str]:
    lines: list[str] = []
    for module in modules:
        prefix = module.__name__
        for name in module.__all__:
            value = getattr(module, name)
            path = f"{prefix}.{name}"
            if isinstance(value, type):
                lines.extend(class_lines(path, value))
            elif inspect.isfunction(value):
                kind = "async function" if inspect.iscoroutinefunction(value) else "function"
                lines.append(f"{path} {kind}{signature(value)}")
            elif typing.get_origin(value) is not None:
                # `WsHealth = Literal[...]`: the alias's members are the API, not its typing class.
                lines.append(f"{path} type alias = {default(value)}")
            else:
                lines.append(f"{path} constant: {type(value).__name__}")
        if len(set(module.__all__)) != len(module.__all__):
            lines.append(f"{prefix}.__all__ has duplicate names")
    return sorted(lines)


def main() -> int:
    if sys.version_info[:2] != SNAPSHOT_PYTHON:
        want = ".".join(map(str, SNAPSHOT_PYTHON))
        print(
            f"public_surface.py: run with Python {want}, the version the snapshot is generated "
            f"with (this is {sys.version.split()[0]}). Point PYTHON at a {want} interpreter.",
            file=sys.stderr,
        )
        return 2
    module = importlib.import_module(PACKAGE)
    installed = Path(sysconfig.get_paths()["purelib"]).resolve()
    origin = Path(module.__file__ or "").resolve()
    if not origin.is_relative_to(installed):
        print(
            f"public_surface.py: {PACKAGE} imports from {origin}, not from this venv's "
            f"site-packages ({installed}). Run it through public_api.sh, which installs the "
            "built wheel.",
            file=sys.stderr,
        )
        return 2
    # A listed module that no longer imports is a removal like any other: its lines drop out of
    # the diff and one MISSING line says why, instead of the run dying on a traceback.
    modules, missing = [module], []
    for name in MODULES[1:]:
        try:
            modules.append(importlib.import_module(name))
        except ImportError as err:
            missing.append(f"{name} module MISSING: {err}")
    lines = sorted(surface(modules) + missing)
    sys.stdout.write("".join(line + "\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
