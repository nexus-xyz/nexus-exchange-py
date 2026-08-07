"""The examples are code, so CI has to run them at least far enough to import.

ENG-4107. `examples/_shared.py` referenced `Network.STABLE` for days after
ENG-6454 deleted it, so every example raised `AttributeError` on its first
client. Nothing caught it, and the interesting part is *why not* — three separate
guards were all structurally incapable of seeing it (@Luc-Campos in review):

  * `git merge origin/main` reported **zero conflicts**. `_shared.py` was a new
    file on this branch, so there was no competing side for git to flag.
  * **No test imported `examples/`**, so the suite stayed green at 258 passed.
  * `ruff` was clean and `mypy` is pointed at `src`, so neither type-checked the
    directory at all.

A green suite plus a clean lint plus a conflict-free merge is normally strong
evidence. Here it was no evidence, because the examples were outside all three.

**Which test catches what, since the split is not obvious.** Verified by
restoring the bug and watching what went red:

  * The *import* tests do **not** catch it. `Network.STABLE.value` sits inside
    `make_client`'s body, so importing the module never evaluates it. They catch a
    different class — a module-level name that no longer exists, a renamed import,
    a syntax error — which is worth having but is not this.
  * `test_make_client_resolves_the_default_network` and
    `test_a_retired_network_name_reports_the_valid_set` are what actually fail on
    it, because they *call* the helpers.

So the load-bearing half is exercising the helpers, not importing the files. Both
are here; the distinction is recorded so nobody deletes the calling tests
believing the import sweep covers them.

Deliberately no example is *run* to completion. That means network I/O,
credentials and placed orders. Calling the client factories is the line: it
reaches every name resolved at call time without sending a request.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nexus_exchange import Network

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(autouse=True)
def _examples_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put `examples/` on `sys.path`, which is what running an example does.

    The programs use a sibling `import _shared`, and `python examples/foo.py` puts
    the script's own directory on `sys.path` — so this reproduces the real
    invocation rather than changing the examples to suit the test. Deliberately
    NOT solved by adding `examples/__init__.py`: that would make them a package
    and break the documented `python examples/foo.py` form.
    """
    monkeypatch.syspath_prepend(str(EXAMPLES_DIR))


def _load(path: Path) -> ModuleType:
    """Import a module by file path, with its directory already on `sys.path`."""
    spec = importlib.util.spec_from_file_location(f"_example_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _shared_module() -> ModuleType:
    """`examples/_shared.py`, loaded by path — `examples/` is not a package."""
    return _load(EXAMPLES_DIR / "_shared.py")


def _example_paths() -> list[Path]:
    """Every runnable example, excluding the shared helper module itself."""
    return sorted(p for p in EXAMPLES_DIR.glob("*.py") if p.name != "_shared.py")


def test_the_examples_directory_is_actually_populated() -> None:
    """Guard the guard: a glob that matches nothing would make every test below vacuous."""
    paths = _example_paths()
    assert len(paths) >= 4, f"expected several examples, found {[p.name for p in paths]}"


@pytest.mark.parametrize("path", _example_paths(), ids=lambda p: p.name)
def test_example_module_imports(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing must not raise — no `main()` call, so no I/O.

    `sys.argv` is neutralised because a module reading it at import time would
    otherwise see pytest's own arguments.
    """
    monkeypatch.setattr("sys.argv", [path.name])
    _load(path)


def test_make_client_resolves_the_default_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact failure: the unsigned default must name a Network that exists."""
    monkeypatch.delenv("NEXUS_NETWORK", raising=False)
    monkeypatch.delenv("NEXUS_BASE_URL", raising=False)
    shared = _shared_module()

    with shared.make_client() as client:
        assert client is not None


def test_signed_examples_refuse_a_real_funds_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NEXUS_NETWORK=mainnet` must not reach a trading example.

    The `LOCAL` default is the first line of defence and this env var walks past
    it. Latently safe today only because `Network.MAINNET.base_url` is `None` and
    construction happens to raise — an accident of DNS, not a control, and it
    disappears when DNS lands. So the guard is asserted directly.
    """
    monkeypatch.setenv("NEXUS_API_KEY", "nx_test")
    monkeypatch.setenv("NEXUS_API_SECRET", "00" * 32)
    monkeypatch.setenv("NEXUS_NETWORK", Network.MAINNET.value)
    monkeypatch.delenv("NEXUS_BASE_URL", raising=False)
    shared = _shared_module()

    with pytest.raises(SystemExit) as exc:
        shared.make_signed_client()
    assert exc.value.code == 2


def test_signed_examples_allow_the_non_real_funds_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The converse, so the guard above cannot pass by refusing everything."""
    monkeypatch.setenv("NEXUS_API_KEY", "nx_test")
    monkeypatch.setenv("NEXUS_API_SECRET", "00" * 32)
    monkeypatch.setenv("NEXUS_BASE_URL", "http://localhost:9090")
    shared = _shared_module()

    for network in (Network.LOCAL, Network.TESTNET):
        monkeypatch.setenv("NEXUS_NETWORK", network.value)
        with shared.make_signed_client() as client:
            assert client is not None


def test_a_retired_network_name_reports_the_valid_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`NEXUS_NETWORK=stable` is what a reader of the old README would try."""
    monkeypatch.setenv("NEXUS_NETWORK", "stable")
    monkeypatch.delenv("NEXUS_BASE_URL", raising=False)
    shared = _shared_module()

    with pytest.raises(SystemExit) as exc:
        shared.make_client()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    for name in (n.value for n in Network):
        assert name in err, f"the valid-set hint should name {name}"


def test_the_readme_documents_only_real_network_names() -> None:
    """The docs half of the same defect — retired vocabulary is its own bug.

    A README naming `stable` sends a reader to a `SystemExit`, and the docstring
    in `_shared.py` had the same list. Asserting on the docs is what stops them
    drifting from the enum again.
    """
    for doc in (EXAMPLES_DIR / "README.md", EXAMPLES_DIR / "_shared.py"):
        text = doc.read_text()
        marker = "NEXUS_NETWORK" if doc.suffix == ".py" else "`NEXUS_NETWORK`"
        assert marker in text, f"{doc.name} should document NEXUS_NETWORK"
        # The line documenting the variable must not offer a retired channel as a
        # value. `beta` is allowed to APPEAR — both files explain what it became —
        # so this checks the documented value list, not the whole file.
        for line in text.splitlines():
            defines = line.strip().startswith(("| `NEXUS_NETWORK`", "NEXUS_NETWORK "))
            if defines:
                assert "stable" not in line.lower(), f"{doc.name}: {line.strip()}"
                assert Network.TESTNET.value in line.lower(), f"{doc.name}: {line.strip()}"


def test_examples_are_not_covered_by_mypy_so_this_file_is_the_substitute() -> None:
    """Records the structural reason these tests exist, and pins it.

    If `mypy` is ever pointed at `examples/`, this file becomes partly redundant
    and that is worth knowing rather than discovering. Reads the config instead of
    asserting a belief about it.
    """
    config = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    if "[tool.mypy]" in config:
        section = config.split("[tool.mypy]", 1)[1].split("\n[", 1)[0]
        assert "examples" not in section, (
            "mypy now appears to cover examples/ — re-check whether the import "
            "tests here are still the only guard, and update this docstring"
        )
    assert os.path.isdir(EXAMPLES_DIR)
