"""The ``Custom`` network target (ENG-9826).

A custom target exists so a deployment this SDK ships no hostname for is still
reachable. That makes it the one config the SDK cannot vouch for, so almost
every test here is about what it refuses.

Three properties carry the weight:

* **Funds are tri-state and required.** ``UNKNOWN`` is a real state — a URL says
  nothing about what is behind it — and it must fail closed everywhere rather
  than read as play money.
* **The label is a key, not a caption.** It namespaces stored credentials, so a
  label that can escape a directory or split a keyring entry lets one target
  address another's secrets.
* **Nothing leaks into the shared map.** A custom target is not addressable by
  name and never joins ``_CONFIGS``; a private host must not become discoverable
  in a public package.

Hosts here are RFC 2606 reserved names — ``example.invalid`` can never resolve,
so nothing in this file can accidentally reach a real deployment.
"""

from __future__ import annotations

import dataclasses
import warnings

import pytest

from nexus_exchange import Client, Funds, Network, NetworkConfig
from nexus_exchange.networks import (
    _CONFIGS,
    _RESERVED_LABELS,
    LEGACY_BASE_URL_LABEL,
)

_BASE = "https://exchange.example.invalid"


def _custom(**overrides: object) -> NetworkConfig:
    """A minimal valid custom config, with per-test overrides."""
    kwargs: dict[str, object] = {
        "label": "dev",
        "funds": Funds.PLAY,
        "base_url": _BASE,
    }
    kwargs.update(overrides)
    return NetworkConfig.custom(**kwargs)  # type: ignore[arg-type]


class TestCustomConfigDrivesTheClient:
    def test_end_to_end_through_the_client(self) -> None:
        config = NetworkConfig.custom(
            label="dev",
            funds=Funds.PLAY,
            base_url=f"{_BASE}/api/exchange",
            direct_base_url=_BASE,
            has_faucet=True,
            chain_id=42,
        )
        with Client(config) as client:
            assert client.network is config
            assert client._base_url == f"{_BASE}/api/exchange"
            assert client._direct_base_url == _BASE
            assert client.network.funds is Funds.PLAY
            assert client.network.has_faucet is True
            assert client.network.signing_domain.chain_id == 42

    def test_direct_base_defaults_to_the_gateway_base(self) -> None:
        with Client(_custom()) as client:
            assert client._base_url == _BASE
            assert client._direct_base_url == _BASE

    def test_a_custom_target_is_accepted_positionally(self) -> None:
        # `network` is the first positional parameter, so a config must work
        # exactly where a `Network` member does.
        with Client(_custom()) as client:
            assert client.network.label == "dev"

    def test_explicit_overrides_still_win_over_the_config(self) -> None:
        # The override path is unchanged for a custom config: the bases it
        # carries are defaults, not a lock.
        with Client(_custom(), base_url=f"{_BASE}:8443") as client:
            assert client._base_url == f"{_BASE}:8443"


class TestFundsAreDeclaredNotGuessed:
    def test_funds_is_required(self) -> None:
        with pytest.raises(TypeError):
            NetworkConfig.custom(label="dev", base_url=_BASE)  # type: ignore[call-arg]

    def test_label_is_required(self) -> None:
        with pytest.raises(TypeError):
            NetworkConfig.custom(funds=Funds.PLAY, base_url=_BASE)  # type: ignore[call-arg]

    @pytest.mark.parametrize("funds", [Funds.REAL, Funds.PLAY, Funds.UNKNOWN])
    def test_every_state_round_trips(self, funds: Funds) -> None:
        assert _custom(funds=funds).funds is funds

    def test_the_string_form_is_normalized_to_a_member(self) -> None:
        # Identity comparisons are the documented guard, and `Funds` subclasses
        # `str` — so a raw "play" left unnormalized would satisfy `== Funds.PLAY`
        # while failing `is Funds.PLAY`, and the guard would silently invert.
        config = _custom(funds="play")
        assert config.funds is Funds.PLAY
        assert type(config.funds) is Funds

    def test_an_unrecognized_funds_value_is_refused(self) -> None:
        with pytest.raises(ValueError, match="funds must be one of"):
            _custom(funds="probably-fine")

    def test_a_bare_string_cannot_be_smuggled_past_the_constructor(self) -> None:
        # `NetworkConfig` is public, so `custom()` cannot be the only gate.
        with pytest.raises(TypeError, match="funds must be a Funds member"):
            NetworkConfig(
                label="dev",
                funds="play",  # type: ignore[arg-type]
                has_faucet=False,
                published_rest_base=_BASE,
                ws_market_data_url="",
                ws_authenticated_url="",
                signing_domain=Network.LOCAL.signing_domain,
                base_url=_BASE,
                direct_base_url=_BASE,
            )

    def test_unknown_funds_is_not_play_funds(self) -> None:
        # The whole point of the tri-state: the documented guard must refuse an
        # undeclared target, and the wrong guard must be visibly wrong.
        config = _custom(funds=Funds.UNKNOWN)
        assert config.funds is not Funds.PLAY  # fails closed, as documented
        assert config.funds is not Funds.REAL  # the negated guard would let it pass


