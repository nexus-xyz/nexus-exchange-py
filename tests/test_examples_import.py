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

from nexus_exchange import Funds, Network

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


def _readme_catalog_names() -> set[str]:
    """The `Example` column of the `## Programs` table in `examples/README.md`.

    Reads the table itself rather than a hand-maintained list, so this stays a
    pin against the directory and not another copy that can drift on its own.
    """
    text = (EXAMPLES_DIR / "README.md").read_text()
    _, _, section = text.partition("## Programs")
    names = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        first = cells[0] if cells else ""
        if first.startswith("`") and first.endswith("`"):
            names.add(first.strip("`"))
    return names


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


def test_signed_examples_refuse_a_real_funds_base_url_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`NEXUS_BASE_URL` must not walk past the refusal the enum check performs.

    ENG-4107 review (bitfalt, P1). The network check reads the *label*, while
    `NEXUS_BASE_URL` decides the *destination*, so this exact invocation -- the one
    from the review, with no `NEXUS_NETWORK` at all -- resolved to `Network.LOCAL`,
    satisfied the real-funds guard, and pointed a live order at the mainnet host.

    Note this cannot be caught by the sibling test above: that one deletes
    `NEXUS_BASE_URL`, which is precisely the path that was unguarded.
    """
    monkeypatch.setenv("NEXUS_API_KEY", "nx_test")
    monkeypatch.setenv("NEXUS_API_SECRET", "00" * 32)
    monkeypatch.setenv("NEXUS_BASE_URL", "https://api.nexus.xyz/v1")
    monkeypatch.delenv("NEXUS_NETWORK", raising=False)
    shared = _shared_module()

    with pytest.raises(SystemExit) as exc:
        shared.make_signed_client()
    assert exc.value.code == 2


def test_signed_examples_refuse_an_unrecognised_base_url_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard fails closed, rather than deny-listing today's mainnet host.

    `Network.MAINNET.base_url` is still `None` pending DNS, so there is no
    real-funds URL to deny-list -- which is why an allowlist is the only shape that
    can work here. An unknown host must therefore be refused, not permitted.
    """
    monkeypatch.setenv("NEXUS_API_KEY", "nx_test")
    monkeypatch.setenv("NEXUS_API_SECRET", "00" * 32)
    monkeypatch.setenv("NEXUS_BASE_URL", "https://exchange.example.invalid/api/exchange")
    monkeypatch.delenv("NEXUS_NETWORK", raising=False)
    shared = _shared_module()

    with pytest.raises(SystemExit) as exc:
        shared.make_signed_client()
    assert exc.value.code == 2


def test_the_loopback_allowlist_does_not_admit_an_unparseable_host() -> None:
    """`""` must not be a member of the play-funds host allowlist.

    PR #18 review (@Luc-Campos): `urlparse(base_url).hostname` falls back to
    `""` in `make_signed_client` for a `NEXUS_BASE_URL` the guard cannot pull a
    host out of, and `_LOOPBACK_HOSTS` used to include `""` -- so that value
    would satisfy `host in allowed` and pass the allowlist check that exists to
    refuse exactly what it does not recognise.

    Asserted directly against the allowlist rather than through
    `make_signed_client()`: every URL that makes `urlparse(...).hostname` empty
    also has an empty ``netloc``, which `Client`'s own `_clean_base_url`
    independently rejects before this guard's answer would matter (confirmed by
    hand: `NEXUS_BASE_URL=http:///no/host` raises there, not here). Routing the
    test through `make_signed_client()` would therefore pass today regardless of
    whether this allowlist itself was fixed -- it would be exercising the
    *other* guard and calling it coverage of this one. Testing the allowlist's
    own data is what actually pins the gap Luc flagged, independent of whatever
    a caller upstream happens to also block.
    """
    shared = _shared_module()
    assert "" not in shared._LOOPBACK_HOSTS
    assert "" not in shared._play_funds_hosts()


def test_signed_examples_allow_loopback_base_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The converse of the two guards above, so neither passes by refusing everything.

    Loopback is the invocation this module's docstring puts in front of the reader
    (`NEXUS_BASE_URL=http://localhost:9090`), so a regression here breaks the
    documented path.
    """
    monkeypatch.setenv("NEXUS_API_KEY", "nx_test")
    monkeypatch.setenv("NEXUS_API_SECRET", "00" * 32)
    monkeypatch.delenv("NEXUS_NETWORK", raising=False)
    shared = _shared_module()

    for url in ("http://localhost:9090", "http://127.0.0.1:9090"):
        monkeypatch.setenv("NEXUS_BASE_URL", url)
        with shared.make_signed_client() as client:
            assert client is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://beta.exchange.nexus.xyz/api/exchange",
        "https://exchange.nexus.xyz/api/exchange",
    ],
)
def test_gateway_style_urls_pass_the_guard(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gateway-style hosts must not be refused as real-funds targets.

    Both URLs are play-funds: the first is what `beta` became (this module's
    docstring), the second is `Network.TESTNET.config.base_url` itself. Neither may
    be mistaken for mainnet.

    This asserted a `ValueError` rather than a working client until #62. `_shared.py`
    passes only `base_url`, `Client` fell `direct_base_url` back to it, and a direct
    base containing `/api/exchange` was rejected -- so *any* gateway-style
    `NEXUS_BASE_URL` raised, the SDK's own testnet default included (ENG-10095).
    Pointing testnet's direct base at the gateway-mounted `/api/v1` ended that, so
    the assertion is now the property the docstring always wanted: the guard admits
    these and a client is built.

    If a change ever starts treating these hosts as real-funds -- or as funds it
    cannot vouch for, which now refuses too -- this fails on the guard's
    `SystemExit(2)` instead.
    """
    monkeypatch.setenv("NEXUS_API_KEY", "nx_test")
    monkeypatch.setenv("NEXUS_API_SECRET", "00" * 32)
    monkeypatch.setenv("NEXUS_BASE_URL", url)
    monkeypatch.delenv("NEXUS_NETWORK", raising=False)
    shared = _shared_module()

    with shared.make_signed_client() as client:
        assert client is not None


