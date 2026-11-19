"""Pure deterministic facts derived from alert and Splunk evidence."""

import re
from typing import Any

from contracts.mock_splunk import Alert, SplunkEvidence

RDP_LATERAL_IMAGE = "mstsc.exe"


def compute_facts(
    alert: Alert,
    evidence: list[SplunkEvidence],
    privileged_patterns: list[str],
    crown_jewel_hosts: list[str] | None = None,
) -> dict[str, Any]:
    """Return facts only. No severity is inferred here."""
    user = alert.entities.user or ""
    compiled = [re.compile(pattern) for pattern in privileged_patterns]
    privileged_account = any(pattern.search(user) for pattern in compiled)
    hosts = set(alert.entities.hosts)
    for item in evidence:
        hosts.update(item.entities.hosts)
    jewel_set = set(crown_jewel_hosts or [])
    crown_jewel_asset = bool(hosts.intersection(jewel_set))
    first_seen_country = _first_seen_country(evidence)
    rdp_lateral_hosts = _rdp_lateral_hosts(evidence)
    return {
        "privileged_account": privileged_account,
        "crown_jewel_asset": crown_jewel_asset,
        "first_seen_country": first_seen_country,
        "rdp_lateral_hosts": rdp_lateral_hosts,
        "evidence_count": len(evidence),
        "mitre_techniques": list(alert.mitre_techniques),
    }


def _first_seen_country(evidence: list[SplunkEvidence]) -> bool:
    for item in evidence:
        if item.content.get("first_seen_country") is True:
            return True
    return False


def _rdp_lateral_hosts(evidence: list[SplunkEvidence]) -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()

    def add_host(name: str | None) -> None:
        if name is None or name in seen:
            return
        seen.add(name)
        hosts.append(name)

    for item in evidence:
        content = item.content
        image = content.get("image")
        if isinstance(image, str) and image.lower() == RDP_LATERAL_IMAGE:
            if item.entities.hosts:
                add_host(item.entities.hosts[0])
            cmdline = content.get("cmdline")
            if isinstance(cmdline, str):
                add_host(_host_from_mstsc(cmdline))
    return hosts


def _host_from_mstsc(cmdline: str) -> str | None:
    marker = "/v:"
    idx = cmdline.lower().find(marker)
    if idx < 0:
        return None
    rest = cmdline[idx + len(marker) :].strip()
    if not rest:
        return None
    token = rest.split()[0].strip("\"'")
    return token or None