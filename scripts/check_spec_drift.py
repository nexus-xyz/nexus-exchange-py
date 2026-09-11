#!/usr/bin/env python3
"""Check the Python SDK's operations manifest (`endpoints.txt`) against the pinned
OpenAPI spec AND against the requests the client code actually issues.

This is the Python counterpart to the same-named scripts in nexus-exchange-rs and
nexus-exchange-cli. Those parse path literals out of Rust source with regexes; this
SDK is itself Python, so the code side is read with `ast` — every REST call goes
through `Client._request(METHOD, path, ..., direct=…)`, and an AST walk sees those
calls exactly, including multi-line ones, without pattern-matching on formatting.

`direct=True` routes the call at the direct backend service, which prefixes the
path with `API_V1_PREFIX` (`/api/v1`) for both signing and the wire (see
`Client._request`). So the operation a call targets is the *resolved* path, not the
literal in the source — `_request("GET", "/bridge/assets", direct=True)` targets
`GET /api/v1/bridge/assets`. The prefix is read out of client.py rather than
hardcoded here, so moving the constant cannot silently desynchronize the checker.

Four invariants are enforced:

0. .api-version <-> the spec file it was handed
   The spec's own `info.version` must equal the pinned tag. Guards against a
   mis-fetched or stale-cached spec: every conclusion below is drawn from this
   file, so if it is not the pinned release, a green run means nothing.

1. endpoints.txt -> pinned spec
   Every operation the manifest lists must exist in the pinned spec, matched
   exactly (placeholder names included, so `{market}` vs `{market_id}` is a
   failure). A miss means a removal, rename, or typo. Spec operations the SDK does
   not implement are reported as an informational coverage gap, with the coverage
   figure the dashboard's Python panel reads.

2. client code <-> endpoints.txt, by equality
   The set of operations the package actually requests must equal the manifest,
   modulo one explicit, documented allowlist:

     * NON_REST_TARGETS — listed in the manifest but reached WITHOUT a
                          `_request` call, so the AST cannot (and should not) see
                          it. It is checked for staleness, so the exemption cannot
                          outlive its reason: an entry the manifest no longer lists
                          fails.

   Nothing is exempt in the other direction. `CODE_ONLY_OPS` used to be — an
   operation implemented "ahead of the pinned spec" was parked there instead of in
   the manifest — but the fleet-wide policy of 2026-08-20 (ENG-8616) deletes any
   operation the contract does not define rather than parking it. The set is
   permanently empty and any entry in it fails the run (ENG-8618).

3. client credential <-> the spec's declared `security` (ENG-13303)
   Same AST walk, one more axis: the credential each call sends (`signed=True` ->
   HMAC, `bearer=` -> session token, neither -> unauthenticated) must satisfy the
   `security` the pinned spec declares for that operation. Nine operations
   disagreed when this was first measured by hand — including a public one the
   client signed, which made it unreachable without credentials — and nothing
   noticed, because agreement on the *path* was all anything checked. The
   surviving disagreements are pinned in `SECURITY_EXCEPTIONS`, which fails when
   one is resolved as loudly as when a new one appears.

Usage: check_spec_drift.py <openapi.json>
"""

import ast
import json
import os
import re
import sys
from typing import NamedTuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PACKAGE = os.path.join(REPO, "src", "nexus_exchange")
CLIENT_PY = os.path.join(PACKAGE, "client.py")
API_VERSION_FILE = os.path.join(REPO, ".api-version")
ENDPOINTS_TXT = os.path.join(REPO, "endpoints.txt")

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# The single method every REST call funnels through, and the httpx handle it uses.
# Request-issuing `_http` calls are required to stay inside `_request` (see
# check_single_entry_point): a method that bypassed `_request` would issue a request
# the AST walk never sees, and an operation invisible to the parser is exactly the
# undercount this checker exists to prevent. Lifecycle calls on the same handle
# (`close`, and anything else that sends nothing) are not requests and are ignored.
REQUEST_FUNC = "_request"
# The paginating entry point (ENG-8081). It funnels into the same sender, but it
# takes the path FIRST and no method - every paginated read is a GET - so the
# walk below has to know both shapes. Without this the five paginated endpoints
# are invisible to the AST walk and go dark in the manifest comparison while
# still being served, which is the exact under-count this checker exists to
# prevent (@Luc-Campos, review of #46).
PAGE_REQUEST_FUNC = "_request_page"
PAGE_REQUEST_METHOD = "GET"
REQUEST_FUNCS = (REQUEST_FUNC, PAGE_REQUEST_FUNC)
# The ONE function permitted to touch `_http`. It used to be `_request` itself;
# ENG-8081 split the sending half out so `_request` and `_request_page` could
# share it. The invariant is unchanged - exactly one sending site - so the check
# below now names that site rather than assuming it is the entry point.
SENDING_FUNC = "_send"
HTTP_HANDLE = "_http"
HTTP_SENDING_CALLS = frozenset(
    {
        "request",
        "send",
        "stream",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
    }
)

