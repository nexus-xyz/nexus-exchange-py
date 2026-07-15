"""Request-capture tests for the default request headers (ENG-5955).

Every REST request must carry, by default:

  * ``User-Agent: nexus-exchange-py/<package version>`` — so the edge can
    segment per-key usage metrics by client + version (ENG-4804).
  * ``X-Nexus-Api-Version: <spec tag>`` — the spec contract the SDK is compiled
    against (ENG-5350), defaulting to the pinned ``.api-version``.

These must hold regardless of the HTTP verb, whether the request is signed, and
whether the caller supplied their own ``httpx.Client``.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from nexus_exchange import (
    DEFAULT_API_VERSION,
    DEFAULT_USER_AGENT,
    Client,
    Network,
    __version__,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# A well-formed 32-byte hex secret, matching the other signed-request tests.
_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def test_default_user_agent_tracks_package_version() -> None:
    assert DEFAULT_USER_AGENT == f"nexus-exchange-py/{__version__}"
    assert DEFAULT_USER_AGENT.startswith("nexus-exchange-py/")


def test_baked_api_version_matches_pinned_file() -> None:
    # ``.api-version`` is the repo's source of truth but is NOT shipped in the
    # wheel, so the tag is baked into the package. Guard the two from drifting;
    # this is the SDK-side mirror of the `drift` CI check.
    pinned = (REPO_ROOT / ".api-version").read_text().strip()
    assert DEFAULT_API_VERSION == pinned


def test_headers_on_public_get(httpx_mock) -> None:
    httpx_mock.add_response(json={})
    with Client(Network.LOCAL) as client:
        client.fetch_tickers()

    req = httpx_mock.get_request()
    assert req.headers["user-agent"] == DEFAULT_USER_AGENT
    assert req.headers["x-nexus-api-version"] == DEFAULT_API_VERSION


def test_headers_on_signed_write_coexist_with_signing_headers(httpx_mock) -> None:
    httpx_mock.add_response(json={})
    with Client(Network.LOCAL, api_key="nx_test", api_secret=_SECRET) as client:
        client._request("POST", "/orders", body={"x": 1}, signed=True, direct=True)

    req = httpx_mock.get_request()
    assert req.headers["user-agent"] == DEFAULT_USER_AGENT
    assert req.headers["x-nexus-api-version"] == DEFAULT_API_VERSION
    # The default headers must not displace the per-call content-type / signing.
    assert req.headers["content-type"] == "application/json"
    assert req.headers["x-api-key"] == "nx_test"
    assert "x-signature" in req.headers


def test_headers_present_with_injected_http_client(httpx_mock) -> None:
    # The key robustness case: a caller-supplied client has no default headers
    # set by us, so the per-request injection is what guarantees the contract.
    httpx_mock.add_response(json={})
    injected = httpx.Client()
    client = Client(Network.LOCAL, http_client=injected)
    try:
        client.fetch_tickers()
    finally:
        injected.close()

    req = httpx_mock.get_request()
    assert req.headers["user-agent"] == DEFAULT_USER_AGENT
    assert req.headers["x-nexus-api-version"] == DEFAULT_API_VERSION


def test_api_version_override_is_honored(httpx_mock) -> None:
    httpx_mock.add_response(json={})
    with Client(Network.LOCAL, api_version="v9.9.9") as client:
        client.fetch_tickers()

    req = httpx_mock.get_request()
    assert req.headers["x-nexus-api-version"] == "v9.9.9"


def test_blank_api_version_falls_back_to_default(httpx_mock) -> None:
    httpx_mock.add_response(json={})
    with Client(Network.LOCAL, api_version="   ") as client:
        client.fetch_tickers()

    req = httpx_mock.get_request()
    assert req.headers["x-nexus-api-version"] == DEFAULT_API_VERSION
