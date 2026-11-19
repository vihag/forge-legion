"""Assert triage-seed stdout and case list state (stdlib only)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SEED_ALERT_ID = "SPL-2026-0001"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: assert_seed_triaged.py <triage.json>", file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8") as handle:
        payload = json.loads(handle.read())
    outcome = payload.get("outcome")
    # With triage_worker up, the seed alert may already be TRIAGED (C3 idempotency).
    if outcome not in ("triaged", "skipped_existing"):
        print(f"expected outcome triaged or skipped_existing, got {outcome!r}", file=sys.stderr)
        return 1
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    query = urllib.parse.urlencode({"alert_id": SEED_ALERT_ID})
    url = f"{base_url}/case/v1/cases?{query}"
    try:
        with urllib.request.urlopen(url) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"case list request failed: {exc}", file=sys.stderr)
        return 1
    total = body.get("total")
    items = body.get("items", [])
    if total != 1:
        print(f"expected total 1, got {total!r}", file=sys.stderr)
        return 1
    if not items:
        print("expected one case item", file=sys.stderr)
        return 1
    if items[0].get("status") != "TRIAGED":
        print(f"expected status TRIAGED, got {items[0].get('status')!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())