# Permanently EMPTY (ENG-8618), and kept as a named tripwire rather than deleted.
#
# This set used to exempt an operation from the manifest comparison on the grounds
# that it was implemented "ahead of the pinned spec": listing it in endpoints.txt
# would (correctly) fail invariant 1, so it was parked here instead. The fleet-wide
# policy of 2026-08-20 (ENG-8616) ends that — an operation the pinned spec does not
# define must not be implemented at all. No attribution, no parking, no release-lag
# exception: an SDK that wants an operation waits for the published tag defining it.
#
# The two entries it used to hold, and where they went:
#
#   POST /account/leverage — `set_leverage`, deleted. The path routed nowhere, so it
#     worked for nobody. api-module serves `POST /leverage`, being documented under
#     ENG-7318; implement it at that path once a *published* spec version defines it.
#   GET /health — `health_check`, deleted. An operational probe of the legacy
#     gateway, never a contract operation, and dropped upstream in v0.7.1 in favour
#     of `GET /status` (which this SDK does not implement — ENG-8618 deletes, it does
#     not repoint).
#
# An entry here buys nothing: `check_code_vs_manifest` no longer subtracts it, so it
# fails as a policy violation (`check_allowlist_empty`) and its caller — if it has
# one — is still reported as an unlisted operation.
CODE_ONLY_OPS: set[tuple[str, str]] = set()

# Listed in endpoints.txt but reached WITHOUT a `_request` call, so no AST walk can
# see a caller. Empty today, and deliberately kept as a named concept: rs uses it
# for the WebSocket upgrade `GET /ws`, which its streaming client opens directly.
# This SDK mints a token over REST (`POST /ws-tokens`) and opens no socket itself,
# so nothing qualifies yet. An entry here that the manifest does not list is stale
# and fails.
NON_REST_TARGETS: set[tuple[str, str]] = set()

# Spec operations this SDK deliberately does not implement are *not* enumerated —
# they are reported as an informational coverage gap. The Python SDK trails the Rust
# SDK by design (see endpoints.txt), so an uncovered operation is a backlog item,
# not drift.


# -- invariant 3: the credential a call sends vs. the security it declares ------

# The three credentials `Client._send` can put on a request, and the spec scheme
# each one satisfies. They are mutually exclusive by construction — `_send`
# refuses `signed=True` together with `bearer=`, and this walk refuses the same
# pair statically, so exactly one of these describes any call.
AUTH_HMAC = "hmac"  # signed=True  -> X-API-Key + X-Timestamp + X-Signature
AUTH_BEARER = "bearer"  # bearer=…      -> Authorization: Bearer <session token>
AUTH_PUBLIC = "public"  # neither       -> no credential at all
SCHEME_FOR_AUTH = {AUTH_HMAC: "hmacAuth", AUTH_BEARER: "bearerAuth"}

# How a declaration with no scheme in it renders. An operation is public when it
# declares `security: []`, when it declares an empty `{}` alternative, or when it
# declares nothing and the document declares no root `security` either — three
# spellings of one meaning, so they normalize to one word.
PUBLIC_DECLARATION = "public"

