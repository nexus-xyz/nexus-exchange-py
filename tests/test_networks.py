"""The network axis (ENG-6454).

These tests are mostly about what the SDK must *refuse* to do. The network axis
is the boundary between play money and real money, and every failure mode worth
guarding is one where something resolves to a plausible-looking wrong answer
instead of stopping: a host derived by interpolation, a signature made under a
guessed domain, a faucet call aimed at mainnet.
"""

import pytest

from nexus_exchange import AuthError, Client, EthSigner, Network, SigningDomain

# A throwaway key; these tests never touch the network.
_KEY = "11" * 32


class TestNetworkMembers:
    def test_axis_is_exactly_mainnet_testnet_local(self) -> None:
        assert [n.value for n in Network] == ["mainnet", "testnet", "local"]

    def test_retired_release_channels_are_refused_with_migration_help(self) -> None:
        # `stable`/`beta` were release channels, not networks. Silently mapping
        # them onto a network is what this change exists to stop.
        for retired, expected in (("stable", "TESTNET"), ("beta", "base_url")):
            with pytest.raises(ValueError, match="no longer a Network value") as exc:
                Network(retired)
            assert expected in str(exc.value)

    def test_unknown_network_is_refused_not_guessed(self) -> None:
        # Fail-safe direction: an unrecognized network is real funds until proven
        # otherwise, so it must not resolve to the friendliest-looking match.
        with pytest.raises(ValueError, match="treated as real funds"):
            Network("devnet")

    def test_str_enum_value_comparison_still_works(self) -> None:
        assert Network.TESTNET == "testnet"


class TestHostMap:
    def test_mainnet_host_is_not_the_interpolated_one(self) -> None:
        # The whole reason the map is spelled out: `api.{network}.nexus.xyz`
        # resolves everywhere testable and is wrong only on real funds.
        cfg = Network.MAINNET.config
        assert "api.mainnet.nexus.xyz" not in cfg.published_rest_base
        assert cfg.published_rest_base == "https://api.nexus.xyz/v1"
        assert "mainnet" not in cfg.ws_market_data_url

    def test_hosted_ws_bases_are_published_for_every_network(self) -> None:
        # The gap this closes: hosted networks previously exposed no WS base.
        for network in Network:
            assert network.ws_market_data_url.endswith("/stream")
            assert network.ws_authenticated_url.endswith("/ws")
            assert network.ws_market_data_url.startswith(("wss://", "ws://"))

    def test_funds_and_faucet_semantics(self) -> None:
        assert Network.MAINNET.real_funds is True
        assert Network.MAINNET.has_faucet is False
        assert Network.TESTNET.real_funds is False
        assert Network.TESTNET.has_faucet is True
        assert Network.LOCAL.real_funds is False

    def test_config_is_immutable_and_shared(self) -> None:
        # Frozen and identical per access, so a live client cannot have its
        # target mutated from another thread or another caller.
        cfg = Network.TESTNET.config
        assert cfg is Network.TESTNET.config
        # FrozenInstanceError subclasses AttributeError.
        with pytest.raises(AttributeError):
            cfg.base_url = "http://evil.example"  # type: ignore[misc]


