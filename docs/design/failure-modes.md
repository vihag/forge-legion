# Failure modes

How the stack detects common failures, what it does, and where that behaviour lives in code. Status names follow `contracts.models.CaseStatus`.

| Failure | Detection | System behaviour | Resulting state | Where in code |
| --- | --- | --- | --- | --- |
| Grok HTTP timeout | `httpx.TimeoutException` on POST (30s client timeout) | Up to 2 attempts; then `GrokCallError` with detail `timeout` | `NEEDS_REVIEW` via `flag_review` (`reason=model_call_failed`) | `services/triage/grok.py:__init__` (58, 30s timeout); `_post_with_retry` (90-93); `graph.py:grok_assess` (133-140, 328-334) |
| Grok 4xx/5xx | 5xx: up to 2 attempts; other 4xx: fail immediately (no retry) | `GrokCallError` `model_call_failed` | `NEEDS_REVIEW` | `grok.py:94-103`; `graph.py:grok_assess` |
| Invalid JSON / schema | `json.loads` or `GrokAssessment.model_validate` fails | `invalid_model_output` | `NEEDS_REVIEW` + `triage_failure` evidence | `grok.py:parse_assessment` (172-182); `graph.py:validate` (169-175) |
| Hallucinated evidence ID | `evidence_references` ∉ `evidence_map` keys | `invalid_model_output` with `unknown evidence references` | `NEEDS_REVIEW` | `grok.py:186-189`; `tests/triage/fixtures/grok_unknown_evidence_ref.json` |
| Bad MITRE ID | Regex `MITRE_RE` mismatch | `invalid_model_output` | `NEEDS_REVIEW` | `grok.py:17-18, 183-185` |
| Overconfidence at auto-resolve | `confidence < AUTO_RESOLVE_MIN_CONFIDENCE` (default 0.8) | `409 policy_gate_failed` (G9); case stays `TRIAGED` | `TRIAGED` | `service.py:_policy_gate_failed` (1065-1067); `test_auto_resolve.py::test_p2_gate_failures` |
| Policy route `review` / no autonomous match | `decide()` returns `review` or `default_route` | Triage aborts in `persist_triage` → `no_route_matched` | `NEEDS_REVIEW` | `graph.py:persist_triage` (222-226), `flag_review` |
| Wrong or unintended `routing.yaml` | No unit test of “intent”; misconfig shows up as different `rule` / route | Same as policy routing — may send traffic to `collaborative`, `autonomous`, or `review` | Varies (`TRIAGED`, `AUTO_RESOLVED`, or `NEEDS_REVIEW`) | `services/triage/routing.py:decide`; `config/routing.yaml` |
| Bot goes silent (2-minute watchdog) | **Not implemented in this repository** | No worker or Case API timer restarts bot chat | — | **Enforced by convention / external bot skill only** — no match for `watchdog` or 2-minute stall logic in `services/` or `docs/` at `main` |
| SOAR down on submit | `SoarError` from mock SOAR client | HTTP **502** `soar_unavailable`; status unchanged | Stays **`APPROVED`** (no transition commit on failure) | `service.py:submit_to_soar` (455-458); `tests/test_soar_integration.py::test_soar_down_returns_502_case_stays_approved` |
| Routine webhook failure | Non-2xx or transport error after delivery attempts | Log warning; case state already committed | Triage outcome unchanged (`TRIAGED` etc.) | `services/triage/webhook.py:_deliver` (163-205); `_MAX_ATTEMPTS = 4`, backoff 1s/2s/4s |
| Webhook disabled / misconfigured | `_is_active()` false | Worker skips enqueue (`webhook_state=disabled`) | Case still `TRIAGED` if triage succeeded | `webhook.py:_is_active`, `worker.py:handle_triage_result` (99-102) |
| Replayed alert / duplicate ingest | Same `alert_id` on `POST /v1/cases` | **409** `duplicate` at API; graph treats existing non-`NEW` as `skipped_existing` | No re-triage | `service.py:create_case` (177-180); `graph.py:ingest_case` (76-87) |
| Illegal state operation | `check_transition` raises `TransitionDenied` | HTTP **409** `illegal_transition`; **no audit row** | Prior status unchanged | `state.py:check_transition`; `service.py:_require_transition`; `test_audit.py::test_rejected_call_writes_no_event` |
| Wrong actor type (e.g. agent approves) | `Forbidden` in `decide` | HTTP **403**; no audit | `DISPOSITION_PROPOSED` unchanged | `service.py:374-378` |
| Policy gate failure (any G1–G10) | Non-empty `failed` from `_policy_gate_failed` | **409** `policy_gate_failed`; no `case.auto_resolved` event | **`TRIAGED`** | `service.py:644-649`; `test_auto_resolve.py::test_p2_gate_failures` |
| Langfuse unreachable or SDK error | Exceptions inside span/generation/finalize paths | Triage and Case API unchanged (fail-open); tracer logs rate-limited warnings; after **5** consecutive errors tracing disables itself for the process (`tracing_langfuse.py:_rate_limited_error`, 38–42) | Case status unaffected | `tracing_langfuse.py` wrap paths (e.g. `trace_alert` 84–86, `finalize` 199–200); worker shutdown flush on daemon thread, bounded by `LANGFUSE_FLUSH_TIMEOUT_S` default 5s (`tracing_langfuse.py:shutdown`, 202–214; `tracing.py:_flush_timeout`, 149–156) |

## Triage failure reasons

`flag_review` records `reason` from a fixed set:

| Reason | Typical cause |
| --- | --- |
| `model_call_failed` | Timeout, transport, or Grok HTTP error |
| `invalid_model_output` | Parse, schema, MITRE, or evidence ID validation |
| `no_route_matched` | Policy returned `review` after an otherwise valid assessment |

Defined in `services/triage/graph.py` (`ReviewReason`, nodes `grok_assess`, `validate`, `persist_triage`, `flag_review`).