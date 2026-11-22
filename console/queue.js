"use strict";

/** Pure queue helpers for the read-only analyst tab. */
const CLOSED_STATUSES = new Set(["AUTO_RESOLVED", "SUBMITTED", "REJECTED"]);

const QUEUE_STATUS_PRIORITY = {
  DISPOSITION_PROPOSED: 0,
  NEEDS_REVIEW: 1,
};

const SEVERITY_RANK = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const QUEUE_FILTER_STATUSES = {
  all: null,
  approvals: new Set(["DISPOSITION_PROPOSED"]),
  needs_investigating: new Set(["NEEDS_REVIEW", "INVESTIGATING"]),
};

const STATUS_HUMAN = {
  NEW: "New",
  TRIAGED: "Triaged",
  INVESTIGATING: "Investigating",
  DISPOSITION_PROPOSED: "Awaiting approval",
  APPROVED: "Approved",
  SUBMITTED: "Sent to SOAR",
  REJECTED: "Rejected",
  NEEDS_REVIEW: "Needs review",
  AUTO_RESOLVED: "Auto-closed",
};

function isOpenCase(row) {
  return row != null && !CLOSED_STATUSES.has(row.status);
}

function filterOpenCases(cases) {
  return (cases || []).filter(isOpenCase);
}

function applyQueueFilter(cases, filterKey) {
  const allowed = QUEUE_FILTER_STATUSES[filterKey];
  if (!allowed) {
    return filterOpenCases(cases);
  }
  return filterOpenCases(cases).filter((row) => allowed.has(row.status));
}

function compareQueueRows(a, b) {
  const pa = QUEUE_STATUS_PRIORITY[a.status] ?? 50;
  const pb = QUEUE_STATUS_PRIORITY[b.status] ?? 50;
  if (pa !== pb) {
    return pa - pb;
  }
  const sa = SEVERITY_RANK[String(a.severity || "").toLowerCase()] ?? 99;
  const sb = SEVERITY_RANK[String(b.severity || "").toLowerCase()] ?? 99;
  if (sa !== sb) {
    return sa - sb;
  }
  return String(a.created_at || "").localeCompare(String(b.created_at || ""));
}

function sortQueueCases(cases) {
  return [...(cases || [])].sort(compareQueueRows);
}

function humanStatus(status) {
  return STATUS_HUMAN[status] || String(status || "—");
}

function ruleLabel(row) {
  if (row && row.rule) {
    return row.rule;
  }
  return row?.title || "—";
}

function parseIso(ts) {
  const parsed = new Date(ts);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatQueueAge(createdAt, nowMs) {
  const created = parseIso(createdAt);
  if (!created) {
    return "—";
  }
  const now = nowMs != null ? nowMs : Date.now();
  const totalSec = Math.max(0, Math.round((now - created.getTime()) / 1000));
  if (totalSec < 60) {
    return `${totalSec}s`;
  }
  const minutes = Math.floor(totalSec / 60);
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remMin = minutes % 60;
  if (hours < 48) {
    return remMin > 0 ? `${hours}h ${remMin}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function resolveLinkTemplate(cfg, fallback) {
  const raw = cfg && cfg.grokbotCenturionLinkTemplate;
  const value = raw == null ? "" : String(raw).trim();
  if (!value || /^\$\{/.test(value)) {
    return fallback;
  }
  return value;
}

function alertIdLabel(alertId) {
  if (alertId == null || String(alertId).trim() === "") {
    return "no alert id";
  }
  return String(alertId);
}

function buildGrokLink(template, caseId, alertId) {
  const alertLabel = alertIdLabel(alertId);
  const text = `Let's work case ${caseId} (${alertLabel})`;
  const encCase = encodeURIComponent(String(caseId));
  const encAlert = encodeURIComponent(alertLabel);
  const encText = encodeURIComponent(text);
  return String(template || "")
    .replaceAll("{case_id}", encCase)
    .replaceAll("{alert_id}", encAlert)
    .replaceAll("{text}", encText);
}

function isAllowedLinkScheme(url) {
  const raw = String(url || "").trim();
  if (!raw) {
    return false;
  }
  const match = /^([a-z][a-z0-9+.-]*):/i.exec(raw);
  if (!match) {
    return false;
  }
  const scheme = match[1].toLowerCase();
  return scheme === "grokbot" || scheme === "https";
}

function isCenturionActor(actorId) {
  return String(actorId || "").toLowerCase().includes("centurion");
}

function isPlanOrNoteType(typeValue) {
  const t = String(typeValue || "").toLowerCase();
  return t.includes("plan") || t.includes("note");
}

function centurionFindings(findings) {
  return (findings || []).filter((f) => isCenturionActor(f.author_id));
}

function centurionAudit(audit) {
  return (audit || []).filter(
    (e) => isCenturionActor(e.actor?.id) || isPlanOrNoteType(e.type),
  );
}

function approvalState(view) {
  if (!view) {
    return "—";
  }
  if (view.decision) {
    const label = view.decision.decision === "approve" ? "Approved" : "Rejected";
    return `${label} by ${view.decision.analyst_id}`;
  }
  if (view.status === "DISPOSITION_PROPOSED") {
    return "Waiting for analyst approval";
  }
  if (view.proposed_disposition) {
    return "Proposed, no decision yet";
  }
  return "—";
}

const api = {
  CLOSED_STATUSES,
  QUEUE_FILTER_STATUSES,
  isOpenCase,
  filterOpenCases,
  applyQueueFilter,
  sortQueueCases,
  humanStatus,
  ruleLabel,
  formatQueueAge,
  resolveLinkTemplate,
  buildGrokLink,
  isAllowedLinkScheme,
  isCenturionActor,
  isPlanOrNoteType,
  centurionFindings,
  centurionAudit,
  approvalState,
};

if (typeof window !== "undefined") {
  window.LegionQueue = api;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
}