class TestFaucetIsSeparateFromFunds:
    def test_a_faucet_is_absent_until_declared(self) -> None:
        # "Not real money" does not imply "can mint more of it"; assuming one
        # would point `claim_credit` at an endpoint that is not there.
        assert _custom(funds=Funds.PLAY).has_faucet is False

    def test_claim_credit_is_refused_on_a_custom_target_by_default(self) -> None:
        with Client(_custom()) as client, pytest.raises(ValueError, match="has no faucet"):
            client.claim_credit()

    def test_a_declared_faucet_is_honoured(self, httpx_mock) -> None:
        httpx_mock.add_response(
            url=f"{_BASE}/api/v1/account/credit",
            method="POST",
            json={"amount": "100"},
        )
        config = _custom(has_faucet=True)
        with Client(config, api_key="nx", api_secret="ab" * 32) as client:
            assert client.claim_credit().amount is not None

    def test_real_funds_with_a_faucet_is_refused(self) -> None:
        # `claim_credit` gates on `has_faucet` alone, so this combination would
        # aim the faucet helper at a real-funds host.
        with pytest.raises(ValueError, match="cannot exist on a real-funds target"):
            _custom(funds=Funds.REAL, has_faucet=True)

    @pytest.mark.parametrize("bad", ["false", "no", 1, 0, None])
    def test_a_non_boolean_faucet_flag_is_refused(self, bad: object) -> None:
        # `bool("false")` is True. Coercing here would fail *open* on the one
        # flag whose job is to fail closed.
        with pytest.raises(TypeError, match="has_faucet must be True or False"):
            _custom(has_faucet=bad)


class TestLabelIsAKey:
    @pytest.mark.parametrize("label", ["dev", "one-two", "a.b_c", "A1", "x" * 64])
    def test_accepts_a_safe_key(self, label: str) -> None:
        assert _custom(label=label).label == label

    @pytest.mark.parametrize("label", ["  dev  ", "dev\t", "\ndev\n"])
    def test_surrounding_whitespace_is_trimmed_not_rejected(self, label: str) -> None:
        # Invisible, and almost always a copy-paste artefact. Trimming is safe
        # precisely because what is left must then satisfy the charset — the
        # whitespace cannot survive into the stored key either way.
        assert _custom(label=label).label == "dev"

    @pytest.mark.parametrize(
        "label",
        [
            "../other",  # escapes a directory
            "one/two",  # splits a path
            "one:two",  # splits a keyring entry
            "one two",  # whitespace inside
            "dev\nprod",  # embedded newline
            "dev\x00",  # NUL
            "dev\tprod",  # inner tab: survives .strip(), unlike a trailing one
            "café",  # non-ASCII: normalizes ambiguously
            "dev?",  # query-ish
            "*",  # glob
        ],
    )
    def test_rejects_a_label_that_could_address_another_target(self, label: str) -> None:
        with pytest.raises(ValueError, match="must contain only ASCII"):
            _custom(label=label)

    def test_a_trailing_newline_cannot_sneak_past_the_anchors(self) -> None:
        # `$` matches before a trailing newline, so an anchored `^...$` would
        # accept this. The pattern uses `\A`/`\Z` precisely to refuse it — and
        # `.strip()` would hide it, so assert on the inner case too.
        with pytest.raises(ValueError):
            _custom(label="dev\nx")

    @pytest.mark.parametrize("label", [".", ".."])
    def test_rejects_the_directory_entries(self, label: str) -> None:
        with pytest.raises(ValueError, match="does not name a directory entry"):
            _custom(label=label)

    @pytest.mark.parametrize("label", ["", "   "])
    def test_rejects_an_empty_label(self, label: str) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _custom(label=label)

    def test_rejects_an_overlong_label(self) -> None:
        with pytest.raises(ValueError, match="at most 64 characters"):
            _custom(label="x" * 65)

    def test_rejects_a_non_string_label(self) -> None:
        with pytest.raises(TypeError, match="label must be a string"):
            _custom(label=7)

    def test_the_named_configs_satisfy_the_same_rule(self) -> None:
        # The invariant runs on every instance, so the shipped map has to pass it
        # too — otherwise the check is only as good as the path that skips it.
        for config in _CONFIGS.values():
            assert config.label == config.label.strip()
            assert config.label


