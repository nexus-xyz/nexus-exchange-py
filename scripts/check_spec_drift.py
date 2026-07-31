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

Three invariants are enforced:

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
   modulo two explicit, documented allowlists:

     * CODE_ONLY_OPS    — implemented, deliberately NOT in the manifest because
                          the pinned spec does not define the operation (listing
                          it would correctly fail invariant 1).
     * NON_REST_TARGETS — listed in the manifest but reached WITHOUT a
                          `_request` call, so the AST cannot (and should not) see
                          it.

   Both allowlists are checked for staleness, so an exemption cannot outlive its
   reason: a CODE_ONLY_OPS entry that no longer has a caller, or that the pinned
   spec has since gained, fails — as does a NON_REST_TARGETS entry the manifest no
   longer lists.

Usage: check_spec_drift.py <openapi.json>
"""

import ast
import json
import os
import re
import sys

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

# Implemented by the client but deliberately absent from endpoints.txt, because the
# pinned spec defines no such operation — listing one here would (correctly) fail
# invariant 1. Two distinct reasons, both intentional:
#
#   POST /account/leverage — AHEAD of the pinned spec. `set_leverage` ships against
#     a route the spec has not published yet (ENG-5296). Move it into endpoints.txt
#     once the pinned spec gains the operation; the staleness check below fails the
#     build when that happens, so this cannot be forgotten.
#   GET /health — OUTSIDE the contract. An operational probe of the legacy gateway
#     (`health_check`), never a spec operation, and v0.7.1 replaced it upstream with
#     `GET /status`. It stays out of the manifest so the coverage figure only ever
#     counts real contract operations.
CODE_ONLY_OPS = {
    ("POST", "/account/leverage"),
    ("GET", "/health"),
}

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


def requested_ops(modules, api_v1_prefix):
    """Every operation the package requests, derived from the `_request(...)` calls
    in its modules. Returns {(METHOD, normalized_path): [locations]}.

    Anything that cannot be attributed to a concrete operation is a hard failure:
    a non-literal method, a computed path, or a non-literal `direct` (which decides
    the /api/v1 prefix). Silently skipping such a call is how a manifest starts
    describing something other than reality."""
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
                and node.func.attr == REQUEST_FUNC
            ):
                continue
            where = f"{rel}:{node.lineno}"
            if len(node.args) < 2:
                fail(
                    f"{where}: {REQUEST_FUNC}() called with fewer than two positional "
                    f"arguments; the checker reads (method, path) positionally."
                )
            method_node, path_node = node.args[0], node.args[1]
            if not (isinstance(method_node, ast.Constant) and isinstance(method_node.value, str)):
                fail(f"{where}: HTTP method is not a string literal; cannot attribute the call.")
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
            for kw in node.keywords:
                if kw.arg is None:
                    fail(
                        f"{where}: {REQUEST_FUNC}() called with **kwargs; the checker "
                        f"cannot tell whether `direct` is set."
                    )
                if kw.arg == "direct":
                    if not (
                        isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool)
                    ):
                        fail(
                            f"{where}: `direct=` is not a True/False literal, so the "
                            f"{api_v1_prefix} prefix cannot be resolved."
                        )
                    direct = kw.value.value
            resolved = f"{api_v1_prefix}{literal}" if direct else literal
            ops.setdefault((method, normalize_path(resolved)), []).append(where)
    if not ops:
        fail(
            f"parsed zero {REQUEST_FUNC}() calls from the package; the call shape may "
            f"have changed — update this checker before trusting a green run."
        )
    return ops


def check_single_entry_point(modules):
    """Every httpx call must sit inside `_request`. A method that reached the
    transport directly would target an operation no AST walk over `_request` calls
    can see, so the code<->manifest equality would pass while under-counting."""
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
            if enclosing.get(node) != REQUEST_FUNC:
                errors += 1
                print(
                    f"\nERROR: {rel}:{node.lineno}: `self.{HTTP_HANDLE}."
                    f"{node.func.attr}()` is called outside {REQUEST_FUNC}(); route it "
                    f"through {REQUEST_FUNC}() so the operation stays visible to the "
                    f"drift check."
                )
    return errors


def check_code_vs_manifest(manifest, available):
    """Invariant 2: requested operations == manifest, modulo the two allowlists."""
    with open(CLIENT_PY) as f:
        api_v1_prefix = read_api_v1_prefix(f.read())
    modules = package_modules()
    requested = requested_ops(modules, api_v1_prefix)
    requested_set = set(requested)
    manifest_norm = {(m, normalize_path(p)) for m, p in manifest}
    available_norm = {(m, normalize_path(p)) for m, p in available}

    errors = check_single_entry_point(modules)

    # (a) requested but unlisted, and not a documented code-only operation.
    unlisted = sorted(requested_set - manifest_norm - CODE_ONLY_OPS)
    # (b) listed but never requested, and not a documented non-REST target.
    uncalled = sorted(manifest_norm - requested_set - NON_REST_TARGETS)
    # (c) an allowlisted code-only operation nothing requests any more.
    orphaned = sorted(CODE_ONLY_OPS - requested_set)
    # (d) an allowlisted code-only operation the pinned spec now defines: its
    #     reason for being exempt is gone, so it belongs in the manifest.
    now_in_spec = sorted(op for op in CODE_ONLY_OPS if op in available_norm)
    # (e) a non-REST target the manifest no longer lists.
    orphaned_non_rest = sorted(NON_REST_TARGETS - manifest_norm)

    if unlisted:
        errors += len(unlisted)
        print(
            f"\nERROR: {len(unlisted)} operation(s) the client requests are NOT in "
            f"endpoints.txt (add them, or add to CODE_ONLY_OPS if the pinned spec "
            f"does not define them):"
        )
        for m, p in unlisted:
            print(f"  - {m} {p}   requested at {', '.join(requested[(m, p)])}")

    if uncalled:
        errors += len(uncalled)
        print(
            f"\nERROR: {len(uncalled)} endpoints.txt entr(ies) are never requested by "
            f"the client (remove them, or add to NON_REST_TARGETS if reached without "
            f"a {REQUEST_FUNC}() call):"
        )
        for m, p in uncalled:
            print(f"  - {m} {p}")

    if orphaned:
        errors += len(orphaned)
        print(
            f"\nERROR: {len(orphaned)} CODE_ONLY_OPS entr(ies) are no longer requested "
            f"by any method (remove them from the allowlist):"
        )
        for m, p in orphaned:
            print(f"  - {m} {p}")

    if now_in_spec:
        errors += len(now_in_spec)
        print(
            f"\nERROR: {len(now_in_spec)} CODE_ONLY_OPS entr(ies) now EXIST in the "
            f"pinned spec; move them into endpoints.txt (that is what the allowlist "
            f"was waiting for) and drop them from CODE_ONLY_OPS:"
        )
        for m, p in now_in_spec:
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
            f"\nOK: the client requests {len(requested_set)} operation(s); each is in "
            f"endpoints.txt or CODE_ONLY_OPS, every endpoints.txt entry has a caller "
            f"or is in NON_REST_TARGETS, and both allowlists are still earning their "
            f"exemptions."
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
    pct = 100.0 * len(manifest) / len(available)
    print(
        f"The Python SDK implements {len(manifest)} of {len(available)} spec "
        f"operations ({pct:.1f}% coverage)."
    )

    # Invariant 1: manifest -> pinned spec, matched exactly.
    missing = [op for op in manifest if op not in available]
    uncovered = sorted(available - set(manifest))

    if uncovered:
        print(f"\nNot yet implemented by the Python SDK ({len(uncovered)}):")
        for m, p in uncovered:
            print(f"  - {m} {p}")

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
    failures += check_code_vs_manifest(manifest, available)

    if failures:
        print(f"\nFAILED: {failures} drift error(s).")
        sys.exit(1)
    print("\nPASSED: no spec drift.")


if __name__ == "__main__":
    main()