# The disagreements between what the client sends and what the pinned spec
# declares that are KNOWN and NOT YET SETTLED (ENG-13303). Each is a contract
# question — whether the spec over-declares or the client under-authenticates is
# the server's middleware to answer, not this SDK's — so they are pinned here
# rather than silently tolerated or unilaterally "fixed".
#
# Pinned on BOTH sides: the credential the call sends, and the declaration it is
# measured against. That makes every way out of an entry loud:
#
#   * the client changes credential          -> sent side no longer matches -> red
#   * the spec widens or narrows its security -> declared side no longer matches -> red
#   * the two come to agree                   -> the divergence is gone, entry is
#                                                stale -> red (delete the entry)
#   * the operation loses its caller          -> stale -> red
#
# so an entry can never outlive the disagreement it records. A green run still
# prints all of them, because a tolerated divergence nobody sees is one nobody
# resolves.
#
# The four shapes, as filed:
#
#   1. `GET /keys`, `DELETE /keys/{key_id}` declare `bearerAuth` ("used only for
#      API key management") and the client signs them with the caller's API key.
#      `POST /keys` is the one of the three on the bearer path — correctly, since
#      it MINTS the HMAC credential and so cannot require it. Moving the other two
#      would stop a signature-only caller from listing or deleting keys, which is
#      a real API change here, not a patch.
#   2. The three `/admin/tiers` operations declare `adminAuth` — a bearer carrying
#      ADMIN_SECRET, a different credential entirely rather than a stricter HMAC.
#      The client signs them with the caller's ordinary key, and there is no way to
#      supply an admin secret through this client at all. Resolving this one adds a
#      credential to the public surface.
#   3. `GET /markets`, `GET /markets/{id}/adl-events` and
#      `GET /account/{address}/adl-history` declare `hmacAuth` and the client sends
#      them unauthenticated. `GET /markets` is the SDK's most basic call, is
#      documented and exercised as public, and every keyless quickstart depends on
#      it — so the spec almost certainly over-declares, but it does declare it.
#
# The fourth shape — `GET /api/v1/bridge/assets`, declared public and sent signed
# — is NOT here: it was the one row with no contract question attached (the client
# was strictly stricter than the contract, at the cost of making a public read
# unreachable without credentials) and it was fixed in the same change that added
# this check.
#
# Keys are (METHOD, normalized path) exactly as `requested_ops` reports them —
# placeholders collapsed to `{}` — and values are (credential sent, declaration).
SECURITY_EXCEPTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/keys"): (AUTH_HMAC, "bearerAuth"),
    ("DELETE", "/keys/{}"): (AUTH_HMAC, "bearerAuth"),
    ("PUT", "/admin/tiers"): (AUTH_HMAC, "adminAuth"),
    ("GET", "/admin/tiers"): (AUTH_HMAC, "adminAuth"),
    ("DELETE", "/admin/tiers/{}"): (AUTH_HMAC, "adminAuth"),
    ("GET", "/markets"): (AUTH_PUBLIC, "hmacAuth"),
    ("GET", "/markets/{}/adl-events"): (AUTH_PUBLIC, "hmacAuth"),
    ("GET", "/account/{}/adl-history"): (AUTH_PUBLIC, "hmacAuth"),
}


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def normalize_path(p):
    """Collapse any `{placeholder}` segment to a bare `{}` so paths compare by
    position rather than placeholder name. Used for the code<->manifest invariant,
    where the code's f-string interpolations carry no name; invariant 1 compares
    against the spec with names intact."""
    return re.sub(r"\{[^}]*\}", "{}", p)


def read_pinned_tag():
    try:
        with open(API_VERSION_FILE) as f:
            tag = f.read().strip()
    except OSError as e:
        fail(f"cannot read {API_VERSION_FILE}: {e}")
    if not re.fullmatch(r"v[0-9]+(\.[0-9]+){0,2}", tag):
        fail(f".api-version must look like vX.Y.Z (got: {tag!r})")
    return tag


def version_tuple(v):
    """Comparable 3-tuple for `vX.Y.Z` / `X.Y` / `X`, padded so 0.7 == 0.7.0."""
    parts = tuple(int(n) for n in v.lstrip("v").split("."))
    return parts + (0,) * (3 - len(parts))


def load_manifest(path=ENDPOINTS_TXT):
    """Parse endpoints.txt into an ordered list of (METHOD, path). Rejects a
    malformed line, an unknown method, a relative path, and a duplicate entry —
    each would otherwise skew the comparison sets or the coverage count."""
    out = []
    seen = {}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError as e:
        fail(f"cannot read {path}: {e}")
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2:
            fail(f"{path}:{lineno}: expected 'METHOD /path', got {line!r}")
        method, p = parts[0].upper(), parts[1]
        if method not in HTTP_METHODS:
            fail(f"{path}:{lineno}: unknown HTTP method {parts[0]!r}")
        if not p.startswith("/"):
            fail(f"{path}:{lineno}: path must start with '/', got {p!r}")
        op = (method, p)
        if op in seen:
            fail(f"{path}:{lineno}: duplicate entry {method} {p} (first seen on line {seen[op]})")
        seen[op] = lineno
        out.append(op)
    if not out:
        fail(f"{path}: no entries parsed; the manifest cannot be empty")
    return out


def canonical_op(op, api_v1_prefix):
    """One (method, path) per operation, whichever spelling the spec used.

    The pinned spec documents many operations TWICE — once bare (`/orders`) and
    once behind the v1 prefix (`/api/v1/orders`) — pending the layout decision in
    ENG-8155. They are one operation. Comparing path strings literally therefore
    reported the spelling this SDK did not pick as an uncovered gap, and divided
    by a denominator that counted 101 paths where the API has 68 operations:
    coverage read 50.5% when it was 75.0%, and 33 of the 50 reported gaps were
    the other spelling of something already implemented (ENG-11847).

    The prefix is the one `read_api_v1_prefix` pulls out of client.py's AST, not
    a literal repeated here — the same reason that function exists: the value
    this checker reasons about has to be the value the client actually sends.

    Applied ONLY to the coverage figures. Invariant 1 keeps comparing literal
    paths, because the manifest must name a path the spec really declares, not
    merely some spelling of it.
    """
    method, path = op
    prefix = api_v1_prefix.rstrip("/")
    if prefix and path.startswith(prefix + "/"):
        path = path[len(prefix) :]
    return (method, path)


