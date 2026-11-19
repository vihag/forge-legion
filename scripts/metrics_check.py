"""Compare live /case/v1/metrics against values recomputed from case views (stdlib)."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from typing import Any

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_TIMING_TOLERANCE_S = 0.1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify /case/v1/metrics against case views")
    parser.add_argument(
        "--base",
        required=True,
        help="Gateway base URL (e.g. http://localhost:8080)",
    )
    return parser.parse_args(argv)


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode("utf-8"))


def _list_cases(base: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 50
    while True:
        query = urllib.parse.urlencode({"limit": limit, "offset": offset})
        page = _fetch_json(f"{base}/case/v1/cases?{query}")
        batch = page.get("items", [])
        if not isinstance(batch, list):
            raise TypeError("expected items list")
        items.extend(batch)
        total = page.get("total", 0)
        if not isinstance(total, int):
            raise TypeError("expected total int")
        offset += limit
        if offset >= total:
            break
    return items


def _view(base: str, case_id: int) -> dict[str, Any]:
    body = _fetch_json(f"{base}/case/v1/cases/{case_id}/view")
    if not isinstance(body, dict):
        raise TypeError("expected view object")
    return body


def _timing_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "average": None}
    return {
        "n": len(values),
        "median": round(float(statistics.median(values)), 1),
        "average": round(sum(values) / len(values), 1),
    }


def _seconds_between(start: str, end: str) -> float:
    def _to_iso(value: str) -> str:
        return f"{value[:-1]}+00:00" if value.endswith("Z") else value

    from datetime import datetime

    begin = datetime.fromisoformat(_to_iso(start))
    finish = datetime.fromisoformat(_to_iso(end))
    return (finish - begin).total_seconds()


def _recompute(base: str) -> dict[str, Any]:
    summaries = _list_cases(base)
    views = [_view(base, int(item["id"])) for item in summaries]
    by_status: dict[str, int] = {}
    auto_resolved = 0
    uplifts: dict[str, int] = {}
    downgrades = 0
    triage_times: list[float] = []
    ai_only_times: list[float] = []
    ai_plus_human_times: list[float] = []

    for view in views:
        status = str(view.get("status"))
        by_status[status] = by_status.get(status, 0) + 1
        if status == "AUTO_RESOLVED":
            auto_resolved += 1
        splunk = view.get("splunk_severity")
        triage = view.get("triage_severity")
        if isinstance(splunk, str) and isinstance(triage, str):
            splunk_rank = _SEVERITY_RANK[splunk]
            triage_rank = _SEVERITY_RANK[triage]
            if triage_rank > splunk_rank:
                key = f"{splunk}->{triage}"
                uplifts[key] = uplifts.get(key, 0) + 1
            elif triage_rank < splunk_rank:
                downgrades += 1
        start = view.get("alert_time") or view.get("created_at")
        if not isinstance(start, str):
            continue
        audit = view.get("audit", [])
        if not isinstance(audit, list):
            continue
        triage_ts = next((e["ts"] for e in audit if e.get("type") == "case.triaged"), None)
        if isinstance(triage_ts, str):
            triage_times.append(_seconds_between(start, triage_ts))
        auto_ts = next((e["ts"] for e in audit if e.get("type") == "case.auto_resolved"), None)
        if isinstance(auto_ts, str):
            ai_only_times.append(_seconds_between(start, auto_ts))
        approve_ts = next((e["ts"] for e in audit if e.get("type") == "disposition.approved"), None)
        if isinstance(approve_ts, str):
            ai_plus_human_times.append(_seconds_between(start, approve_ts))

    return {
        "counts": {
            "total": len(views),
            "by_status": by_status,
            "auto_resolved": auto_resolved,
        },
        "uplifts": uplifts,
        "downgrades": downgrades,
        "time_to_triage_s": _timing_stats(triage_times),
        "time_to_resolve_s": {
            "ai_only": _timing_stats(ai_only_times),
            "ai_plus_human": _timing_stats(ai_plus_human_times),
        },
    }


def _timing_close(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("n") != right.get("n"):
        return False
    if left.get("n") == 0:
        return right.get("n") == 0
    for key in ("median", "average"):
        lv = left.get(key)
        rv = right.get(key)
        if lv is None or rv is None:
            return lv == rv
        if abs(float(lv) - float(rv)) > _TIMING_TOLERANCE_S:
            return False
    return True


def _compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for key in ("total", "auto_resolved"):
        if expected["counts"][key] != actual["counts"][key]:
            diffs.append(f"counts.{key} {actual['counts'][key]} != {expected['counts'][key]}")
    for status, count in expected["counts"]["by_status"].items():
        live = actual["counts"]["by_status"].get(status, 0)
        if live != count:
            diffs.append(f"counts.by_status.{status} {live} != {count}")
    if expected["uplifts"] != actual.get("uplifts", {}):
        diffs.append(f"uplifts {actual.get('uplifts')} != {expected['uplifts']}")
    if expected["downgrades"] != actual.get("downgrades"):
        diffs.append("downgrades mismatch")
    if not _timing_close(expected["time_to_triage_s"], actual.get("time_to_triage_s", {})):
        diffs.append("time_to_triage_s mismatch")
    resolve = actual.get("time_to_resolve_s", {})
    for block in ("ai_only", "ai_plus_human"):
        if not _timing_close(
            expected["time_to_resolve_s"][block],
            resolve.get(block, {}),
        ):
            diffs.append(f"time_to_resolve_s.{block} mismatch")
    return diffs


def _auto_resolved_soar_clear(base: str) -> list[str]:
    issues: list[str] = []
    summaries = _list_cases(base)
    for item in summaries:
        if item.get("status") != "AUTO_RESOLVED":
            continue
        case_id = item["id"]
        query = urllib.parse.urlencode({"case_id": case_id})
        soar = _fetch_json(f"{base}/soar/v1/submissions?{query}")
        items = soar.get("items", [])
        if items:
            issues.append(f"case {case_id} AUTO_RESOLVED but SOAR has submissions")
    return issues


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base = args.base.rstrip("/")
    expected = _recompute(base)
    actual = _fetch_json(f"{base}/case/v1/metrics")
    diffs = _compare(expected, actual)
    diffs.extend(_auto_resolved_soar_clear(base))
    if diffs:
        for line in diffs:
            print(line, file=sys.stderr)
        return 1
    print("metrics match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())