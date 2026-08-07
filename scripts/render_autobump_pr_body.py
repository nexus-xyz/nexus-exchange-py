#!/usr/bin/env python3
"""Render the spec-autobump PR body markdown.

Kept out of the workflow's inline shell so the markdown (dense with backticks and
`${...}` examples) isn't fighting shell quoting, and so the body is easy to
eyeball and diff. Driven by `.github/workflows/spec-autobump.yml` (ENG-7960; the
reference implementation is nexus-exchange-rs, ENG-3563).

Reads the captured oasdiff output from a file so the verbatim verdict lands in the
PR. Writes the rendered markdown to stdout.

Usage:
  render_autobump_pr_body.py --new-tag vX.Y.Z --old-tag vA.B.C \
      --verdict {non-breaking|breaking} --oasdiff-file PATH \
      --oasdiff-version 1.2.3 --auto-merge {armed|unavailable|not-attempted}
"""

import argparse
import sys

# GitHub rejects a PR body over 65536 characters; a spec release with a large
# breaking surface can produce a lot of oasdiff text. Truncate the embedded block
# (never the surrounding explanation) and say so, rather than failing to open the
# PR at all — the full output is in the workflow log either way.
MAX_OASDIFF_CHARS = 20000


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--new-tag", required=True)
    ap.add_argument("--old-tag", required=True)
    ap.add_argument("--verdict", required=True, choices=["non-breaking", "breaking"])
    ap.add_argument("--oasdiff-file", required=True)
    ap.add_argument("--oasdiff-version", required=True)
    ap.add_argument(
        "--auto-merge",
        required=True,
        choices=["armed", "unavailable", "not-attempted"],
        help=(
            "armed: auto-merge was armed. unavailable: the repo has auto-merge "
            "disabled, so it could not be. not-attempted: breaking, so by design."
        ),
    )
    args = ap.parse_args()

    try:
        with open(args.oasdiff_file) as f:
            oasdiff_out = f.read().strip() or "(no output captured)"
    except OSError:
        oasdiff_out = "(no output captured)"
    if len(oasdiff_out) > MAX_OASDIFF_CHARS:
        oasdiff_out = (
            oasdiff_out[:MAX_OASDIFF_CHARS]
            + "\n\n[truncated — see the workflow log for the full output]"
        )

    out = []
    out.append(
        f"nexus-exchange-api released **{args.new_tag}** "
        f"(was pinned at **{args.old_tag}**). Opened automatically by "
        f"`spec-autobump` (ENG-7960).\n"
    )
    out.append(f"### oasdiff verdict: **{args.verdict}**\n")
    out.append(
        f"Classified `{args.old_tag} -> {args.new_tag}` with "
        f"`oasdiff breaking --fail-on ERR` (oasdiff `{args.oasdiff_version}`, pinned "
        f"by the workflow) — the same gate the api repo runs as "
        f'"Classify API changes". ERR-level changes are breaking; WARN/INFO are '
        f"not.\n"
    )
    out.append("<details><summary>oasdiff breaking output</summary>\n")
    out.append(f"```\n{oasdiff_out}\n```\n")
    out.append("</details>\n")
    out.append("### Applied\n")
    out.append(f"- Bumped `.api-version` to `{args.new_tag}`.")
    out.append('- Updated the bot-managed "currently targets" line in the README.')
    out.append(
        f"- Bumped the baked `DEFAULT_API_VERSION` constant (the "
        f"`X-Nexus-Api-Version` header value) to `{args.new_tag}`, so the wheel — "
        f"which does not ship `.api-version` — advertises the same contract."
    )
    out.append(
        "- Left the README SDK<->spec compatibility table alone: it records what "
        "*released* versions shipped against, so a bare spec release changes "
        "nothing in it. A row is appended when a release goes out.\n"
    )
    out.append("### What verifies this\n")
    out.append(
        "The `spec-drift` check on this PR is the merge signal. It runs on every "
        "PR (no path filter), so it runs here too, and it enforces both directions "
        "against the **new** pin: every `endpoints.txt` entry exists in that spec, "
        "and the operations the client code requests are exactly that list. An "
        "additive release needs no SDK edits, so it stays green; it goes red "
        "precisely when the new spec dropped or renamed something this SDK "
        "implements.\n"
    )
    out.append(
        "If it does go red, the fix belongs on this branch: push the removals or "
        "renames the new spec implies. The bot deliberately touches only the pin, "
        "so a red drift is a request for human edits, not a bot bug.\n"
    )

    if args.verdict == "non-breaking":
        out.append("### Merge gating (non-breaking)\n")
        if args.auto_merge == "armed":
            out.append(
                "GitHub auto-merge has been **armed** (squash). It does NOT merge on "
                "its own — the PR can only merge once:\n"
            )
            out.append("- the required status checks pass (`spec-drift`, `test`, `lint`, …), and")
            out.append(
                "- the **ENG-4149 ruleset bypass** for this bot is configured to "
                "satisfy the review rules for pin-bump PRs only.\n"
            )
            out.append(
                "Until ENG-4149 lands, this PR sits green awaiting the bypass — "
                "auto-merge cannot fire. No premature merge."
            )
        else:
            out.append(
                "Auto-merge was **not armed**: this repository has auto-merge "
                "disabled (`allow_auto_merge: false`), so arming it would have been "
                "a silent no-op. **A human must merge this PR.**\n"
            )
            out.append(
                "To change that, enable *Allow auto-merge* in the repository "
                "settings; ENG-4149 (bot identity + ruleset bypass) still gates "
                "actual auto-landing after that."
            )
    else:
        out.append("### Merge gating (breaking)\n")
        out.append(
            f"oasdiff flagged an ERR-level (breaking) change, so auto-merge was "
            f"**NOT** armed and a reviewer was requested. A human owns this: review "
            f"what `{args.new_tag}` changes, make the SDK code changes it implies, "
            f"plan the SDK version bump (pre-1.0, a breaking change is a **minor**), "
            f"then merge. Labeled `breaking · needs-SDK-update`."
        )

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