class TestSigningDomain:
    def test_chain_id_is_unknown_by_default_and_signing_refuses(self) -> None:
        # Same rule the named networks follow: never guess a domain.
        assert _custom().signing_domain.chain_id is None

    def test_a_supplied_chain_id_is_carried(self) -> None:
        assert _custom(chain_id=8453).signing_domain.chain_id == 8453

    def test_the_contract_constants_are_not_overridable(self) -> None:
        # name/version are contract-level and identical on every deployment, so
        # "caller-supplied domain" means chain_id in all five SDKs.
        domain = _custom(chain_id=1).signing_domain
        assert domain.name == Network.LOCAL.signing_domain.name
        assert domain.version == Network.LOCAL.signing_domain.version

    @pytest.mark.parametrize("bad", [0, -1, True, "1", 1.0])
    def test_a_nonsense_chain_id_is_refused_at_construction(self, bad: object) -> None:
        # Caught next to the typo rather than after an order has been built.
        with pytest.raises((TypeError, ValueError), match="chain_id"):
            _custom(chain_id=bad)


class TestBaseUrlValidation:
    def test_a_gateway_path_is_accepted(self) -> None:
        # The topology this whole variant exists to reach (ENG-10095).
        assert _custom(base_url=f"{_BASE}/api/exchange").base_url == f"{_BASE}/api/exchange"

    def test_trailing_slashes_are_trimmed(self) -> None:
        assert _custom(base_url=f"{_BASE}/").base_url == _BASE

    @pytest.mark.parametrize("url", ["", "   ", "/"])
    def test_rejects_an_empty_base(self, url: str) -> None:
        with pytest.raises(ValueError, match="non-empty URL"):
            _custom(base_url=url)

    @pytest.mark.parametrize("url", ["exchange.example.invalid", "ftp://x.invalid", "//x.invalid"])
    def test_rejects_a_missing_or_wrong_scheme(self, url: str) -> None:
        # httpx would otherwise raise at request time, long after construction.
        with pytest.raises(ValueError, match="must start with http"):
            _custom(base_url=url)

    def test_rejects_a_url_with_no_host(self) -> None:
        with pytest.raises(ValueError, match="must include a host"):
            _custom(base_url="https:///path")

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.invalid?a=1",
            "https://x.invalid#f",
            # Empty but present: these parse as *no* query/fragment, so a check on
            # the parsed components would accept them and then build
            # "https://x.invalid/p?/api/v1/orders".
            "https://x.invalid/p?",
            "https://x.invalid/p#",
        ],
    )
    def test_rejects_a_query_or_fragment(self, url: str) -> None:
        # The request path is appended, so this would send *and sign* a mangled
        # URL rather than failing outright.
        with pytest.raises(ValueError, match="query or fragment"):
            _custom(base_url=url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://exchange.example.invalid@evil.invalid",
            "https://user:pass@evil.invalid",
            # A backslash is not a netloc delimiter to `urlsplit`, so this is
            # userinfo too — and it is the form that reads as the real host.
            "https://exchange.example.invalid\\@evil.invalid",
            "https://exchange.example.invalid@evil.invalid/api/exchange",
        ],
    )
    def test_rejects_userinfo(self, url: str) -> None:
        # The only check here aimed at a person rather than a typo: everything
        # before the '@' is credentials, so the host is `evil.invalid` while the
        # string reads as ours — and the client would sign requests and send API
        # keys there. The sibling SDKs reject it too (ENG-9823).
        with pytest.raises(ValueError, match="userinfo"):
            _custom(base_url=url)

    def test_the_direct_base_is_validated_on_its_own_terms(self) -> None:
        with pytest.raises(ValueError, match="direct_base_url"):
            _custom(direct_base_url="not-a-url")

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_direct_base_falls_back_to_the_gateway_base(self, blank: object) -> None:
        # A blank string is "unset" everywhere else in this SDK, so it must not
        # be the one input that raises instead of deferring.
        assert _custom(direct_base_url=blank).direct_base_url == _BASE