class TestClientTargeting:
    def test_default_network_is_testnet_and_preserves_legacy_targets(self) -> None:
        # Defaulting to real funds would be one keystroke from a costly mistake;
        # and testnet's bases are byte-identical to the old `STABLE` ones, so
        # existing code keeps hitting exactly the same URLs.
        with Client() as client:
            assert client.network is Network.TESTNET
            assert client._base_url == "https://exchange.nexus.xyz/api/exchange"
            assert client._direct_base_url == "https://exchange.nexus.xyz"

    def test_mainnet_without_an_explicit_base_refuses_at_construction(self) -> None:
        # Its host is published but not resolvable. Guessing one would mean
        # inventing a real-funds target; falling back to another network's would
        # be worse. Fail here, not once an order has been built.
        with pytest.raises(ValueError, match="no default base_url yet"):
            Client(Network.MAINNET)

    def test_mainnet_never_falls_back_to_another_networks_host(self) -> None:
        with pytest.raises(ValueError) as exc:
            Client(Network.MAINNET)
        assert "exchange.nexus.xyz" not in str(exc.value)

    def test_mainnet_with_an_explicit_base_is_allowed(self) -> None:
        with Client(Network.MAINNET, base_url="https://api.nexus.xyz") as client:
            assert client._base_url == "https://api.nexus.xyz"
            assert client.network.real_funds is True

    def test_retired_beta_channel_is_reachable_via_the_two_overrides(self) -> None:
        # Beta is demoted to an explicit override. It keeps the gateway/direct
        # split, which a single base_url would have collapsed.
        with Client(
            base_url="https://beta.exchange.nexus.xyz/api/exchange",
            direct_base_url="https://beta.exchange.nexus.xyz",
        ) as client:
            assert client._base_url == "https://beta.exchange.nexus.xyz/api/exchange"
            assert client._direct_base_url == "https://beta.exchange.nexus.xyz"

    def test_single_base_url_override_still_covers_both_surfaces(self) -> None:
        with Client(Network.LOCAL, base_url="http://127.0.0.1:8080/") as client:
            assert client._base_url == "http://127.0.0.1:8080"
            assert client._direct_base_url == "http://127.0.0.1:8080"

    def test_blank_override_falls_back_to_the_network_default(self) -> None:
        # Matches the old `base_url or network.base_url` behaviour, and how a
        # blank api_version is treated — a blank string is "unset", not a target.
        with Client(Network.LOCAL, base_url="   ") as client:
            assert client._base_url == "http://localhost:9090"

    def test_blank_override_on_mainnet_still_refuses(self) -> None:
        with pytest.raises(ValueError, match="no default base_url yet"):
            Client(Network.MAINNET, base_url="  ")

    def test_a_base_url_that_is_only_slashes_is_rejected(self) -> None:
        # Would otherwise leave an empty base issuing relative requests.
        with pytest.raises(ValueError, match="non-empty URL"):
            Client(Network.LOCAL, base_url="/")

    def test_string_network_is_accepted_and_validated(self) -> None:
        with Client("local") as client:
            assert client.network is Network.LOCAL
        with pytest.raises(ValueError):
            Client("stable")

    def test_network_is_readonly(self) -> None:
        # Credentials are per-network, so switching must mean a new client.
        with Client(Network.LOCAL) as client:
            with pytest.raises(AttributeError):
                client.network = Network.MAINNET  # type: ignore[misc]


class TestNetworkRestrictedOperations:
    def test_claim_credit_is_refused_on_a_faucet_less_network(self) -> None:
        # `POST /account/credit` is marked testnet/local-only in the spec. Better
        # to fail locally than to spend a signed request on a real-funds host.
        with Client(Network.MAINNET, base_url="https://api.nexus.xyz") as client:
            with pytest.raises(ValueError, match="no faucet"):
                client.claim_credit()

    def test_claim_credit_is_allowed_on_local(self, httpx_mock) -> None:
        httpx_mock.add_response(
            url="http://localhost:9090/api/v1/account/credit", json={"amount": "100"}
        )
        secret = "00" * 32
        with Client(Network.LOCAL, api_key="nx_test", api_secret=secret) as client:
            client.claim_credit()


class TestSigningDomain:
    def test_static_map_publishes_no_chain_id(self) -> None:
        # Null means "not published", not zero — and mainnet is Ethereum-backed,
        # so a Nexus L1 chain id is never right there.
        for network in Network:
            domain = network.signing_domain
            assert domain.chain_id is None
            assert domain.name == "Nexus Exchange"
            assert domain.version == "1"

    def test_signing_refuses_a_missing_chain_id(self) -> None:
        # The requirement with no safe fallback: no chain id means refuse to
        # sign, never default. Feeding the static domain straight in must fail.
        signer = EthSigner.from_hex(_KEY)
        with pytest.raises(AuthError, match="chain_id is required"):
            signer.register_agent(
                agent="0x" + "22" * 20,
                expires_at_ms=1,
                nonce=1,
                chain_id=Network.MAINNET.signing_domain.chain_id,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad", [0, -1, True])
    def test_signing_refuses_a_nonsense_chain_id(self, bad: object) -> None:
        # `True` matters: bool is an int subclass, so it would otherwise sign
        # under chain id 1 — Ethereum Mainnet.
        signer = EthSigner.from_hex(_KEY)
        with pytest.raises(AuthError, match="chain_id must be"):
            signer.register_agent(
                agent="0x" + "22" * 20,
                expires_at_ms=1,
                nonce=1,
                chain_id=bad,  # type: ignore[arg-type]
            )

    def test_a_valid_chain_id_still_signs(self) -> None:
        signer = EthSigner.from_hex(_KEY)
        reg = signer.register_agent(agent="0x" + "22" * 20, expires_at_ms=1, nonce=1, chain_id=1)
        assert reg.signature.startswith("0x")

    def test_domain_defaults_match_the_contract(self) -> None:
        assert SigningDomain() == SigningDomain("Nexus Exchange", "1", None)
