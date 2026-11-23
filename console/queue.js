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

function formatCaseId(id) {
  if (id == null || String(id).trim() === "") {
    return "—";
  }
  return `#${id}`;
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
    const who = view.decision.analyst_id ? ` by ${view.decision.analyst_id}` : "";
    return `${label}${who}`;
  }
  if (view.status === "DISPOSITION_PROPOSED") {
    return "Waiting for analyst approval in Grok Bot";
  }
  if (view.proposed_disposition) {
    return "Proposed, no decision yet";
  }
  return "—";
}

function approvalTone(view) {
  if (!view) {
    return "neutral";
  }
  if (view.decision) {
    return view.decision.decision === "approve" ? "approved" : "rejected";
  }
  if (view.status === "DISPOSITION_PROPOSED") {
    return "waiting";
  }
  if (view.proposed_disposition) {
    return "proposed";
  }
  return "neutral";
}

function humanizeLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  const spaced = raw.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function formatConfidence(value) {
  if (value == null || value === "") {
    return null;
  }
  const n = Number(value);
  if (!Number.isFinite(n)) {
    return null;
  }
  const pct = n <= 1 ? Math.round(n * 100) : Math.round(n);
  return `${pct}%`;
}

function uniqueStrings(values) {
  const out = [];
  const seen = new Set();
  for (const value of values || []) {
    const text = String(value == null ? "" : value).trim();
    if (!text) {
      continue;
    }
    const key = text.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(text);
  }
  return out;
}

const MITRE_RE = /\bT\d{4}(?:\.\d{3})?\b/g;

function addMitre(target, value) {
  if (Array.isArray(value)) {
    for (const item of value) {
      addMitre(target, item);
    }
    return;
  }
  if (typeof value !== "string") {
    return;
  }
  MITRE_RE.lastIndex = 0;
  const matches = value.match(MITRE_RE);
  if (matches) {
    target.push(...matches);
  }
}

function extractMitreTechniques(view) {
  const found = [];
  addMitre(found, view && view.mitre_techniques);
  for (const ev of (view && view.evidence) || []) {
    const content = ev && ev.content && typeof ev.content === "object" ? ev.content : {};
    addMitre(found, content.alert_mitre_techniques);
    addMitre(found, content.mitre_techniques);
    if (content.assessment && typeof content.assessment === "object") {
      addMitre(found, content.assessment.mitre_techniques);
    }
    if (content.facts && typeof content.facts === "object") {
      addMitre(found, content.facts.mitre_techniques);
    }
  }
  for (const finding of (view && view.findings) || []) {
    const statement = finding && finding.statement;
    if (typeof statement === "string") {
      const match = statement.trim().match(/^(T\d{4}(?:\.\d{3})?)\b/);
      if (match) {
        found.push(match[1]);
      }
    }
  }
  const unique = uniqueStrings(found);
  unique.sort((a, b) => a.localeCompare(b, "en", { numeric: true }));
  return unique;
}

function isUsableString(value) {
  return typeof value === "string" && value.trim() !== "";
}