class TestARawOverrideIsValidatedTheSameWay:
    """Naming a network keeps its config, so ``custom()`` never sees the URL.

    That made the override path — the only way to reach mainnet — the one place a
    base URL was taken on trust: it was stripped of trailing slashes and used to
    build and sign requests without a scheme, host or userinfo check. The checks
    live in one function so both doors get all of them.
    """

    def test_userinfo_is_refused_on_the_mainnet_override(self) -> None:
        # The highest-value target for this trick: mainnet *requires* an override,
        # moves real money, and the string reads as the published host.
        with pytest.raises(ValueError, match="userinfo"):
            Client(Network.MAINNET, base_url="https://api.nexus.xyz@evil.invalid")

    def test_a_query_is_refused_on_an_override(self) -> None:
        # Previously accepted, then concatenated into
        # "https://x.invalid?a=1/api/v1/markets/summary" and signed.
        with pytest.raises(ValueError, match="query or fragment"):
            Client(Network.LOCAL, base_url="https://x.invalid?a=1")

    def test_a_schemeless_override_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must start with http"):
            Client(Network.LOCAL, base_url="x.invalid")

    def test_the_direct_override_is_validated_too(self) -> None:
        with pytest.raises(ValueError, match="direct_base_url"):
            Client(Network.LOCAL, direct_base_url="https://x.invalid@evil.invalid")

    def test_a_blank_override_still_defers_to_the_default(self) -> None:
        # The ordering that makes the above safe to add: validation runs on what
        # survives the fallback, so "unset" keeps meaning unset rather than
        # becoming the one override that raises.
        with Client(Network.LOCAL, base_url="   ") as client:
            assert client._base_url == Network.LOCAL.config.base_url

    def test_a_named_config_passed_directly_is_validated_at_construction(self) -> None:
        # `NetworkConfig` is public and `__post_init__` does not check URLs, so a
        # raw dataclass can carry an unvalidated base. The client is where it
        # would be signed, so the client is where it must be caught.
        bad = NetworkConfig(
            label="dev",
            funds=Funds.PLAY,
            has_faucet=False,
            published_rest_base=_BASE,
            ws_market_data_url="",
            ws_authenticated_url="",
            signing_domain=Network.LOCAL.signing_domain,
            base_url="https://exchange.example.invalid@evil.invalid",
            direct_base_url=_BASE,
        )
        with pytest.raises(ValueError, match="userinfo"):
            Client(bad)


