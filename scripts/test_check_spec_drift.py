#!/usr/bin/env python3
"""Regression tests for the spec-drift checker itself.

A checker nobody has defeated on purpose is a checker nobody knows works. These
tests prove `check_spec_drift.py` goes RED for each way the manifest can stop
describing reality — the bug it was written for included: `endpoints.txt` listed
five bridge operations WITHOUT the `/api/v1` prefix the client actually sends
(`direct=True`), so all five were absent from every released spec while the code
was correct all along. TestRealPackage pins exactly that: strip the prefix back off
the real manifest and the real client goes red, ten errors, both directions.

Everything is hermetic — no network, no spec download. The synthetic-package tests
build a throwaway package + manifest so the contract holds regardless of which
operations the SDK happens to implement today; TestRealPackage runs the same
parsers over the real `src/nexus_exchange`.

Run: python3 scripts/test_check_spec_drift.py   (stdlib unittest; no pytest needed)
"""

import contextlib
import io
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_spec_drift as csd  # noqa: E402

# A minimal client module: the prefix constant the checker reads, plus the real
# shape of the request path since ENG-8081 — `_send` holds the only httpx call,
# and `_request` / `_request_page` are the two entry points that funnel into it.
# Test cases append call sites.
CLIENT_HEADER = """\
API_V1_PREFIX = "/api/v1"


class Client:
    def _send(self, method, path, *, query="", body=None, signed=False, direct=False):
        return self._http.request(method, f"{path}{query}")

    def _request(self, method, path, *, query="", body=None, signed=False, direct=False):
        return self._send(method, path, query=query, body=body, signed=signed, direct=direct)

    def _request_page(self, path, *, query="", signed=False, direct=False):
        return self._send("GET", path, query=query, signed=signed, direct=direct)
"""