def test_the_signed_guard_tests_play_positively_not_real_negatively() -> None:
    """`funds is not Funds.PLAY`, never `funds is Funds.REAL` (ENG-9826, #60).

    The two forms are indistinguishable across today's built-ins -- MAINNET is
    REAL, TESTNET and LOCAL are PLAY, and none is UNKNOWN -- so no runtime test
    in this file can tell them apart. That is exactly why this one reads the
    source. The difference appears the moment a target declares nothing, which
    `NetworkConfig.custom(funds=Funds.UNKNOWN)` already permits and a future
    network could: the negated form reads undeclared funds as safe and lets a
    signed example place live orders against it, which is the inversion the
    `Funds` docstring calls out by name.

    Both sites are pinned -- the refusal in `make_signed_client` and the
    allowlist in `_play_funds_hosts` -- because a host allowlist built by
    negating REAL admits an undeclared target just as quietly.
    """
    source = (EXAMPLES_DIR / "_shared.py").read_text(encoding="utf-8")
    assert source.count("is not Funds.PLAY") == 2, (
        "both the refusal and the host allowlist must test PLAY positively"
    )
    # A trailing colon makes this the control-flow form; the ternary that picks
    # the refusal *message* legitimately asks whether funds are REAL.
    assert "if network.funds is Funds.REAL:" not in source


def test_signed_examples_allow_the_play_funds_networks(
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


def test_the_docs_do_not_sell_a_bare_base_url_as_a_target_selector() -> None:
    """ENG-10095: a URL says nothing about whose money is behind it.

    Both docs used to present `NEXUS_BASE_URL` as the thing that "overrides
    NEXUS_NETWORK", with `https://beta.exchange.nexus.xyz/api/exchange` as the
    way to reach that deploy. Two things went stale under it. The `Custom`
    network (ENG-9823/9826) made a declared-funds config the documented way to
    name a deployment, and ENG-10955 deprecated the bare `base_url` *selector*
    in favour of it -- so a URL offered as the way to pick a target now teaches
    the shortcut that resolves to `Funds.UNKNOWN`.

    It was also not what these factories do: both pass the override alongside a
    named network, which keeps that network's funds. So the old wording named a
    behaviour the code does not have, in the direction that reads as safer than
    it is -- see `test_a_base_url_override_keeps_the_named_networks_funds` for
    the semantics the replacement claims.
    """
    for doc in (EXAMPLES_DIR / "README.md", EXAMPLES_DIR / "_shared.py"):
        flat = doc.read_text(encoding="utf-8").lower().replace("`", "")
        assert "overrides nexus_network" not in flat, (
            f"{doc.name}: NEXUS_BASE_URL is a modifier on the named network, not a "
            f"selector that overrides it -- the funds stay the network's (ENG-10095)"
        )
        assert "networkconfig.custom" in flat, (
            f"{doc.name}: should point at NetworkConfig.custom() for a deployment "
            f"whose funds have to be declared, not at a bare URL (ENG-10955)"
        )


def test_a_base_url_override_keeps_the_named_networks_funds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The semantics the docs now claim, pinned so the prose cannot drift off them.

    ENG-10095. `make_client` passes `NEXUS_BASE_URL` *with* `Network.TESTNET`, so
    only the send target moves -- the config keeps testnet's funds, faucet and
    signing domain. The deprecated bare selector (a URL and no network) would
    instead resolve to `Funds.UNKNOWN`, and that difference is the whole point of
    the section: an override cannot make the guardrails match the far end.

    Asserted on `_base_url` because the override lands on the client, not on the
    config -- `client.network` is testnet entire, which is exactly the surprise
    worth pinning.
    """
    monkeypatch.delenv("NEXUS_NETWORK", raising=False)
    monkeypatch.setenv("NEXUS_BASE_URL", "http://localhost:9090")
    shared = _shared_module()

    with shared.make_client() as client:
        assert client._base_url == "http://localhost:9090"
        assert client.network.funds is Funds.PLAY
        assert client.network.label == Network.TESTNET.config.label


def test_the_readme_catalog_matches_the_examples_directory() -> None:
    """The catalog half of the same drift class as the README-network-names test.

    #69 deleted `examples/health_check.py`; the merge was clean and nothing
    imported or lint-checked the README's `## Programs` table, so the stale
    `health_check.py` row survived undetected (PR #18 review, @Luc-Campos) --
    the same three structurally-blind guards this module's docstring already
    describes, just aimed at the catalog instead of a helper's attribute.
    `paginate_fills.py` had *also* drifted out of the table by the time this
    landed, with nothing to catch that either.

    So: pin the table to the directory itself, not to today's file list. A
    fixed set of expected names would only re-encode the same drift risk one
    level up -- add a tenth example and this test would need editing right
    alongside the one it exists to make unnecessary.
    """
    documented = _readme_catalog_names()
    on_disk = {p.name for p in _example_paths()}
    assert documented == on_disk, (
        "examples/README.md's Programs table has drifted from examples/: "
        f"documented but missing from disk={sorted(documented - on_disk)}, "
        f"on disk but undocumented={sorted(on_disk - documented)}"
    )


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
