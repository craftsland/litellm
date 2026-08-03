#!/usr/bin/env python3
"""Follow an AWS e2e run from Buildkite and surface it live.

The suite runs as a k8s Job in a private VPC, so Buildkite cannot watch it
directly. Instead the pod uploads partial JUnit to Test Engine as it goes
(same run_env[key]), and this polls Test Engine from the outside.

Two live surfaces come out of it: this job's own log, which streams in the
Buildkite UI, and an annotation on the build page that is rewritten in place
each poll so the failure list stays current rather than accumulating.

Fails closed. No run for the commit by the deadline is a failure, never a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ORG = os.environ.get("E2E_ORG", "berriai-1")
SUITE = os.environ.get("E2E_SUITE", "litellm-e2e-aws")
DEADLINE_SECONDS = int(os.environ.get("E2E_DEADLINE_SECONDS", "10800"))
POLL_SECONDS = int(os.environ.get("E2E_POLL_SECONDS", "30"))
SHOW_FAILURES = int(os.environ.get("E2E_SHOW_FAILURES", "25"))
BASE = f"https://api.buildkite.com/v2/analytics/organizations/{ORG}/suites/{SUITE}"


def api(url: str) -> object:
    token = os.environ.get("BUILDKITE_API_TOKEN", "").strip()
    if not token:
        sys.exit("BUILDKITE_API_TOKEN is not set (needs read_suites; NOT the ingest token)")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "litellm-e2e-poller"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read()[:200].decode("utf-8", "replace")
        print(f"  (api {exc.code} on {url.rsplit('/', 1)[-1]}: {body})", file=sys.stderr)
        return None


def find_run(sha: str) -> dict[str, object] | None:
    runs = api(f"{BASE}/runs?per_page=100")
    if not isinstance(runs, list):
        return None
    matching = [r for r in runs if str(r.get("commit_sha", "")).startswith(sha[:12])]
    # Newest first; the API returns most-recent-first already, but be explicit.
    matching.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return matching[0] if matching else None


def failures_for(run_id: str) -> tuple[str, ...]:
    out: list[str] = []
    for page in range(1, 6):
        batch = api(f"{BASE}/runs/{run_id}/failed_executions?per_page=100&page={page}")
        if not isinstance(batch, list) or not batch:
            break
        out += [str(e.get("test_name") or e.get("test_id") or "?") for e in batch]
        if len(batch) < 100:
            break
    return tuple(out)


def annotate(style: str, body: str) -> None:
    """Rewrite the annotation in place. Same --context replaces, so the build
    page shows current state instead of one annotation per poll."""
    if not os.environ.get("BUILDKITE"):
        return
    subprocess.run(
        ["buildkite-agent", "annotate", body, "--style", style, "--context", "e2e-progress"],
        check=False,
    )


def render(run: dict[str, object], failures: tuple[str, ...], sha: str) -> str:
    state = run.get("state")
    head = f"**AWS e2e — `{sha[:10]}`** · state `{state}` · **{len(failures)} failed**"
    if not failures:
        return f"{head}\n\nNo failures reported yet."
    shown = failures[:SHOW_FAILURES]
    lines = [head, "", *(f"- `{name}`" for name in shown)]
    if len(failures) > len(shown):
        lines.append(f"- _…and {len(failures) - len(shown)} more_")
    lines.append(f"\n[Open run]({run.get('web_url', '')})")
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.stderr.write("usage: poll-e2e.py <commit-sha>\n")
        return 2
    sha = sys.argv[1].strip()

    print(f"e2e: following {SUITE} for commit {sha}")
    deadline = time.monotonic() + DEADLINE_SECONDS
    seen_finished = 0
    run: dict[str, object] | None = None

    while time.monotonic() < deadline:
        run = find_run(sha)
        if run is None:
            print("e2e: no run for this commit yet, waiting…", flush=True)
            time.sleep(POLL_SECONDS)
            continue

        run_id = str(run["id"])
        failures = failures_for(run_id)
        state = str(run.get("state"))
        print(f"e2e: state={state} failures={len(failures)}", flush=True)
        annotate("error" if failures else "info", render(run, failures, sha))

        # state flips back to running when more results upload, so require it
        # to hold at finished across two polls before calling the run done.
        seen_finished = seen_finished + 1 if state == "finished" else 0
        if seen_finished >= 2:
            break
        time.sleep(POLL_SECONDS)

    if run is None:
        annotate("error", f"**AWS e2e — no run found for `{sha[:10]}`**\n\nThe suite never reported. Treating as failed.")
        print(f"e2e: no run for {sha} within {DEADLINE_SECONDS}s", file=sys.stderr)
        return 1

    failures = failures_for(str(run["id"]))
    result = str(run.get("result"))
    print(f"\ne2e: finished — result={result}, {len(failures)} failed")
    annotate("error" if failures else "success", render(run, failures, sha))
    return 0 if result == "passed" and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