function extractEntities(view) {
  const users = [];
  const hosts = [];
  const ips = [];
  for (const ev of (view && view.evidence) || []) {
    if (!ev || ev.source === "triage_assessment") {
      continue;
    }
    const content = ev.content && typeof ev.content === "object" ? ev.content : {};
    const ents = content.entities && typeof content.entities === "object" ? content.entities : {};
    if (isUsableString(ents.user)) {
      users.push(ents.user);
    } else if (isUsableString(content.user)) {
      users.push(content.user);
    } else if (isUsableString(content.TargetUserName)) {
      users.push(content.TargetUserName);
    } else if (isUsableString(content.UserName)) {
      users.push(content.UserName);
    } else if (isUsableString(content.actor)) {
      users.push(content.actor);
    } else if (isUsableString(content.UserId)) {
      users.push(content.UserId);
    }
    if (Array.isArray(ents.hosts)) {
      for (const host of ents.hosts) {
        if (isUsableString(host)) {
          hosts.push(host);
        }
      }
    }
    for (const key of ["host", "target", "client", "src_host", "ComputerName", "WorkstationName"]) {
      if (isUsableString(content[key])) {
        hosts.push(content[key]);
      }
    }
    for (const key of ["src_ip", "client_ip", "ClientIP", "ClientIPAddress", "vpn_ip"]) {
      const fromEnt = key === "src_ip" && isUsableString(ents.src_ip) ? ents.src_ip : null;
      if (fromEnt) {
        ips.push(fromEnt);
      } else if (isUsableString(content[key])) {
        ips.push(content[key]);
      }
    }
  }
  return {
    users: uniqueStrings(users),
    hosts: uniqueStrings(hosts),
    ips: uniqueStrings(ips),
  };
}

function extractKeyFacts(view) {
  const entities = extractEntities(view);
  const flags = [];
  if (view && view.privileged_account) {
    flags.push("Privileged account");
  }
  if (view && view.crown_jewel_asset) {
    flags.push("Crown jewel");
  }
  return {
    rule: view && view.rule ? humanizeLabel(view.rule) : "",
    users: entities.users,
    hosts: entities.hosts,
    ips: entities.ips,
    techniques: extractMitreTechniques(view),
    confidence: formatConfidence(view && view.confidence),
    flags,
    verdict: view && view.verdict ? humanizeLabel(view.verdict) : "",
  };
}

const COLLAPSE_CHAR_LIMIT = 360;
const COLLAPSE_LINE_LIMIT = 4;

function shouldCollapse(text, charLimit, lineLimit) {
  const raw = String(text || "");
  const chars = charLimit == null ? COLLAPSE_CHAR_LIMIT : charLimit;
  const lines = lineLimit == null ? COLLAPSE_LINE_LIMIT : lineLimit;
  if (raw.length > chars) {
    return true;
  }
  return raw.split(/\n/).length > lines;
}

function teaserText(text, charLimit) {
  const raw = String(text || "").trim();
  const limit = charLimit == null ? COLLAPSE_CHAR_LIMIT : charLimit;
  if (raw.length <= limit) {
    return raw;
  }
  const para = raw.search(/\n\s*\n/);
  if (para >= 40 && para <= limit) {
    return `${raw.slice(0, para).trimEnd()}…`;
  }
  const slice = raw.slice(0, limit);
  const at = Math.max(slice.lastIndexOf(" "), slice.lastIndexOf("\n"));
  const cut = at >= limit * 0.6 ? slice.slice(0, at) : slice;
  return `${cut.trimEnd()}…`;
}

const TECHNICAL_RE =
  /(\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b|\bT\d{4}(?:\.\d{3})?\b|\b(?:EV|SPL|SOAR)-[A-Z0-9-]+\b|\b[A-Z]{2,}(?:-[A-Z0-9]{1,})+\b|\bhttps?:\/\/[^\s)]+|\b[a-fA-F0-9]{32,64}\b|(?<![A-Za-z0-9.])[a-z][a-z0-9-]+\.[a-z][a-z0-9-]+\b(?!\.[A-Za-z]))/g;

function splitTechnicalTokens(text) {
  const raw = String(text == null ? "" : text);
  if (!raw) {
    return [];
  }
  const parts = [];
  let last = 0;
  TECHNICAL_RE.lastIndex = 0;
  let match = TECHNICAL_RE.exec(raw);
  while (match) {
    if (match.index > last) {
      parts.push({ value: raw.slice(last, match.index), mono: false });
    }
    parts.push({ value: match[0], mono: true });
    last = match.index + match[0].length;
    match = TECHNICAL_RE.exec(raw);
  }
  if (last < raw.length) {
    parts.push({ value: raw.slice(last), mono: false });
  }
  return parts;
}

