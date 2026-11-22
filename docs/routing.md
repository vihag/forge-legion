# Routing policy

First match wins. Implemented in `services/triage/routing.py` against `config/routing.yaml`. Grok's `recommended_route` is stored but **not** used as the route.

## `config/routing.yaml`

```yaml
version: 1
privileged_account_patterns: ['^adm\.', '^da-', '^svc-admin']
crown_jewel_hosts: [FIN-DB-01, DC-01, DC-02, JMP-01, PAY-APP-02, SWIFT-GW-01]
rules:
  - {name: privileged_account_collaborative, when: {privileged_account: true}, route: collaborative}
  - {name: splunk_high_or_critical_collaborative, when: {splunk_severity_in: [high, critical]}, route: collaborative}
  - {name: crown_jewel_collaborative, when: {crown_jewel_asset: true}, route: collaborative}
  - {name: non_benign_verdict_collaborative, when: {verdict_in: [suspicious, malicious]}, route: collaborative}
  - {name: high_or_critical_collaborative, when: {assessed_severity_in: [high, critical]}, route: collaborative}
  - {name: low_medium_autonomous, when: {assessed_severity_in: [low, medium]}, route: autonomous}
default_route: review
```

## How to read it

| Piece | Role |
| --- | --- |
| `privileged_account_patterns` | Regex list; matched against alert user for deterministic `privileged_account` |
| `crown_jewel_hosts` | Hostnames that set `crown_jewel_asset` when intersecting alert/evidence hosts |
| `rules[]` | Evaluated **in order**; first `when` clause that matches sets `route` and `rule` name |
| `when` keys | `privileged_account`, `crown_jewel_asset`, `splunk_severity_in`, `verdict_in`, `assessed_severity_in` (from facts + Grok assessment + Splunk severity) |
| `default_route: review` | If no rule matches, route is `review` → triage graph flags `NEEDS_REVIEW` (`no_route_matched`) |

**Autonomous** route triggers an auto-resolve **request**; the Case API still runs G1–G10. **Collaborative** leaves the case `TRIAGED` and eligible for the CENTURION webhook.