class TestBareBaseUrlIsSugarForACustomTarget:
    def test_a_lone_base_url_yields_undeclared_funds(self) -> None:
        # Previously this kept whichever network was default, so a client aimed
        # at a real-funds deployment still reported play-funds guardrails.
        with Client(base_url=_BASE) as client:
            assert client.network.funds is Funds.UNKNOWN
            assert client.network.has_faucet is False

    def test_the_sugar_refuses_the_faucet(self) -> None:
        with Client(base_url=_BASE) as client, pytest.raises(ValueError, match="has no faucet"):
            client.claim_credit()

    def test_a_lone_direct_base_url_is_sugar_too(self) -> None:
        with Client(direct_base_url=_BASE) as client:
            assert client.network.funds is Funds.UNKNOWN
            assert client._base_url == _BASE
            assert client._direct_base_url == _BASE

    def test_naming_a_network_alongside_a_url_keeps_its_semantics(self) -> None:
        # The caller declared the network, so the flags are theirs, not a guess.
        with Client(Network.LOCAL, base_url="http://127.0.0.1:8080") as client:
            assert client.network.funds is Funds.PLAY
            assert client.network.has_faucet is True

    def test_no_network_and_no_url_is_still_testnet(self) -> None:
        with Client() as client:
            assert client.network is Network.TESTNET.config

    def test_a_blank_override_is_not_a_custom_target(self) -> None:
        # A blank string is "unset", not a target — it must not tip the client
        # into an undeclared-funds config with no URL to show for it.
        with Client(base_url="   ") as client:
            assert client.network is Network.TESTNET.config

    def test_a_blank_gateway_base_defers_to_a_real_direct_base(self) -> None:
        # The two must not be combined before stripping, or the blank one wins
        # the fallback and the config is built with an empty base.
        with Client(base_url="   ", direct_base_url=_BASE) as client:
            assert client.network.funds is Funds.UNKNOWN
            assert client._base_url == _BASE
            assert client._direct_base_url == _BASE

    def test_the_deprecated_selector_stays_silent_at_runtime(self) -> None:
        # The bare selector is deprecated in the docs only (ENG-10955), so this
        # asserts the decision rather than leaving it to whoever next reads the
        # docstring. Silence here is the *current* state, not the end state: a
        # release that warns has to ship before one that removes the form, and
        # this test is the thing that has to be updated — deliberately, in the
        # PR that adds the warning — to say so.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with Client(base_url=_BASE) as client:
                assert client.network.funds is Funds.UNKNOWN
        # Matched on the warning itself, not on `w.filename`: a `stacklevel=2`
        # warning reports the *caller's* file, so filtering by package path
        # would drop the very warning this test exists to catch. All three
        # deprecation-shaped categories count, so staging the warning as
        # `PendingDeprecationWarning` or `FutureWarning` does not slip past.
        ours = [
            w
            for w in caught
            if issubclass(
                w.category, (DeprecationWarning, PendingDeprecationWarning, FutureWarning)
            )
            and "base_url" in str(w.message)
        ]
        assert ours == [], f"the bare selector warned at runtime: {[str(w.message) for w in ours]}"


class TestCustomTargetsStayOutOfTheSharedMap:
    def test_the_map_is_unchanged_by_building_a_custom_config(self) -> None:
        before = dict(_CONFIGS)
        _custom(label="dev")
        assert dict(_CONFIGS) == before

    def test_a_custom_label_is_not_addressable_by_name(self) -> None:
        # The refusal is the point: a custom target must not become reachable by
        # guessing its name, so the shipped map stays the complete host list.
        _custom(label="dev")
        with pytest.raises(ValueError, match="unknown network"):
            Network("dev")

    def test_the_map_stays_immutable(self) -> None:
        with pytest.raises(TypeError):
            _CONFIGS["dev"] = _custom()  # type: ignore[index]

    def test_a_custom_config_is_frozen(self) -> None:
        config = _custom()
        with pytest.raises(AttributeError):
            config.base_url = "https://elsewhere.invalid"  # type: ignore[misc]

    def test_configs_are_shareable_across_threads_by_value(self) -> None:
        # Frozen and equal-by-value, so handing the same config to several
        # clients cannot let one retarget another. Nothing here needs a lock.
        config = _custom()
        with Client(config) as a, Client(config) as b:
            assert a.network is b.network
            assert a.network == _custom()


# ── Reserved labels (ENG-11134) ──────────────────────────────────────────────
#
# The label-is-a-key premise above has one hole the charset rule cannot close:
# `mainnet` is a perfectly legal label. So a custom target could call itself
# `mainnet` and, on any consumer that keys stored credentials by label, address
# the built-in network's secrets.
#
# `nexus-exchange-rs` already refused this (`RESERVED_LABELS` in its
# `config.rs`); this SDK stated the same threat model and enforced everything
# except the collision. These tests are the enforcement.


