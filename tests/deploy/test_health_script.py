"""Unit tests for scripts/health.py."""

from __future__ import annotations

import pytest
from scripts.health import CheckResult, FetchResult, main, run_checks


def _fetch_factory(responses: dict[str, FetchResult]):
    def fetch(url: str, headers: dict[str, str], body: bytes | None) -> FetchResult:
        del headers, body
        return responses.get(url, FetchResult(status=404))

    return fetch


def test_all_ok_exits_0(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = "http://localhost:8080"
    monkeypatch.setenv("PUBLIC_BASE_URL", base)
    monkeypatch.setenv("HEALTH_TIMEOUT_S", "1")

    def _all_ok(
        base_url: str,
        fetch: object,
        bearer: str | None = None,
    ) -> list[CheckResult]:
        del base_url, fetch, bearer
        return [
            ("gateway", True, 200),
            ("case", True, 200),
            ("splunk", True, 200),
            ("soar", True, 200),
            ("mcp", True, 200),
            ("mcp_init", True, 200),
        ]

    monkeypatch.setattr("scripts.health.run_checks", _all_ok)
    code = main()
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 6


def test_one_service_down_exits_1_and_names_it(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = "http://localhost:8080"
    monkeypatch.setenv("PUBLIC_BASE_URL", base)
    monkeypatch.setenv("HEALTH_TIMEOUT_S", "0")

    calls = 0

    def _soar_down(
        base_url: str,
        fetch: object,
        bearer: str | None = None,
    ) -> list[CheckResult]:
        nonlocal calls
        calls += 1
        del base_url, fetch, bearer
        return [
            ("gateway", True, 200),
            ("case", True, 200),
            ("splunk", True, 200),
            ("soar", False, 503),
            ("mcp", True, 200),
            ("mcp_init", True, 200),
        ]

    monkeypatch.setattr("scripts.health.run_checks", _soar_down)
    code = main()
    assert code == 1
    assert calls > 0
    err = capsys.readouterr().out
    assert "FAIL soar" in err


def test_mcp_redirect_counts_as_failure() -> None:
    base = "http://localhost:8080"
    responses = {
        f"{base}/healthz": FetchResult(200),
        f"{base}/case/healthz": FetchResult(200),
        f"{base}/splunk/healthz": FetchResult(200),
        f"{base}/soar/healthz": FetchResult(200),
        f"{base}/mcp/healthz": FetchResult(200),
        f"{base}/mcp": FetchResult(307, history=("http://localhost:8080/mcp/",)),
    }
    results = run_checks(base, _fetch_factory(responses))
    mcp_init = results[-1]
    assert mcp_init[0] == "mcp_init"
    assert mcp_init[1] is False


def test_sends_bearer_when_token_set() -> None:
    base = "http://localhost:8080"
    seen: dict[str, str] = {}

    def fetch(url: str, headers: dict[str, str], body: bytes | None) -> FetchResult:
        del body
        if url == f"{base}/mcp":
            seen.update(headers)
            return FetchResult(200)
        return FetchResult(200)

    run_checks(base, fetch, bearer="secret-token")
    assert seen.get("Authorization") == "Bearer secret-token"