def coverage_figures(manifest, available, api_v1_prefix):
    """Canonical coverage sets, split out of `main` so they are testable.

    Worth keeping separate: a report that hides every gap and a report with no
    gaps print the same reassuring line, so the tests need something to call
    other than `main`.
    """

    def canon(ops):
        return {canonical_op(op, api_v1_prefix) for op in ops}

    spec_set, mine = canon(available), canon(manifest)
    return {
        "spec": spec_set,
        "manifest": mine,
        "covered": spec_set & mine,
        "uncovered": sorted(spec_set - mine),
        "spellings": len(available) - len(spec_set),
    }


def spec_ops(spec):
    ops = set()
    for p, methods in spec.get("paths", {}).items():
        for m in methods:
            if m.upper() in HTTP_METHODS:
                ops.add((m.upper(), p))
    if not ops:
        fail("the spec declares no operations; wrong or truncated openapi.json?")
    return ops


def read_api_v1_prefix(source):
    """Read the `API_V1_PREFIX = "..."` literal out of client.py's AST, so the
    prefix this checker applies to `direct=True` calls is the one the client
    actually sends. Absence is a setup error, not a reason to guess."""
    for node in ast.walk(ast.parse(source, CLIENT_PY)):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id == "API_V1_PREFIX"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                return node.value.value
    fail(f'{CLIENT_PY}: no `API_V1_PREFIX = "..."` string constant found')


def package_modules():
    """Every `.py` file in the package, subpackages included.

    Recursive on purpose. The package is flat today, so a top-level listing would
    return the same set — but a future subpackage (a `ws/` streaming client is the
    obvious candidate) would be invisible to a flat scan, and invisible is the one
    failure mode this checker must not have: its calls would go unattributed, the
    manifest would never be asked to list them, and the run would still pass. That
    is a silent undercount, not a loud one, so the recursion is what keeps every
    other guarantee here honest."""
    mods = []
    for dirpath, dirnames, filenames in os.walk(PACKAGE):
        # Generated trees hold stale copies of real modules; walking them would
        # double-count operations and attribute them to paths nobody edits.
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        mods.extend(os.path.join(dirpath, name) for name in filenames if name.endswith(".py"))
    if not mods:
        fail(f"no Python modules found under {PACKAGE}")
    return sorted(mods)