class TestReservedLabels:
    @pytest.mark.parametrize(
        "label",
        [
            "mainnet",
            "testnet",
            "local",
            # The legacy bare-`base_url` path stores credentials under this one.
            "custom",
            # Case-folded, because the storage this keys is often
            # case-insensitive: on macOS and Windows `Mainnet` and `mainnet`
            # reach the same entry, so reserving one spelling reserves nothing.
            "Mainnet",
            "MAINNET",
            "Custom",
            "TestNet",
        ],
    )
    def test_reserved_label_is_refused(self, label: str) -> None:
        with pytest.raises(ValueError) as excinfo:
            NetworkConfig.custom(
                label=label,
                base_url="https://stage.example.invalid",
                funds=Funds.PLAY,
            )
        message = str(excinfo.value)
        assert "reserved" in message
        # The error has to say WHY, or the next reader assumes it is a style
        # rule and picks `mainnet2`, which is fine, rather than understanding
        # that the label reaches a credential store.
        assert "credential" in message

    def test_a_label_merely_containing_a_reserved_name_is_allowed(self) -> None:
        # The rule is collision, not resemblance. `mainnet-shadow` keys its own
        # entry, so refusing it would cost real names for no security gain.
        for label in ("mainnet-shadow", "my-testnet", "local2", "customer"):
            assert (
                NetworkConfig.custom(
                    label=label,
                    base_url="https://stage.example.invalid",
                    funds=Funds.PLAY,
                ).label
                == label
            )

    def test_reserved_set_covers_every_built_in_network(self) -> None:
        # The load-bearing guard, mirroring rs's
        # `reserved_labels_cover_every_built_in_network`. A network added to
        # `Network` later without touching `_RESERVED_LABELS` would silently
        # become claimable — and nothing else in this file would notice, because
        # every test above names its label as a literal.
        for network in Network:
            assert network.value.casefold() in _RESERVED_LABELS, (
                f"Network.{network.name} is not reserved: a custom target could "
                f"claim its credential-storage key"
            )
            assert network.config.label.casefold() in _RESERVED_LABELS, (
                f"the label of Network.{network.name} ({network.config.label!r}) is not reserved"
            )

    def test_the_built_ins_still_construct_under_their_own_labels(self) -> None:
        # The check belongs to `custom()`, not to `_clean_label`: the built-in
        # configs run their labels through the latter at import time and those
        # case-fold straight into the reserved set. Enforcing it there would
        # refuse the very networks the set protects — an import-time failure of
        # the whole package.
        assert [n.config.label for n in Network] == ["Mainnet", "Testnet", "Local"]

    def test_the_legacy_bare_url_path_keeps_its_label(self) -> None:
        # `"custom"` is reserved BECAUSE this path stores credentials under it,
        # so the fix must not break the thing it is protecting. The label is a
        # constant on this path, not caller input.
        config = NetworkConfig._legacy_bare_url(
            base_url="https://gateway.example.invalid",
            direct_base_url="https://direct.example.invalid",
        )
        assert config.label == LEGACY_BASE_URL_LABEL == "custom"
        # And it stays UNKNOWN-funds: a bare URL says nothing about what is
        # behind it, which is the pre-existing behaviour this must preserve.
        assert config.funds is Funds.UNKNOWN

    def test_no_public_method_relabels_a_config(self) -> None:
        # `with_label` was public, and `__post_init__` re-runs `_clean_label` but
        # deliberately NOT `_reject_reserved_label` — the legacy path exists to
        # hold a reserved label — so one public call undid the rule above:
        # `custom(label="dev", ...).with_label("mainnet")` returned a config
        # labelled `mainnet`, in any casing. A reserved set is only as strong as
        # the narrowest way to set a label, so the relabeler is private now.
        assert not hasattr(NetworkConfig, "with_label")
        public_relabelers = [
            name
            for name in dir(NetworkConfig)
            if not name.startswith("_")
            and "label" in name
            and callable(getattr(NetworkConfig, name, None))
        ]
        assert public_relabelers == []

    def test_post_init_stores_the_cleaned_label_rather_than_validating_a_copy(self) -> None:
        # `_clean_label` returns a STRIPPED copy. Validating that return while
        # leaving `self.label` alone left a path that passed every check and then
        # held the raw value — the precise harm `_LABEL_PATTERN`'s \A/\Z anchors
        # exist to prevent, which is carrying a newline into whatever consumes
        # the credential key.
        config = NetworkConfig.custom(
            label="dev",
            base_url="https://stage.example.invalid",
            funds=Funds.PLAY,
        )
        assert config._with_label("dev\n").label == "dev"
        # `dataclasses.replace` is the generic form of the same route, and it
        # runs `__post_init__` too.
        assert dataclasses.replace(config, label="  spaced  ").label == "spaced"
        # The ordinary public constructor, for completeness.
        assert (
            NetworkConfig.custom(
                label="  padded  ",
                base_url="https://stage.example.invalid",
                funds=Funds.PLAY,
            ).label
            == "padded"
        )
