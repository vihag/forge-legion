# C10 contract: opt-in Langfuse tracing for triage (#44)

Base: `main` @ 8d191d1 (C9), rebased onto C9b (#43) when it merges. Author: FORGE Architecture. No code here.
Budget: composer-2.5, 1 run, at most 1 steer, at most 1 QA retry, hard $10 cap.
STOP RULE: if the same CI step goes red twice, stop and report; an env/CI blocker over 10 min is skipped and noted in the PR.

## 0. Reasoning in brief (for the demo)
- **SDK checked live on 2026-11-22 (PyPI + langfuse.com):** current is `langfuse` **5.2.0** (OpenTelemetry-based; v4 rewrite,
  v5 removed v4 deprecations). API: `Langfuse(public_key, secret_key, base_url)`, `start_as_current_observation(as_type="span"|"generation")`,
  `propagate_attributes(trace_name, tags, metadata)`, `score_current_trace(name, value, data_type, comment)`, `flush()`, `shutdown()`.
  v5 needs Python ≥ 3.11 (we are 3.11) and Langfuse Cloud or self-hosted ≥ v4.59.0. The legacy `trace()/span()/generation()` API is not used.
- **Pin:** `[project.optional-dependencies] tracing = ["langfuse>=5.2,<6"]`. The default install is unchanged.
- **Approach: manual spans, not the LangChain `CallbackHandler`.** The handler would add no new package (langgraph already pulls
  `langchain-core>=1.4.7`, and v5 needs `>=1`), but we still choose manual because:
  1. our Grok call is raw httpx, so the handler would never produce the generation with model, usage and errors;
  2. the handler serialises full graph state (alert, evidence) as each node's input and output, and manual spans let us send exactly what we choose;
  3. a fake SDK object is enough to test manual spans, with no callback internals and no network.
- **Tracing observes, never decides.** Every tracer call is wrapped. Triage results are identical with tracing on, off or broken.

## 1. Gating and lazy import
- Enabled only when `LANGFUSE_ENABLED=1` and `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` are all non-empty.
  Otherwise the worker uses `NoopTracer` and logs one line, `langfuse tracing: disabled (enabled=0|public_key=empty|secret_key=empty|host=empty)`,
  which gives key names and set/empty only.
- `services/triage/tracing.py` (no langfuse import): `Tracer` Protocol, `NoopTracer`, and `build_tracer(env) -> Tracer`. Only when gated on
  does it `importlib.import_module("services.triage.tracing_langfuse")`. That adapter is the **only** file that imports `langfuse`.
- The adapter builds the client explicitly with `Langfuse(public_key=..., secret_key=..., base_url=LANGFUSE_HOST, timeout=5)`.
  `base_url` beats env lookups, so our `LANGFUSE_HOST` name is what's used. The `langfuse` and `opentelemetry` loggers are set to WARNING, and SDK debug is never on.
- If gated on but `import langfuse` fails (extra not installed), log `langfuse tracing: disabled (sdk not installed)` and fall back to Noop. Never crash.
- `TriageDeps` gains `tracer: Tracer = NoopTracer()` (default keeps every existing call site). Node wrapping happens at `add_node`
  in `build_graph`. The worker and the one-shot `run` CLI both build the tracer.

## 2. Trace shape (exact)
- **Trace:** one per alert, opened in the worker around `run_triage` plus the auto-resolve/gate step. The root span is named `triage`,
  with `propagate_attributes(trace_name="triage", metadata={"alert_id": ...})` and input `{"alert_id": ...}`.
- **Node spans:** one child span per LangGraph node that actually ran, named exactly as the node: `fetch_alert`, `ingest_case`,
  `fetch_evidence`, `attach_evidence`, `deterministic_checks`, `grok_assess`, `validate`, `route`, `persist_triage`, `flag_review`.
  Input and output are summaries only: `{"keys": [<state keys written>], "evidence_count"?: int}`. Never raw alert, evidence or state.
  A node exception sets `level="ERROR"`, `status_message=<exception class name>` and re-raises unchanged.
- **Generation:** one child of `grok_assess`, named `grok.assess`, `as_type="generation"`.
  - `model` = `model_returned`, falling back to `model_requested`.
  - `input` = `{"user_message": <prompt user message>}`.
  - `output` = the parsed assessment JSON.
  - `usage_details` = `{"input": usage.prompt_tokens, "output": usage.completion_tokens, "total": usage.total_tokens}`, missing keys omitted.
  - `metadata` = `{"response_id", "request_id"}`.
  - Latency comes from the generation's own start and end.
  - On `GrokCallError` or a transport error: `level="ERROR"`, `status_message` = `model_call_failed` or `invalid_model_output` (the reason code only, never `str(exc)`).
- **Finalize:** after the gate step, a child span `finalize` is opened inside `propagate_attributes(tags=[f"route:{route}", f"outcome:{outcome}"],
  metadata={...})`.
  - `outcome` is one of `auto_resolved | triaged | needs_review | skipped_existing | error`.
  - `route` is `autonomous | collaborative | review`, or `none` if unset.
  - Metadata values are strings, with None becoming `""`: `case_id`, `rule`, `model_requested`, `model_returned`, `response_id`, `request_id`.
- **Scores** (trace level, via `score_current_trace` inside the root):
  - `assessment_confidence`: NUMERIC 0–1, only when an assessment exists.
  - `auto_resolve_gate`: BOOLEAN, only when `route == "autonomous"`. The value is 1 when the worker's gate label is `pass` and 0 for `fail:<rule>`, with `comment=<rule>`.
- `skipped_existing` alerts still get a trace (root + `finalize`, no generation, no scores).

## 3. Failure isolation
- Every adapter method is wrapped in `try/except Exception`. On error it logs `langfuse tracing error: <ExceptionClass>` (rate-limited to 1 per 60 s)
  and returns a no-op handle. After 5 consecutive errors the tracer disables itself for the process and logs that once.
- Non-blocking: the SDK exports in its own background thread. Our code never calls `flush()` per alert.
- Shutdown: the worker's existing `finally` (and the end of the `run` CLI) calls `tracer.shutdown(timeout_s=LANGFUSE_FLUSH_TIMEOUT_S, default 5)`.
  This runs `client.shutdown()` in a daemon thread that is joined with that timeout, so exit is never held longer than that.
- A Langfuse outage never changes the `TriageResult`, case writes, routing, the auto-resolve request or webhook behaviour.

## 4. Secrets and deploy
- Keys come from env only. Nothing logs, prints or returns `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` or `LANGFUSE_HOST`.
  Exceptions are logged by class name only.
- **Keys live in `.env.local`** (FORGE Lead ruling, 2026-11-22), like `XAI_API_KEY` and `ROUTINE_WEBHOOK_KEY` today; every container
  sees them via the shared `env_file`. No new env file, no Compose version bump. Least-privilege env per container for **all** keys is a
  follow-up (#27 / roadmap), not C10.
- `.env.example` gets a commented block, names only: `# LANGFUSE_ENABLED=0`, `# LANGFUSE_PUBLIC_KEY=`, `# LANGFUSE_SECRET_KEY=`,
  `# LANGFUSE_HOST=`, `# LANGFUSE_FLUSH_TIMEOUT_S=5`. `env-merge` ignores commented lines. Empty `LANGFUSE_ENABLED` means off;
  empty `LANGFUSE_FLUSH_TIMEOUT_S` means 5.
- `deploy/Dockerfile`: `ARG PIP_EXTRAS=""` and install `.` or `.[${PIP_EXTRAS}]`. Only `triage_worker` and `triage` builds pass
  `PIP_EXTRAS: tracing`, and every other image is unchanged.
- `pyproject.toml`: the `tracing` extra plus a mypy override `module = ["langfuse", "langfuse.*"]`, `ignore_missing_imports = true`.
  CI does not install the extra.

## 5. File scope
**In:** `services/triage/{tracing.py (new), tracing_langfuse.py (new), graph.py, worker.py, __main__.py}`, `pyproject.toml`,
`deploy/{compose.yml,Dockerfile}`, `.env.example`, `docs/observability.md` (new), `mkdocs.yml` (nav: `Observability: observability.md`
after Triage), `README.md` (one line linking the docs page), `tests/triage/test_tracing*.py` (new), `tests/deploy/test_compose_tracing.py` (new). Roadmap/#27 note: per-container least-privilege env (all keys).
**Out:** Case API, Mock Splunk, Mock SOAR, MCP adapter, `/v1` OpenAPI (no API change, `export_openapi.py --check` unchanged), routing,
prompts, webhook, console, CI workflow files.

## 6. Acceptance criteria (named tests; no network; fake SDK injected via `build_tracer(env, sdk=...)`)
- **TR1 `test_tracing_disabled_no_import`** (subprocess for clean `sys.modules`): import the worker module and triage one fixture alert with
  `LANGFUSE_ENABLED` unset, with `=0`, and with `=1` but `LANGFUSE_HOST` empty. Each time, assert neither `langfuse` nor
  `services.triage.tracing_langfuse` is in `sys.modules`.
- **TR2b `test_enabled_but_missing_var_stays_off`** (subprocess, parametrised over each of PUBLIC_KEY, SECRET_KEY, HOST): `LANGFUSE_ENABLED=1`
  with that one var empty or unset → `NoopTracer`, disabled line names that key as `empty`, `langfuse` not in `sys.modules`.
- **TR2 `test_tracing_gating_matrix`:** all 16 combinations of the 4 vars give an active tracer only when all are set. The disabled log line contains
  key names and `set/empty` only. With `sdk` import failing, the result is Noop and the line is `sdk not installed`.
- **TR3 `test_trace_shape`:** a fake SDK records the calls. For a triaged fixture alert, assert:
  - one root span `triage`, with `trace_name="triage"` and `alert_id` in its metadata;
  - node span names equal to the executed node list in order;
  - one `grok.assess` generation under `grok_assess`;
  - one `finalize` span with tags `["route:<r>", "outcome:<o>"]` and exactly the 6 metadata keys, all string values.
- **TR4 `test_generation_fields`:** model, input and output as specified; `usage_details` mapping (and missing keys omitted); `response_id`
  and `request_id` in metadata. With the Grok fake raising `GrokCallError` the generation has `level="ERROR"`, `status_message="model_call_failed"`,
  and an invalid-output case gives `invalid_model_output`.
- **TR5 `test_scores`:** autonomous + AUTO_RESOLVED gives `auto_resolve_gate=1`; autonomous + fail gives `0`, `comment=<rule>`; collaborative gives no gate score;
  `assessment_confidence` equals the stored value; `skipped_existing` gives no scores.
- **TR6 `test_langfuse_outage_isolated`:** the fake SDK raises on every method (constructor excepted). The `TriageResult` and the Case API calls
  recorded by the fake case client equal those of a Noop run. The worker processes 3 alerts. After 5 errors there's exactly one
  "disabled for process" line, and error lines carry the class name only.
- **TR7 `test_shutdown_bounded`:** fake `shutdown()` sleeps 30 s and `tracer.shutdown(timeout_s=1)` returns in under 2 s. A worker exiting via
  `KeyboardInterrupt` calls `shutdown` exactly once.
- **TR8 `test_tracing_secrets_never_logged`** (canary, like WH6): the env is `LANGFUSE_PUBLIC_KEY=pk-lf-PK-CANARY-3b1e`,
  `LANGFUSE_SECRET_KEY=sk-lf-SK-CANARY-8a4f` and `LANGFUSE_HOST=https://lf-canary-77d0.invalid`. The fake SDK raises exceptions whose messages
  contain all three. Run the enabled, outage, shutdown and disabled paths with caplog at DEBUG (root, `langfuse`, `opentelemetry`,
  `httpx`, `httpcore`) and capsys. Assert none of `PK-CANARY-3b1e`, `SK-CANARY-8a4f` or `lf-canary-77d0` appears in any log record, stdout or stderr.
- **TR9 `test_compose_tracing_scope`:** parse `deploy/compose.yml`: only `triage_worker` and `triage` builds pass `PIP_EXTRAS: tracing`;
  no service references a new env file. `.env.example` has the 5 names only on lines starting `# `, with no uncommented `LANGFUSE_` line.
- **TR10:** the existing `tests/triage/*` and the full suite pass unchanged with the default install (no `langfuse`).
- **CI:** ruff, mypy strict, pytest, `export_openapi.py --check`, compose boot-smoke (no LANGFUSE keys, so the worker stays healthy
  and tracing is disabled), and the docs workflow `mkdocs build --strict` all green. Workflow files are unchanged.

## 7. Docs: `docs/observability.md`
It covers:
- what is traced (the shape in §2, as a table);
- how to enable it: put the keys in `.env.local` by hand (never via `env-set`, which refuses `*_KEY`), set `LANGFUSE_ENABLED=1`, `make up`;
- the self-hosted or Cloud requirement (Langfuse ≥ v4.59.0 or Cloud);
- that it's off by default and never affects triage;
- where the scores show up.

No secrets or example key values beyond `pk-lf-…`/`sk-lf-…` placeholders.

## 8. Laptop verification (ARCHON runs, in /Users/vihaggupta/forge-legion; no `cp .env.example`, no secrets printed)
1. `git pull && make env-merge` should print no LANGFUSE key (the names are commented).
2. **Merge only missing names, with a backup; never overwrite** (prints key names only):
   `cp -p .env.local .env.local.bak-c10 && for k in LANGFUSE_ENABLED LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_FLUSH_TIMEOUT_S; do grep -q "^$k=" .env.local || { echo "$k=" >> .env.local; echo "added $k"; }; done`
   Re-running it prints nothing.
3. **Disabled path:** `make up && make reset && make health` → all healthy. Then
   `docker compose --env-file .env.local -f deploy/compose.yml logs triage_worker | grep "langfuse tracing"` should show `disabled (...)`.
4. **Enabled-but-incomplete stays off:**
   `docker compose --env-file .env.local -f deploy/compose.yml run --rm -e LANGFUSE_ENABLED=1 -e LANGFUSE_HOST= triage python -c "import os,sys;from services.triage.tracing import build_tracer;print(type(build_tracer(os.environ)).__name__, 'langfuse' in sys.modules)"`
   should print `NoopTracer False`. Repeat with `-e LANGFUSE_SECRET_KEY=` instead: same output.
5. `docker compose --env-file .env.local -f deploy/compose.yml exec triage_worker python -c "import langfuse;print(langfuse.__version__)"`
   should print a `5.x` version (the extra is installed in the worker image). The same against `case_api` should give `ModuleNotFoundError`.
6. **Enabled path (only if Vihag has put his Langfuse keys in `.env.local` himself; ARCHON never reads them):**
   `make env-set KEY=LANGFUSE_ENABLED VALUE=1`, `make up`, `make stream-start`, wait 3 min, `make stream-stop`.
   The worker log shows `langfuse tracing: enabled` and no `tracing error` lines. Vihag opens Langfuse and sees `triage` traces with tags
   `route:*`/`outcome:*`, a `grok.assess` generation and the two scores.
7. `docker compose ... logs triage_worker | grep -c -i -E "pk-lf|sk-lf|langfuse_secret|langfuse_public"` should print `0`. The pattern is printed, never a value.
8. Unless Vihag says to leave it on: `make env-set KEY=LANGFUSE_ENABLED VALUE=0 && make up`.

## Parked
- Tracing for the Case API and SENTINEL MCP calls.
- Langfuse prompt management.
- Sampling.
- LLM-as-judge evaluators.