def literal_path(node):
    """The path a `_request` argument resolves to, with every f-string
    interpolation collapsed to `{}`. Returns None if the value is not a literal —
    a computed path cannot be attributed to an operation, and the caller fails
    loudly rather than dropping it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{}")
            else:
                return None
        return "".join(parts)
    return None


class RequestCall(NamedTuple):
    """One `_request` / `_request_page` call site: where it is, and which
    credential it sends. `auth` is one of the three `AUTH_*` constants."""

    where: str
    auth: str


def requested_ops(modules, api_v1_prefix):
    """Every operation the package requests, derived from the `_request(...)` calls
    in its modules. Returns {(METHOD, normalized_path): [RequestCall, ...]}.

    Anything that cannot be attributed to a concrete operation is a hard failure:
    a non-literal method, a computed path, a non-literal `direct` (which decides
    the /api/v1 prefix), or a non-literal `signed` (which decides the credential).
    Silently skipping such a call is how a manifest starts describing something
    other than reality."""
    ops = {}
    for path in modules:
        try:
            with open(path) as f:
                source = f.read()
        except OSError as e:
            fail(f"cannot read {path}: {e}")
        rel = os.path.relpath(path, REPO)
        for node in ast.walk(ast.parse(source, path)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in REQUEST_FUNCS
            ):
                continue
            where = f"{rel}:{node.lineno}"
            called = node.func.attr
            if called == PAGE_REQUEST_FUNC:
                # `_request_page(path, ...)` - path first, method implicit. Every
                # paginated read is a GET; `_request_page` hard-codes it when it
                # calls the sender, so reading it from the call site would be
                # inventing a degree of freedom the code does not have.
                if len(node.args) < 1:
                    fail(
                        f"{where}: {PAGE_REQUEST_FUNC}() called with no positional "
                        f"argument; the checker reads (path) positionally."
                    )
                method, path_node = PAGE_REQUEST_METHOD, node.args[0]
            else:
                if len(node.args) < 2:
                    fail(
                        f"{where}: {REQUEST_FUNC}() called with fewer than two positional "
                        f"arguments; the checker reads (method, path) positionally."
                    )
                method_node, path_node = node.args[0], node.args[1]
                if not (
                    isinstance(method_node, ast.Constant) and isinstance(method_node.value, str)
                ):
                    fail(
                        f"{where}: HTTP method is not a string literal; cannot attribute the call."
                    )
                method = method_node.value.upper()
                if method not in HTTP_METHODS:
                    fail(f"{where}: unknown HTTP method {method_node.value!r}")
            literal = literal_path(path_node)
            if literal is None:
                fail(
                    f"{where}: request path is not a literal (or f-string of "
                    f"literals); cannot attribute the call to an operation."
                )
            direct = False
            signed = False
            bearer = False
            for kw in node.keywords:
                if kw.arg is None:
                    fail(
                        f"{where}: {called}() called with **kwargs; the checker "
                        f"cannot tell what `direct`, `signed` or `bearer` are set to."
                    )
                if kw.arg in ("direct", "signed"):
                    if not (
                        isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool)
                    ):
                        why = (
                            f"the {api_v1_prefix} prefix cannot be resolved"
                            if kw.arg == "direct"
                            else "the credential this call sends cannot be resolved"
                        )
                        fail(f"{where}: `{kw.arg}=` is not a True/False literal, so {why}.")
                    if kw.arg == "direct":
                        direct = kw.value.value
                    else:
                        signed = kw.value.value
                elif kw.arg == "bearer":
                    # Presence decides, not the value: the token is a runtime
                    # secret (`create_api_key` passes the caller's session token),
                    # so unlike `signed=` it cannot be a literal to read. An
                    # explicit `bearer=None` is the parameter's own default and
                    # sends no credential, so it reads as absent.
                    bearer = not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
            if signed and bearer:
                # `_send` raises on this pair at runtime; refusing it here too means
                # the walk never has to guess which of two credentials a call sends.
                fail(
                    f"{where}: {called}() passes both `signed=True` and `bearer=`; a "
                    f"request carries one credential, and {SENDING_FUNC}() refuses this "
                    f"pair at runtime."
                )
            auth = AUTH_HMAC if signed else (AUTH_BEARER if bearer else AUTH_PUBLIC)
            resolved = f"{api_v1_prefix}{literal}" if direct else literal
            ops.setdefault((method, normalize_path(resolved)), []).append(RequestCall(where, auth))
    if not ops:
        fail(
            f"parsed zero {'/'.join(REQUEST_FUNCS)}() calls from the package; the call shape may "
            f"have changed — update this checker before trusting a green run."
        )
    return ops


def check_single_entry_point(modules):
    """Every httpx call must sit inside `_send`, the single sending site. A method
    that reached the transport directly would target an operation no AST walk over
    the entry points can see, so the code<->manifest equality would pass while
    under-counting.

    `_send` rather than `_request` since ENG-8081: the sending half was split out
    so `_request` and `_request_page` could share it. One sender, two entry points
    - the invariant this guards is unchanged."""
    errors = 0
    for path in modules:
        rel = os.path.relpath(path, REPO)
        with open(path) as f:
            tree = ast.parse(f.read(), path)
        # Map each node to the function that lexically encloses it.
        enclosing = {}
        for func in ast.walk(tree):
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(func):
                    enclosing.setdefault(child, func.name)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            handle = node.func.value
            if not (isinstance(handle, ast.Attribute) and handle.attr == HTTP_HANDLE):
                continue
            if node.func.attr not in HTTP_SENDING_CALLS:
                continue  # lifecycle (e.g. `close`), not a request
            if enclosing.get(node) != SENDING_FUNC:
                errors += 1
                print(
                    f"\nERROR: {rel}:{node.lineno}: `self.{HTTP_HANDLE}."
                    f"{node.func.attr}()` is called outside {SENDING_FUNC}(); route it "
                    f"through {SENDING_FUNC}() (reached via {REQUEST_FUNC}() or "
                    f"{PAGE_REQUEST_FUNC}()) so the operation stays visible to the "
                    f"drift check."
                )
    return errors


def check_allowlist_empty():
    """The delete-only policy (ENG-8616): `CODE_ONLY_OPS` must be empty.

    This replaces two staleness checks — the entry lost its caller, the pinned spec
    gained the operation — and covers the hole they left. Both only fire when
    something *changes*; an operation that has never appeared in any spec version,
    and never will, satisfies neither and sits green forever. That is how both of
    this SDK's entries survived several spec generations. A flat "must be empty" has
    no such blind spot."""
    if not CODE_ONLY_OPS:
        return 0
    print(
        f"\nERROR: CODE_ONLY_OPS holds {len(CODE_ONLY_OPS)} entr(ies) and must be "
        f"EMPTY (ENG-8616): an operation the pinned spec does not define must not be "
        f"implemented. Delete the method — or, once a published spec version defines "
        f"the operation, implement it against that path and list it in endpoints.txt:"
    )
    for m, p in sorted(CODE_ONLY_OPS):
        print(f"  - {m} {p}")
    return len(CODE_ONLY_OPS)


def check_code_vs_manifest(manifest):
    """Invariant 2: requested operations == manifest, modulo NON_REST_TARGETS.

    Takes no spec argument: with `CODE_ONLY_OPS` empty by policy there is nothing
    left in this invariant that depends on what the spec declares. Invariant 1 in
    `main` is the one that compares against the spec."""
    with open(CLIENT_PY) as f:
        api_v1_prefix = read_api_v1_prefix(f.read())
    modules = package_modules()
    requested = requested_ops(modules, api_v1_prefix)
    requested_set = set(requested)
    manifest_norm = {(m, normalize_path(p)) for m, p in manifest}

    errors = check_single_entry_point(modules) + check_allowlist_empty()

    # (a) requested but unlisted. No exemption: an operation the pinned spec does
    #     not define is deleted, not parked (ENG-8616), so every request the client
    #     issues must have a manifest line.
    unlisted = sorted(requested_set - manifest_norm)
    # (b) listed but never requested, and not a documented non-REST target.
    uncalled = sorted(manifest_norm - requested_set - NON_REST_TARGETS)
    # (c) a non-REST target the manifest no longer lists.
    orphaned_non_rest = sorted(NON_REST_TARGETS - manifest_norm)

    if unlisted:
        errors += len(unlisted)
        print(
            f"\nERROR: {len(unlisted)} operation(s) the client requests are NOT in "
            f"endpoints.txt (add the line if the pinned spec defines the operation; "
            f"delete the method if it does not):"
        )
        for m, p in unlisted:
            print(f"  - {m} {p}   requested at {', '.join(c.where for c in requested[(m, p)])}")

    if uncalled:
        errors += len(uncalled)
        print(
            f"\nERROR: {len(uncalled)} endpoints.txt entr(ies) are never requested by "
            f"the client (remove them, or add to NON_REST_TARGETS if reached without "
            f"a {REQUEST_FUNC}() call):"
        )
        for m, p in uncalled:
            print(f"  - {m} {p}")

    if orphaned_non_rest:
        errors += len(orphaned_non_rest)
        print(
            f"\nERROR: {len(orphaned_non_rest)} NON_REST_TARGETS entr(ies) are not "
            f"listed in endpoints.txt (remove them from the allowlist):"
        )
        for m, p in orphaned_non_rest:
            print(f"  - {m} {p}")

    if not errors:
        print(
            f"\nOK: the client requests {len(requested_set)} operation(s), each with an "
            f"endpoints.txt line; every endpoints.txt entry has a caller or is in "
            f"NON_REST_TARGETS; and CODE_ONLY_OPS is empty."
        )
    return errors


def security_alternatives(spec, operation):
    """The alternatives `operation` accepts, as a set of frozensets of scheme names.

    OpenAPI's `security` is a list of ALTERNATIVES (any one suffices), each a map
    of scheme -> scopes that must ALL be presented. So `[{a: []}, {b: []}]` means
    "a or b" while `[{a: [], b: []}]` means "a and b" — a distinction that matters
    here, because the client sends exactly one credential and therefore can never
    satisfy an alternative naming two schemes.

    An empty frozenset is the public alternative. It arises three ways, all one
    meaning: `security: []`, an empty `{}` entry, and no `security` at all (with no
    root-level `security` to inherit). Absent-at-both-levels is public per the
    specification, not "unspecified" — this document declares no root `security`,
    so all 27 of its public operations reach us through the first two spellings and
    the four with no key at all through the third."""
    absent = object()
    declared = operation.get("security", spec.get("security", absent))
    if declared is absent:
        return frozenset({frozenset()})
    if declared is None:
        # `security: null` is not a spelling of anything — reading it as public
        # would be a guess, in the direction that hides a requirement.
        fail("`security` is present but null; it must be a list of requirements")
    if not isinstance(declared, list):
        fail(f"`security` must be a list, got {type(declared).__name__}: {declared!r}")
    if not declared:
        return frozenset({frozenset()})
    alternatives = set()
    for entry in declared:
        if not isinstance(entry, dict):
            fail(f"every `security` entry must be an object, got {entry!r}")
        alternatives.add(frozenset(entry))
    return frozenset(alternatives)


def render_declaration(alternatives):
    """One canonical string per declaration, so two declarations compare (and pin)
    by equality: `"hmacAuth"`, `"public"`, `"hmacAuth | bearerAuth"` for a choice,
    `"a+b"` for an alternative requiring both."""
    return " | ".join(
        sorted("+".join(sorted(alt)) if alt else PUBLIC_DECLARATION for alt in alternatives)
    )


def satisfies(auth, alternatives):
    """Whether one credential satisfies a declaration.

    The client presents a single credential, so it satisfies the declaration only
    if some alternative is exactly that one scheme. Public counts as a credential
    of its own: it satisfies only a declaration that offers the public alternative,
    which is what makes signing a declared-public operation a finding rather than
    harmless extra caution — it costs a keyless caller the endpoint entirely."""
    scheme = SCHEME_FOR_AUTH.get(auth)
    required = frozenset() if scheme is None else frozenset({scheme})
    return required in alternatives


def declared_security_index(spec):
    """{(METHOD, normalized_path): {rendered: (alternatives, [literal spec paths])}}.

    Normalized so it can be keyed by what `requested_ops` reports (the client's
    f-string interpolations carry no placeholder NAME, so `{id}` vs `{deposit_id}`
    cannot be matched any other way). Collapsing names can in principle merge two
    distinct spec paths, so the value keeps every declaration seen for a key: more
    than one means the operation is ambiguous under normalization, and the check
    below refuses to pick rather than reporting a coin-flip."""
    index = {}
    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.upper() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            key = (method.upper(), normalize_path(path))
            alternatives = security_alternatives(spec, operation)
            rendered = render_declaration(alternatives)
            index.setdefault(key, {}).setdefault(rendered, (alternatives, []))[1].append(path)
    return index


def check_declared_security(spec):
    """Invariant 3: every call's credential satisfies the operation's `security`.

    Runs its own AST walk rather than taking `check_code_vs_manifest`'s: the two
    answer different questions (which operations, versus how each is authenticated)
    and keeping them independent means either can be exercised — and can fail —
    without the other."""
    with open(CLIENT_PY) as f:
        api_v1_prefix = read_api_v1_prefix(f.read())
    requested = requested_ops(package_modules(), api_v1_prefix)
    index = declared_security_index(spec)

    errors = 0
    accepted = []
    for op, calls in sorted(requested.items()):
        method, path = op
        sent = {call.auth for call in calls}
        if len(sent) > 1:
            # One operation, two credentials, decided by which method you call.
            # Whatever the server accepts, the SDK cannot be described as doing
            # one thing here, so it is reported rather than measured.
            errors += 1
            print(
                f"\nERROR: {method} {path} is requested with more than one credential "
                f"({', '.join(sorted(sent))}); one operation, one credential:"
            )
            for call in sorted(calls):
                print(f"  - {call.auth:<7} at {call.where}")
            continue
        auth = next(iter(sent))
        declarations = index.get(op)
        if not declarations:
            # Unreachable while invariants 1 and 2 hold — they already require every
            # requested operation to have a manifest line that the spec declares —
            # so this fires only alongside a louder failure. Reported anyway: an
            # unverifiable call must never read as a verified one.
            errors += 1
            print(
                f"\nERROR: {method} {path} is requested at "
                f"{', '.join(c.where for c in calls)} but the pinned spec declares no "
                f"such operation, so its credential cannot be checked."
            )
            continue
        if len(declarations) > 1:
            errors += 1
            print(
                f"\nERROR: {method} {path} matches spec paths that declare different "
                f"security, so the declaration to check against is ambiguous:"
            )
            for rendered, (_, paths) in sorted(declarations.items()):
                print(f"  - {rendered}: {', '.join(sorted(paths))}")
            continue
        rendered, (alternatives, _) = next(iter(declarations.items()))
        agrees = satisfies(auth, alternatives)
        pinned = SECURITY_EXCEPTIONS.get(op)

        if agrees and pinned is not None:
            errors += 1
            print(
                f"\nERROR: {method} {path} now sends {auth} and the pinned spec declares "
                f"{rendered}: they AGREE, so the SECURITY_EXCEPTIONS entry recording "
                f"their disagreement is stale. Delete it — and close the ENG-13303 row "
                f"it stands for."
            )
        elif not agrees and pinned == (auth, rendered):
            accepted.append((op, auth, rendered))
        elif not agrees:
            errors += 1
            known = (
                f" (SECURITY_EXCEPTIONS pins {pinned[0]} vs {pinned[1]}, which is no "
                f"longer what either side says)"
                if pinned is not None
                else ""
            )
            print(
                f"\nERROR: {method} {path} sends {auth} but the pinned spec declares "
                f"{rendered}{known}. Requested at "
                f"{', '.join(c.where for c in calls)}."
            )
            print(
                "  Fix the client if the spec is authoritative; if the spec "
                "over-declares, widen it upstream and re-pin. Adding a "
                "SECURITY_EXCEPTIONS entry is for a divergence that is filed and "
                "awaiting that decision — not for one nobody has looked at."
            )

    orphaned = sorted(set(SECURITY_EXCEPTIONS) - set(requested))
    if orphaned:
        errors += len(orphaned)
        print(
            f"\nERROR: {len(orphaned)} SECURITY_EXCEPTIONS entr(ies) name an operation "
            f"the client no longer requests (remove them):"
        )
        for method, path in orphaned:
            print(f"  - {method} {path}")

    if accepted:
        # Printed on a green run too. A tolerated divergence that nobody is shown
        # is a tolerated divergence that nobody resolves.
        print(
            f"\nKNOWN auth divergences, pinned in SECURITY_EXCEPTIONS and awaiting a "
            f"contract decision (ENG-13303) — {len(accepted)}:"
        )
        for (method, path), auth, rendered in accepted:
            print(f"  - {method} {path}: sends {auth}, spec declares {rendered}")

    if not errors:
        print(
            f"\nOK: {len(requested) - len(accepted)} of {len(requested)} requested "
            f"operation(s) send a credential the pinned spec's `security` accepts; the "
            f"other {len(accepted)} are the pinned, known divergences above."
        )
    return errors


def check_pin_matches_spec(spec, pinned):
    """Invariant 0: the spec file handed to us is the pinned release."""
    declared = spec.get("info", {}).get("version")
    if not isinstance(declared, str) or not re.fullmatch(r"[0-9]+(\.[0-9]+){0,2}", declared):
        fail(f"spec has no usable info.version (got: {declared!r})")
    if version_tuple(declared) != version_tuple(pinned):
        fail(
            f"spec declares version {declared!r} but .api-version pins {pinned!r}; "
            f"the wrong spec was fetched (or a cached copy is stale), so no result "
            f"from this run can be trusted."
        )
    return declared


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <openapi.json>")
    try:
        with open(sys.argv[1]) as f:
            spec = json.load(f)
    except OSError as e:
        fail(f"cannot read {sys.argv[1]}: {e}")
    except json.JSONDecodeError as e:
        fail(f"{sys.argv[1]} is not valid JSON: {e}")

    pinned = read_pinned_tag()
    declared = check_pin_matches_spec(spec, pinned)
    manifest = load_manifest()
    available = spec_ops(spec)

    print(f"Pinned spec: {pinned} (openapi.json declares {declared})")

    with open(CLIENT_PY) as f:
        api_v1_prefix = read_api_v1_prefix(f.read())
    cov = coverage_figures(manifest, available, api_v1_prefix)
    pct = 100.0 * len(cov["covered"]) / len(cov["spec"]) if cov["spec"] else 0.0
    print(
        f"The Python SDK implements {len(cov['covered'])} of {len(cov['spec'])} "
        f"spec operations ({pct:.1f}% coverage) — {len(available)} documented "
        f"paths, {cov['spellings']} of them a second spelling of an operation "
        f"already counted."
    )

    # Invariant 1: manifest -> pinned spec, matched exactly. Literal paths, NOT
    # canonicalized: the manifest must name a path the spec really declares.
    missing = [op for op in manifest if op not in available]
    uncovered = cov["uncovered"]

    if uncovered:
        print(f"\nNot yet implemented by the Python SDK ({len(uncovered)}):")
        for m, p in uncovered:
            print(f"  - {m} {p}")
    else:
        print("\nOK: every spec operation is implemented by the Python SDK.")

    failures = 0
    if missing:
        failures += len(missing)
        print(
            f"\nERROR: {len(missing)} endpoints.txt entr(ies) are NOT in the pinned "
            f"spec (removed, renamed, or a typo — including a placeholder-name "
            f"mismatch):"
        )
        for m, p in missing:
            print(f"  - {m} {p}")
    else:
        print("\nOK: every endpoints.txt entry exists in the pinned spec.")

    # Invariant 2: client code <-> endpoints.txt.
    failures += check_code_vs_manifest(manifest)

    # Invariant 3: the credential each call sends <-> the spec's `security`.
    failures += check_declared_security(spec)

    if failures:
        print(f"\nFAILED: {failures} drift error(s).")
        sys.exit(1)
    print("\nPASSED: no spec drift.")


if __name__ == "__main__":
    main()