function proseFromPayload(payload) {
  if (typeof payload === "string") {
    return payload.trim();
  }
  if (!payload || typeof payload !== "object") {
    return "";
  }
  for (const key of [
    "plan",
    "note",
    "notes",
    "text",
    "statement",
    "summary",
    "message",
    "instruction_text",
    "body",
    "content",
  ]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function formatCenturionNotes(view) {
  const notes = [];
  for (const event of centurionAudit(view && view.audit)) {
    const text = proseFromPayload(event && event.payload);
    if (!text) {
      continue;
    }
    notes.push({
      text,
      type: event.type || "",
      author: (event.actor && event.actor.id) || "",
      ts: event.ts || "",
    });
  }
  return notes;
}

function sentinelLabel(authorId) {
  const id = String(authorId || "").trim();
  if (!id) {
    return "Unknown";
  }
  const lower = id.toLowerCase();
  if (lower.includes("threat")) {
    return "Threat SENTINEL";
  }
  if (lower.includes("evidence")) {
    return "Evidence SENTINEL";
  }
  if (lower.includes("disposition")) {
    return "Disposition SENTINEL";
  }
  if (lower.includes("centurion")) {
    return "CENTURION";
  }
  if (lower.includes("triage")) {
    return "Grok triage";
  }
  return id;
}

function evidenceById(evidence) {
  const map = new Map();
  for (const item of evidence || []) {
    if (item && item.id != null) {
      map.set(item.id, item);
    }
  }
  return map;
}

function evidenceRefLabels(evidenceIds, evidence) {
  const lookup = evidenceById(evidence);
  const labels = [];
  for (const id of evidenceIds || []) {
    const item = lookup.get(id);
    if (item && item.ref) {
      labels.push(String(item.ref));
    } else if (id != null && String(id).trim() !== "") {
      labels.push(String(id));
    }
  }
  return labels;
}

function evidenceListItems(evidence) {
  return (evidence || []).map((item) => ({
    id: item && item.id,
    source: (item && item.source) || "unknown",
    ref: (item && item.ref) || (item && item.id != null ? String(item.id) : "—"),
  }));
}

function findingCardModel(finding, evidence) {
  const statement = finding && finding.statement ? String(finding.statement) : "";
  return {
    kind: finding && finding.kind ? String(finding.kind) : "",
    author: sentinelLabel(finding && finding.author_id),
    confidence: formatConfidence(finding && finding.confidence),
    statement,
    evidenceRefs: evidenceRefLabels(finding && finding.evidence_ids, evidence),
  };
}

function dispositionModel(view) {
  const proposed = view && view.proposed_disposition;
  if (!proposed) {
    return null;
  }
  const soar = view.soar;
  let soarAction = humanizeLabel(proposed.action) || "—";
  if (soar && soar.soar_case_id) {
    soarAction = `Submitted as ${soar.soar_case_id}`;
  }
  return {
    action: humanizeLabel(proposed.action) || "—",
    soarAction,
    rationale: proposed.rationale ? String(proposed.rationale) : "",
    approval: approvalState(view),
    tone: approvalTone(view),
  };
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
  formatCaseId,
  resolveLinkTemplate,
  buildGrokLink,
  isAllowedLinkScheme,
  isCenturionActor,
  isPlanOrNoteType,
  centurionFindings,
  centurionAudit,
  approvalState,
  approvalTone,
  humanizeLabel,
  formatConfidence,
  uniqueStrings,
  extractMitreTechniques,
  extractEntities,
  extractKeyFacts,
  shouldCollapse,
  teaserText,
  splitTechnicalTokens,
  proseFromPayload,
  formatCenturionNotes,
  sentinelLabel,
  evidenceRefLabels,
  evidenceListItems,
  findingCardModel,
  dispositionModel,
  COLLAPSE_CHAR_LIMIT,
};

if (typeof window !== "undefined") {
  window.LegionQueue = api;
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
}