def _quiet(fn, *args, **kwargs):
    """Run a check function, swallowing its output; return its error count."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args, **kwargs)


def spec_of(*ops):
    """An OpenAPI-shaped dict declaring exactly `ops` (as (METHOD, path) pairs)."""
    paths = {}
    for method, path in ops:
        paths.setdefault(path, {})[method.lower()] = {"responses": {"200": {}}}
    return {"info": {"version": "9.9.9"}, "paths": paths}


@contextlib.contextmanager
def synthetic_package(client_body="", manifest="", extra_modules=None, pinned="v9.9.9"):
    """Point the checker at a throwaway package, manifest and pin.

    Restores every patched global on exit so one test cannot leak into the next.
    """
    saved = {
        name: getattr(csd, name)
        for name in ("REPO", "PACKAGE", "CLIENT_PY", "API_VERSION_FILE", "ENDPOINTS_TXT")
    }
    with tempfile.TemporaryDirectory() as tmp:
        pkg = os.path.join(tmp, "src", "nexus_exchange")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "client.py"), "w") as f:
            f.write(CLIENT_HEADER + textwrap.indent(textwrap.dedent(client_body), "    "))
        for name, source in (extra_modules or {}).items():
            # `name` may carry a subpackage path (`ws/stream.py`), so the parent
            # directory is created on demand — that shape is itself under test.
            dest = os.path.join(pkg, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f:
                f.write(textwrap.dedent(source))
        endpoints = os.path.join(tmp, "endpoints.txt")
        with open(endpoints, "w") as f:
            f.write(manifest)
        api_version = os.path.join(tmp, ".api-version")
        with open(api_version, "w") as f:
            f.write(pinned + "\n")
        csd.REPO = tmp
        csd.PACKAGE = pkg
        csd.CLIENT_PY = os.path.join(pkg, "client.py")
        csd.API_VERSION_FILE = api_version
        csd.ENDPOINTS_TXT = endpoints
        try:
            yield tmp
        finally:
            for name, value in saved.items():
                setattr(csd, name, value)


@contextlib.contextmanager
def allowlists(code_only=frozenset(), non_rest=frozenset()):
    """Swap both allowlists for the duration of a test."""
    saved = (csd.CODE_ONLY_OPS, csd.NON_REST_TARGETS)
    csd.CODE_ONLY_OPS, csd.NON_REST_TARGETS = set(code_only), set(non_rest)
    try:
        yield
    finally:
        csd.CODE_ONLY_OPS, csd.NON_REST_TARGETS = saved


class TestManifestVersusSpec(unittest.TestCase):
    """Invariant 1: every manifest entry must exist in the pinned spec."""

    def _missing(self, manifest, spec):
        with synthetic_package(manifest=manifest):
            entries = csd.load_manifest(csd.ENDPOINTS_TXT)
        return [op for op in entries if op not in csd.spec_ops(spec)]

    def test_unprefixed_bridge_path_is_missing_from_the_spec(self):
        # The original bug, in miniature: the spec serves the operation under
        # /api/v1 and the manifest claimed the bare path.
        missing = self._missing("GET /bridge/assets\n", spec_of(("GET", "/api/v1/bridge/assets")))
        self.assertEqual(missing, [("GET", "/bridge/assets")])

    def test_correct_prefix_matches(self):
        self.assertEqual(
            self._missing("GET /api/v1/bridge/assets\n", spec_of(("GET", "/api/v1/bridge/assets"))),
            [],
        )

    def test_placeholder_name_mismatch_is_missing(self):
        # Invariant 1 matches exactly, so a renamed placeholder is drift, not a
        # cosmetic difference — the spec's name is the contract.
        self.assertEqual(
            self._missing(
                "GET /markets/{market}/ticker\n",
                spec_of(("GET", "/markets/{market_id}/ticker")),
            ),
            [("GET", "/markets/{market}/ticker")],
        )

    def test_method_mismatch_is_missing(self):
        self.assertEqual(
            self._missing("PUT /keys\n", spec_of(("POST", "/keys"))),
            [("PUT", "/keys")],
        )


class TestManifestParser(unittest.TestCase):
    """The manifest parser rejects anything that would skew the comparison sets."""

    def _load(self, manifest):
        with synthetic_package(manifest=manifest):
            return csd.load_manifest(csd.ENDPOINTS_TXT)

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertEqual(self._load("# c\n\nGET /markets\n\n# trailing\n"), [("GET", "/markets")])

    def test_duplicate_entry_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(self._load, "GET /markets\nGET /markets\n")

    def test_malformed_line_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(self._load, "GET /markets extra\n")

    def test_unknown_method_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(self._load, "FETCH /markets\n")

    def test_relative_path_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(self._load, "GET markets\n")

    def test_empty_manifest_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(self._load, "# only a comment\n")


class TestCodeVersusManifest(unittest.TestCase):
    """Invariant 2: requested operations == manifest, modulo the allowlists."""

    def _errors(self, client_body, manifest, spec, code_only=(), non_rest=(), extra=None):
        with synthetic_package(client_body=client_body, manifest=manifest, extra_modules=extra):
            entries = csd.load_manifest(csd.ENDPOINTS_TXT)
            with allowlists(code_only, non_rest):
                return _quiet(csd.check_code_vs_manifest, entries, csd.spec_ops(spec))

    def test_agreement_passes(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets")),
            ),
            0,
        )

    def test_direct_call_resolves_to_the_api_v1_operation(self):
        # The prefix is applied from client.py's own constant, so a `direct=True`
        # call matches the prefixed manifest line and NOT the bare one.
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/bridge/assets", direct=True)\n',
                "GET /api/v1/bridge/assets\n",
                spec_of(("GET", "/api/v1/bridge/assets")),
            ),
            0,
        )

    def test_direct_call_against_unprefixed_manifest_is_two_errors(self):
        # Exactly the shipped bug: one unlisted request + one uncalled entry.
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/bridge/assets", direct=True)\n',
                "GET /bridge/assets\n",
                spec_of(("GET", "/bridge/assets")),
            ),
            2,
        )

    def test_requested_but_unlisted_fails(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("POST", "/orders")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets"), ("POST", "/orders")),
                extra=None,
            ),
            2,  # POST /orders unlisted, GET /markets uncalled
        )

    def test_listed_but_never_requested_fails(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n',
                "GET /markets\nGET /keys\n",
                spec_of(("GET", "/markets"), ("GET", "/keys")),
            ),
            1,
        )

    def test_code_only_op_is_exempt_from_the_manifest(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n'
                'def b(self):\n    self._request("POST", "/account/leverage")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets")),
                code_only={("POST", "/account/leverage")},
            ),
            0,
        )

    def test_stale_code_only_op_without_a_caller_fails(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets")),
                code_only={("POST", "/account/leverage")},
            ),
            1,
        )

    def test_code_only_op_the_spec_now_defines_fails(self):
        # The allowlist is self-expiring: once the pinned spec ships the operation,
        # the exemption's reason is gone and it belongs in the manifest.
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n'
                'def b(self):\n    self._request("POST", "/account/leverage")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets"), ("POST", "/account/leverage")),
                code_only={("POST", "/account/leverage")},
            ),
            1,
        )

    def test_non_rest_target_is_exempt_from_needing_a_caller(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n',
                "GET /markets\nGET /ws\n",
                spec_of(("GET", "/markets"), ("GET", "/ws")),
                non_rest={("GET", "/ws")},
            ),
            0,
        )

    def test_stale_non_rest_target_fails(self):
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets")),
                non_rest={("GET", "/ws")},
            ),
            1,
        )

    def test_placeholders_compare_by_position(self):
        # The code interpolates a value; the manifest names it. Both normalize.
        self.assertEqual(
            self._errors(
                'def a(self, m):\n    self._request("GET", f"/markets/{m}/ticker")\n',
                "GET /markets/{market_id}/ticker\n",
                spec_of(("GET", "/markets/{market_id}/ticker")),
            ),
            0,
        )

    def test_request_in_another_module_is_counted(self):
        # Every module in the package is scanned, so an adapter that calls
        # `_request` on a borrowed client cannot smuggle in an unlisted operation.
        self.assertEqual(
            self._errors(
                "",
                "GET /markets\n",
                spec_of(("GET", "/markets"), ("GET", "/tickers")),
                extra={
                    "adapter.py": """
                        class Adapter:
                            def go(self):
                                self._client._request("GET", "/tickers")
                    """
                },
            ),
            2,  # GET /tickers unlisted, GET /markets uncalled
        )

    def test_httpx_call_outside_request_fails(self):
        # `/keys` is fetched straight off the transport, so no `_request` call
        # names it: the bypass is reported, and the manifest line it was meant to
        # satisfy still reads as uncalled.
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n'
                'def sneaky(self):\n    return self._http.get("/keys")\n',
                "GET /markets\nGET /keys\n",
                spec_of(("GET", "/markets"), ("GET", "/keys")),
            ),
            2,
        )

    def test_a_page_request_only_endpoint_stays_visible(self):
        """The regression @Luc-Campos asked for (#46).

        `_request_page` is the second entry point (ENG-8081). Before the checker
        knew about it, an endpoint reached ONLY through it was invisible to the
        AST walk: the manifest line read as never-requested, and the operation
        vanished from the code side of the comparison while still being served.
        Four of the five paginated endpoints were in that state.
        """
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request_page("/fills", signed=True)\n',
                "GET /fills\n",
                spec_of(("GET", "/fills")),
            ),
            0,
        )

    def test_page_request_resolves_direct_to_the_api_v1_operation(self):
        # `direct=True` decides the /api/v1 prefix on this entry point too, so a
        # paginated direct call must resolve the same way `_request` does.
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request_page("/fills", direct=True)\n',
                "GET /api/v1/fills\n",
                spec_of(("GET", "/api/v1/fills")),
            ),
            0,
        )

    def test_httpx_call_outside_send_fails_even_inside_request(self):
        # The sending site is `_send`, not the entry point. A transport call
        # placed inside `_request` itself is still a bypass now, and saying so
        # is what keeps "one sender" from decaying into "any entry point may
        # send".
        self.assertEqual(
            self._errors(
                'def a(self):\n    self._request("GET", "/markets")\n'
                'def sneaky(self):\n    return self._http.get("/keys")\n',
                "GET /markets\nGET /keys\n",
                spec_of(("GET", "/markets"), ("GET", "/keys")),
            ),
            2,
        )

    def test_lifecycle_call_on_the_http_handle_is_not_a_request(self):
        self.assertEqual(
            self._errors(
                "def close(self):\n    self._http.close()\n"
                'def a(self):\n    self._request("GET", "/markets")\n',
                "GET /markets\n",
                spec_of(("GET", "/markets")),
            ),
            0,
        )


class TestUnattributableCalls(unittest.TestCase):
    """A call the parser cannot attribute is a hard failure, never a silent skip."""

    def _run(self, client_body):
        with synthetic_package(client_body=client_body, manifest="GET /markets\n"):
            entries = csd.load_manifest(csd.ENDPOINTS_TXT)
            with allowlists():
                return _quiet(csd.check_code_vs_manifest, entries, {("GET", "/markets")})

    def test_computed_path_fails(self):
        with self.assertRaises(SystemExit):
            self._run('def a(self, p):\n    self._request("GET", p)\n')

    def test_non_literal_method_fails(self):
        with self.assertRaises(SystemExit):
            self._run('def a(self, m):\n    self._request(m, "/markets")\n')

    def test_non_literal_direct_fails(self):
        with self.assertRaises(SystemExit):
            self._run('def a(self, d):\n    self._request("GET", "/markets", direct=d)\n')

    def test_kwargs_splat_fails(self):
        with self.assertRaises(SystemExit):
            self._run('def a(self, kw):\n    self._request("GET", "/markets", **kw)\n')

    def test_concatenated_path_fails(self):
        # A path assembled with `+` cannot be attributed, so it must fail rather
        # than decode to a plausible-looking operation.
        with self.assertRaises(SystemExit):
            self._run('def a(self, p):\n    self._request("GET", "/markets/" + p)\n')

    def test_f_string_with_a_format_spec_still_resolves(self):
        # An interpolation carrying a format spec is still one path segment, so it
        # normalizes like any other — no failure, and no invented segment.
        with synthetic_package(
            client_body='def a(self, n):\n    self._request("GET", f"/markets/{n:d}/x")\n',
            manifest="GET /markets/{market_id}/x\n",
        ):
            requested = csd.requested_ops(csd.package_modules(), "/api/v1")
        self.assertEqual(set(requested), {("GET", "/markets/{}/x")})

    def test_no_request_calls_at_all_fails(self):
        # The parser going blind must be an error, not "zero drift".
        with synthetic_package(client_body="def a(self):\n    return None\n", manifest="GET /x\n"):
            with self.assertRaises(SystemExit):
                _quiet(csd.requested_ops, csd.package_modules(), "/api/v1")


class TestModuleDiscovery(unittest.TestCase):
    """The set of files the walk reads. Everything else here is only as good as
    this: an operation in a module the checker never opens is not reported as
    unattributable — it is not seen at all, and the run passes while the manifest
    under-counts. That is the one failure mode with no loud symptom."""

    def test_subpackage_module_is_discovered(self):
        with synthetic_package(
            client_body='def a(self):\n    self._request("GET", "/x")\n',
            extra_modules={"ws/__init__.py": "", "ws/stream.py": ""},
            manifest="GET /x\n",
        ):
            # Relative to the *patched* PACKAGE, so this must be read inside the
            # fixture — it restores the real paths on exit.
            found = {os.path.relpath(m, csd.PACKAGE) for m in csd.package_modules()}
        self.assertIn(os.path.join("ws", "stream.py"), found)

    def test_operation_requested_only_from_a_subpackage_is_attributed(self):
        # The regression proper: a flat listing returns zero errors here, because
        # the call is invisible rather than unlisted.
        with synthetic_package(
            client_body='def a(self):\n    self._request("GET", "/x")\n',
            extra_modules={
                "ws/__init__.py": "",
                "ws/stream.py": (
                    "class S:\n"
                    "    def go(self):\n"
                    '        self._request("GET", "/hidden", direct=True)\n'
                ),
            },
            manifest="GET /x\n",
        ):
            requested = csd.requested_ops(csd.package_modules(), "/api/v1")
            self.assertIn(("GET", "/api/v1/hidden"), requested)
            with allowlists():
                errors = _quiet(
                    csd.check_code_vs_manifest,
                    csd.load_manifest(csd.ENDPOINTS_TXT),
                    csd.spec_ops(spec_of(("GET", "/x"), ("GET", "/api/v1/hidden"))),
                )
        self.assertEqual(errors, 1)

    def test_httpx_bypass_in_a_subpackage_is_caught(self):
        # check_single_entry_point reads the same module list, so the bypass guard
        # inherits the same blind spot if the walk is flat.
        with synthetic_package(
            client_body='def a(self):\n    self._request("GET", "/x")\n',
            extra_modules={
                "ws/__init__.py": "",
                "ws/stream.py": ("class S:\n    def go(self):\n        self._http.get('/raw')\n"),
            },
            manifest="GET /x\n",
        ):
            errors = _quiet(csd.check_single_entry_point, csd.package_modules())
        self.assertEqual(errors, 1)

    def test_pycache_is_not_walked(self):
        # A stale generated copy would double-count the same operation and pin the
        # error to a path nobody edits.
        with synthetic_package(
            client_body='def a(self):\n    self._request("GET", "/x")\n',
            extra_modules={"__pycache__/client.py": 'X = "stale"\n'},
            manifest="GET /x\n",
        ):
            found = csd.package_modules()
        self.assertEqual([m for m in found if "__pycache__" in m], [])


class TestPinMatchesSpec(unittest.TestCase):
    """Invariant 0: the spec handed to the checker must be the pinned release."""

    def test_matching_version_passes(self):
        self.assertEqual(csd.check_pin_matches_spec(spec_of(("GET", "/x")), "v9.9.9"), "9.9.9")

    def test_two_component_pin_matches_padded_spec_version(self):
        spec = spec_of(("GET", "/x"))
        spec["info"]["version"] = "0.7"
        self.assertEqual(csd.check_pin_matches_spec(spec, "v0.7.0"), "0.7")

    def test_mismatched_version_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(csd.check_pin_matches_spec, spec_of(("GET", "/x")), "v0.7.2")

    def test_missing_version_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(csd.check_pin_matches_spec, {"paths": {}}, "v9.9.9")

    def test_spec_without_operations_fails(self):
        with self.assertRaises(SystemExit):
            _quiet(csd.spec_ops, {"info": {"version": "9.9.9"}, "paths": {}})


class TestRealPackage(unittest.TestCase):
    """The same parsers over the real package — the regression pin.

    The spec side is synthesized from the real manifest, so these assertions are
    about code<->manifest agreement only and stay true as the SDK grows.
    """

    @classmethod
    def setUpClass(cls):
        cls.manifest = csd.load_manifest()
        cls.spec = csd.spec_ops(spec_of(*cls.manifest))

    def test_real_code_and_manifest_agree(self):
        self.assertEqual(_quiet(csd.check_code_vs_manifest, self.manifest, self.spec), 0)

    def test_real_bridge_operations_are_requested_under_api_v1(self):
        with open(csd.CLIENT_PY) as f:
            prefix = csd.read_api_v1_prefix(f.read())
        self.assertEqual(prefix, "/api/v1")
        requested = csd.requested_ops(csd.package_modules(), prefix)
        for op in (
            ("GET", "/api/v1/bridge/assets"),
            ("GET", "/api/v1/bridge/deposit-addresses"),
            ("POST", "/api/v1/bridge/deposit-addresses"),
            ("GET", "/api/v1/bridge/deposits"),
            ("GET", "/api/v1/bridge/deposits/{}"),
        ):
            self.assertIn(op, requested, f"{op[0]} {op[1]} should be requested under /api/v1")
            self.assertNotIn((op[0], op[1].replace("/api/v1", "", 1)), requested)

    def test_dropping_the_api_v1_prefix_from_the_manifest_goes_red(self):
        # Defeat the fix the way it was originally broken: five bridge lines
        # without the prefix the client sends. Ten errors — five requests with no
        # manifest line, five manifest lines with no request.
        broken = [
            (m, p.replace("/api/v1/bridge/", "/bridge/", 1)) if "/bridge/" in p else (m, p)
            for m, p in self.manifest
        ]
        self.assertNotEqual(broken, self.manifest, "the manifest should contain bridge entries")
        self.assertEqual(_quiet(csd.check_code_vs_manifest, broken, self.spec), 10)

    def test_real_manifest_lists_no_operation_outside_the_contract(self):
        # `GET /health` is requested by `health_check` but is not a spec operation,
        # so it must live in CODE_ONLY_OPS rather than in the manifest — otherwise
        # the coverage figure counts an operation no released spec defines.
        self.assertNotIn(("GET", "/health"), self.manifest)
        self.assertIn(("GET", "/health"), csd.CODE_ONLY_OPS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
