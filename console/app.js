"use strict";

const METRICS_URL = "/case/v1/metrics";
const CASES_URL = "/case/v1/cases";
const POLL_MS = 5000;
const QUEUE_POLL_MS = 5000;
const CASE_PAGE_SIZE = 200;
const CASE_PAGE_MAX = 500;
const DEFAULT_GROKBOT_TEMPLATE =
  "grokbot://app/v1/agent?id=8818388d-d88b-4b48-a907-11dcc6b8547d";

const SEVERITY_ORDER = ["low", "medium", "high", "critical"];

const STATUS_LABELS = {
  NEW: ["New", "neutral"],
  TRIAGED: ["Triaged", "brown"],
  INVESTIGATING: ["Investigating", "brown"],
  DISPOSITION_PROPOSED: ["Awaiting decision", "brown"],
  APPROVED: ["Approved", "green"],
  SUBMITTED: ["Sent to SOAR", "green"],
  REJECTED: ["Rejected", "terracotta"],
  NEEDS_REVIEW: ["Needs review", "terracotta"],
  AUTO_RESOLVED: ["Auto-closed", "green"],
};

const ROUTE_LABELS = {
  autonomous: ["Auto-close track", "route-auto"],
  collaborative: ["With LEGION", "route-legion"],
  review: ["Analyst review", "route-review"],
};

const VERDICT_LABELS = {
  benign: ["Benign", "green"],
  false_positive: ["False positive", "green"],
  suspicious: ["Suspicious", "brown"],
  malicious: ["Malicious", "terracotta"],
};

const TYPE_LABELS = {
  system: "System",
  agent: "AI bot",
  analyst: "Human",
};

const AGENT_NAMES = {
  "triage-langgraph": "Grok triage (LangGraph)",
  "policy:autonomous-triage": "Auto-close policy",
};

const FILTER_KEY = "legion.console.feedFilter";
const TAB_KEY = "legion.console.activeTab";
const QUEUE_FILTER_KEY = "legion.console.queueFilter";
const FILTERS = ["all", "auto", "legion", "awaiting", "analyst"];
const QUEUE_FILTERS = ["all", "approvals", "needs_investigating"];
const FEED_MAX = 50;

/*
 * Outcome buckets for the live feed, from the API's own fields:
 *   auto     - status AUTO_RESOLVED (closed by the auto-close policy)
 *   legion   - handed to LEGION and still being worked:
 *              TRIAGED on the collaborative or autonomous track, or INVESTIGATING
 *   awaiting - DISPOSITION_PROPOSED or NEEDS_REVIEW (an analyst call is pending)
 *   analyst  - APPROVED, SUBMITTED or REJECTED (an analyst has decided)
 * NEW and TRIAGED on the review track belong to no bucket and show under All only.
 */
function outcomeOf(row) {
  switch (row.status) {
    case "AUTO_RESOLVED":
      return "auto";
    case "INVESTIGATING":
      return "legion";
    case "TRIAGED":
      return row.route === "collaborative" || row.route === "autonomous" ? "legion" : null;
    case "DISPOSITION_PROPOSED":
    case "NEEDS_REVIEW":
      return "awaiting";
    case "APPROVED":
    case "SUBMITTED":
    case "REJECTED":
      return "analyst";
    default:
      return null;
  }
}

/*
 * The metrics endpoint returns the 20 most recently updated cases. The feed
 * keeps every case it has seen since the page loaded (up to FEED_MAX); a case
 * that changes is always updated most recently, so it re-enters the window.
 */
const feed = {
  cases: new Map(),
  seenKeys: null,
  freshKeys: new Set(),
  lastTotal: null,
  filter: loadFilter(),
};

function loadFilter() {
  try {
    const saved = window.localStorage.getItem(FILTER_KEY);
    return FILTERS.includes(saved) ? saved : "all";
  } catch (_err) {
    return "all";
  }
}

function saveFilter(value) {
  try {
    window.localStorage.setItem(FILTER_KEY, value);
  } catch (_err) {
    /* storage unavailable: keep the choice for this session only */
  }
}

function rowKey(row) {
  return `${row.id}:${row.status}`;
}

function sortedFeed() {
  return [...feed.cases.values()].sort((a, b) => {
    const byTime = String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    return byTime !== 0 ? byTime : b.id - a.id;
  });
}

function mergeRecent(metrics) {
  const total = metrics.counts.total;
  const recent = metrics.recent || [];
  let reset = feed.lastTotal != null && total < feed.lastTotal;
  for (const row of recent) {
    const known = feed.cases.get(row.id);
    if (known && known.alert_id !== row.alert_id) {
      reset = true;
    }
  }
  if (reset) {
    feed.cases.clear();
  }
  feed.lastTotal = total;
  for (const row of recent) {
    feed.cases.set(row.id, row);
  }
  const rows = sortedFeed();
  for (const row of rows.slice(FEED_MAX)) {
    feed.cases.delete(row.id);
  }
  const keys = new Set(rows.slice(0, FEED_MAX).map(rowKey));
  // Highlight a row when the case is new or its status changed since the last poll.
  // Nothing is highlighted on the first load.
  feed.freshKeys = new Set();
  if (feed.seenKeys !== null && !reset) {
    for (const key of keys) {
      if (!feed.seenKeys.has(key)) {
        feed.freshKeys.add(key);
      }
    }
  }
  feed.seenKeys = keys;
}

function renderFilter() {
  const counts = { all: 0, auto: 0, legion: 0, awaiting: 0, analyst: 0 };
  for (const row of feed.cases.values()) {
    counts.all += 1;
    const bucket = outcomeOf(row);
    if (bucket) {
      counts[bucket] += 1;
    }
  }
  for (const node of document.querySelectorAll("[data-count]")) {
    node.textContent = String(counts[node.dataset.count] ?? 0);
  }
  for (const button of document.querySelectorAll("#feed-filter [data-filter]")) {
    button.setAttribute("aria-pressed", String(button.dataset.filter === feed.filter));
  }
}

function initFilter() {
  const group = document.getElementById("feed-filter");
  if (!group) {
    return;
  }
  group.addEventListener("click", (event) => {
    const button = event.target.closest("[data-filter]");
    if (!button) {
      return;
    }
    feed.filter = button.dataset.filter;
    saveFilter(feed.filter);
    feed.freshKeys = new Set();
    renderFilter();
    renderRecent();
  });
  renderFilter();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text != null) {
    node.textContent = String(text);
  }
  return node;
}

function setField(path, value) {
  for (const node of document.querySelectorAll(`[data-field="${path}"]`)) {
    node.textContent = value == null ? "—" : String(value);
  }
}

function formatNumber(value) {
  if (value == null) {
    return null;
  }
  return Number(value).toLocaleString("en-US");
}

function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) {
    return "—";
  }
  const total = Math.max(0, Math.round(Number(seconds)));
  if (total < 60) {
    return `${total}s`;
  }
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  }
  return `${minutes}m ${String(secs).padStart(2, "0")}s`;
}

function friendlyAgentName(actorId, type) {
  if (AGENT_NAMES[actorId]) {
    return AGENT_NAMES[actorId];
  }
  const id = String(actorId || "").toLowerCase();
  if (type === "analyst" || id.includes("analyst")) {
    return "Analyst";
  }
  if (id.includes("centurion")) {
    return "CENTURION";
  }
  if (id.includes("evidence")) {
    return "Evidence SENTINEL";
  }
  if (id.includes("threat")) {
    return "Threat SENTINEL";
  }
  if (id.includes("disposition")) {
    return "Disposition SENTINEL";
  }
  return actorId;
}

function severityChip(severity) {
  const sev = String(severity || "").toLowerCase();
  return el("span", `chip sev sev-${sev || "none"}`, sev ? sev.toUpperCase() : "—");
}

function arrow(direction) {
  const node = el("span", `chev chev-${direction}`);
  node.setAttribute("aria-label", "changed to");
  node.setAttribute("role", "img");
  return node;
}

function transitionRow(from, to, count, direction) {
  const li = el("li", "transition");
  const pair = el("span", "pair");
  pair.append(severityChip(from), arrow(direction), severityChip(to));
  li.append(pair, el("span", "count", `×${count}`));
  return li;
}

function sortTransitions(entries) {
  return entries.sort((a, b) => {
    if (b.count !== a.count) {
      return b.count - a.count;
    }
    return SEVERITY_ORDER.indexOf(b.to) - SEVERITY_ORDER.indexOf(a.to);
  });
}

function renderUplifts(uplifts) {
  const list = document.getElementById("uplift-list");
  const total = document.getElementById("uplift-total");
  if (!list || !total) {
    return;
  }
  const entries = Object.entries(uplifts || {}).map(([key, count]) => {
    const [from, to] = key.split("->");
    return { from, to, count };
  });
  total.textContent = String(entries.reduce((sum, item) => sum + item.count, 0));
  list.replaceChildren();
  if (entries.length === 0) {
    list.appendChild(el("li", "empty", "None so far"));
    return;
  }
  for (const item of sortTransitions(entries)) {
    list.appendChild(transitionRow(item.from, item.to, item.count, "up"));
  }
}

/*
 * The metrics API reports downgrades as a single total. The per-transition
 * rows are derived from the recent-cases list, so they only cover that window;
 * a note explains any remainder so the total always stays exact.
 */
function renderDowngrades(total, recent) {
  const list = document.getElementById("downgrade-list");
  const totalNode = document.getElementById("downgrade-total");
  const note = document.getElementById("downgrade-note");
  if (!list || !totalNode || !note) {
    return;
  }
  const count = Number(total || 0);
  totalNode.textContent = String(count);
  list.replaceChildren();
  note.textContent = "";
  const counts = {};
  for (const row of recent || []) {
    if (!row.triage_severity) {
      continue;
    }
    const before = SEVERITY_ORDER.indexOf(row.splunk_severity);
    const after = SEVERITY_ORDER.indexOf(row.triage_severity);
    if (after < before) {
      const key = `${row.splunk_severity}->${row.triage_severity}`;
      counts[key] = (counts[key] || 0) + 1;
    }
  }
  const entries = Object.entries(counts).map(([key, n]) => {
    const [from, to] = key.split("->");
    return { from, to, count: n };
  });
  if (count === 0) {
    list.appendChild(el("li", "empty", "None so far"));
    return;
  }
  for (const item of sortTransitions(entries)) {
    list.appendChild(transitionRow(item.from, item.to, item.count, "down"));
  }
  const shown = entries.reduce((sum, item) => sum + item.count, 0);
  if (shown < count) {
    note.textContent =
      shown === 0
        ? "Breakdown shown for recent cases only."
        : `Breakdown covers recent cases; ${count - shown} more earlier.`;
  }
}

function renderTiming(id, block) {
  const card = document.getElementById(id);
  if (!card) {
    return;
  }
  const value = card.querySelector('[data-timing="value"]');
  const note = card.querySelector('[data-timing="note"]');
  if (!block || !block.n) {
    value.textContent = "—";
    note.textContent = "no cases yet";
    card.classList.add("empty-card");
    return;
  }
  card.classList.remove("empty-card");
  value.textContent = formatDuration(block.median);
  const plural = block.n === 1 ? "case" : "cases";
  let text = `median · based on ${block.n} ${plural}`;
  // Only show the average when it adds information (not skewed far from median).
  if (block.average != null && block.median != null && block.median > 0) {
    const ratio = block.average / block.median;
    if (ratio >= 0.5 && ratio <= 2) {
      text += ` · average ${formatDuration(block.average)}`;
    }
  }
  note.textContent = text;
}

function pill(map, key, base) {
  const entry = map[key];
  if (!entry) {
    return el("span", `${base} neutral`, key ? String(key) : "—");
  }
  return el("span", `${base} ${entry[1]}`, entry[0]);
}

function renderRecent() {
  const body = document.querySelector("#recent-table tbody");
  if (!body) {
    return;
  }
  const rows = sortedFeed().filter(
    (row) => feed.filter === "all" || outcomeOf(row) === feed.filter,
  );
  body.replaceChildren();
  if (rows.length === 0) {
    const tr = el("tr");
    const td = el("td", "empty", feed.cases.size === 0 ? "No cases yet" : "No cases in this view yet");
    td.colSpan = 6;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const row of rows) {
    const bucket = outcomeOf(row);
    const tr = el("tr", bucket ? `outcome-${bucket}` : "outcome-none");
    if (feed.freshKeys.has(rowKey(row))) {
      tr.classList.add("fresh");
    }

    const idCell = el("td", "case-id", `#${row.id}`);

    const alertCell = el("td", "alert");
    alertCell.append(
      el("span", "alert-title", row.title || row.alert_id),
      el("span", "alert-id", row.alert_id),
    );

    const sevCell = el("td", "sev-cell");
    const sevWrap = el("span", "pair");
    const changed = row.triage_severity && row.triage_severity !== row.splunk_severity;
    sevWrap.appendChild(severityChip(row.splunk_severity));
    if (changed) {
      const up =
        SEVERITY_ORDER.indexOf(row.triage_severity) > SEVERITY_ORDER.indexOf(row.splunk_severity);
      sevWrap.append(arrow(up ? "up" : "down"), severityChip(row.triage_severity));
    }
    sevCell.appendChild(sevWrap);
    if (changed) {
      sevCell.title = "Splunk severity, then Grok's severity";
    }

    const routeCell = el("td");
    if (row.route) {
      const entry = ROUTE_LABELS[row.route] || [row.route, "route-review"];
      routeCell.appendChild(el("span", `chip route ${entry[1]}`, entry[0]));
    } else {
      routeCell.appendChild(el("span", "muted", "—"));
    }

    const statusCell = el("td");
    statusCell.appendChild(pill(STATUS_LABELS, row.status, "pill"));

    const verdictCell = el("td");
    if (row.verdict) {
      verdictCell.appendChild(pill(VERDICT_LABELS, row.verdict, "verdict"));
    } else {
      verdictCell.appendChild(el("span", "muted", "—"));
    }

    tr.append(idCell, alertCell, sevCell, routeCell, statusCell, verdictCell);
    body.appendChild(tr);
  }
}

function parseTs(ts) {
  const parsed = new Date(ts);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function relative(date, now) {
  const secs = Math.max(0, Math.round((now - date) / 1000));
  if (secs < 60) {
    return `${secs}s ago`;
  }
  const mins = Math.floor(secs / 60);
  if (mins < 60) {
    return `${mins}m ago`;
  }
  return `${Math.floor(mins / 60)}h ago`;
}

function clock(date) {
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

function renderAgents(agents) {
  const body = document.querySelector("#agents-table tbody");
  const empty = document.getElementById("agents-empty");
  if (!body) {
    return;
  }
  body.replaceChildren();
  const list = agents || [];
  empty?.classList.toggle("hidden", list.length > 0);
  const now = new Date();
  for (const agent of list) {
    const tr = el("tr");
    const nameCell = el("td", "agent-name");
    nameCell.append(
      el("span", `dot type-${agent.type}`),
      el("span", null, friendlyAgentName(agent.actor_id, agent.type)),
    );
    nameCell.title = agent.actor_id;
    const typeCell = el("td");
    typeCell.appendChild(
      el("span", `chip type-chip type-${agent.type}`, TYPE_LABELS[agent.type] || agent.type),
    );
    const eventsCell = el("td", "num", formatNumber(agent.events));
    const lastCell = el("td", "last-seen");
    const seen = parseTs(agent.last_seen);
    if (seen) {
      lastCell.append(el("span", "clock", clock(seen)), el("span", "rel", relative(seen, now)));
    } else {
      lastCell.textContent = agent.last_seen || "—";
    }
    tr.append(nameCell, typeCell, eventsCell, lastCell);
    body.appendChild(tr);
  }
}

function applyMetrics(metrics) {
  setField("counts.total", formatNumber(metrics.counts.total));
  setField("counts.auto_resolved", formatNumber(metrics.counts.auto_resolved));
  setField("counts.collaborative_open", formatNumber(metrics.counts.collaborative_open));
  setField("counts.with_legion_open", formatNumber(metrics.counts.with_legion_open));
  setField("counts.needs_review", formatNumber(metrics.counts.needs_review));
  mergeRecent(metrics);
  renderUplifts(metrics.uplifts);
  renderDowngrades(metrics.downgrades, [...feed.cases.values()]);

  const banner = document.getElementById("auto-off-banner");
  const minutesTile = document.getElementById("tile-minutes");
  if (metrics.auto_resolve_enabled) {
    banner?.classList.add("hidden");
    setField("minutes_saved_estimate", formatNumber(metrics.minutes_saved_estimate));
    minutesTile?.classList.remove("off");
  } else {
    banner?.classList.remove("hidden");
    setField("minutes_saved_estimate", "Auto-close off");
    minutesTile?.classList.add("off");
  }

  setField("assumptions.minutes_per_alert", metrics.assumptions.minutes_per_alert);
  renderTiming("timing-triage", metrics.time_to_triage_s);
  renderTiming("timing-ai-only", metrics.time_to_resolve_s.ai_only);
  renderTiming("timing-ai-human", metrics.time_to_resolve_s.ai_plus_human);
  renderFilter();
  renderRecent();
  renderAgents(metrics.agents);

  const updated = document.getElementById("last-updated");
  if (updated) {
    updated.textContent = `Live · updated ${clock(new Date())}`;
  }
  document.getElementById("live-indicator")?.classList.remove("stale");
}

async function refresh() {
  try {
    const response = await fetch(METRICS_URL, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      document.getElementById("live-indicator")?.classList.add("stale");
      return;
    }
    const metrics = await response.json();
    applyMetrics(metrics);
  } catch (_err) {
    document.getElementById("live-indicator")?.classList.add("stale");
    /* polling continues */
  }
}

const queueState = {
  filter: loadQueueFilter(),
  cases: [],
  selectedId: null,
  selectedView: null,
};

function legionQueue() {
  return window.LegionQueue;
}

function loadQueueFilter() {
  try {
    const saved = window.localStorage.getItem(QUEUE_FILTER_KEY);
    return QUEUE_FILTERS.includes(saved) ? saved : "all";
  } catch (_err) {
    return "all";
  }
}

function saveQueueFilter(value) {
  try {
    window.localStorage.setItem(QUEUE_FILTER_KEY, value);
  } catch (_err) {
    /* session only */
  }
}

function loadTab() {
  try {
    const saved = window.localStorage.getItem(TAB_KEY);
    return saved === "queue" ? "queue" : "overview";
  } catch (_err) {
    return "overview";
  }
}

function saveTab(tab) {
  try {
    window.localStorage.setItem(TAB_KEY, tab);
  } catch (_err) {
    /* session only */
  }
}

function getLinkTemplate() {
  return legionQueue().resolveLinkTemplate(window.LEGION_CONFIG, DEFAULT_GROKBOT_TEMPLATE);
}

let activeTab = loadTab();
let metricsTimer = null;
let queueTimer = null;

function setTab(tab) {
  activeTab = tab === "queue" ? "queue" : "overview";
  saveTab(activeTab);
  const overview = document.getElementById("panel-overview");
  const queuePanel = document.getElementById("panel-queue");
  overview?.classList.toggle("hidden", activeTab !== "overview");
  queuePanel?.classList.toggle("hidden", activeTab !== "queue");
  for (const button of document.querySelectorAll(".tab-bar .tab")) {
    button.setAttribute("aria-pressed", String(button.dataset.tab === activeTab));
  }
  const footer = document.getElementById("footer-refresh");
  if (footer) {
    footer.textContent =
      activeTab === "queue"
        ? "Analyst queue refreshes every 5 seconds while this tab is open"
        : "Refreshes every 5 seconds";
  }
  if (activeTab === "overview") {
    stopQueuePoll();
    startMetricsPoll();
  } else {
    stopMetricsPoll();
    startQueuePoll();
  }
}

function initTabs() {
  const bar = document.querySelector(".tab-bar");
  if (!bar) {
    return;
  }
  bar.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tab]");
    if (!button) {
      return;
    }
    setTab(button.dataset.tab);
  });
  setTab(activeTab);
}

function startMetricsPoll() {
  if (metricsTimer != null) {
    return;
  }
  refresh();
  metricsTimer = window.setInterval(refresh, POLL_MS);
}

function stopMetricsPoll() {
  if (metricsTimer != null) {
    window.clearInterval(metricsTimer);
    metricsTimer = null;
  }
}

function startQueuePoll() {
  if (queueTimer != null) {
    return;
  }
  refreshQueue();
  queueTimer = window.setInterval(refreshQueue, QUEUE_POLL_MS);
}

function stopQueuePoll() {
  if (queueTimer != null) {
    window.clearInterval(queueTimer);
    queueTimer = null;
  }
}

async function fetchOpenCases() {
  let offset = 0;
  let total = 0;
  const items = [];
  do {
    const params = new URLSearchParams({
      limit: String(CASE_PAGE_SIZE),
      offset: String(offset),
    });
    const response = await fetch(`${CASES_URL}?${params}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error("cases list failed");
    }
    const page = await response.json();
    total = Number(page.total) || 0;
    for (const row of page.items || []) {
      items.push(row);
    }
    offset += CASE_PAGE_SIZE;
  } while (offset < total && offset < CASE_PAGE_MAX);
  const L = legionQueue();
  return L ? L.filterOpenCases(items) : items;
}

function renderQueueTable() {
  const L = legionQueue();
  const body = document.querySelector("#queue-table tbody");
  if (!body || !L) {
    return;
  }
  const rows = L.sortQueueCases(L.applyQueueFilter(queueState.cases, queueState.filter));
  body.replaceChildren();
  if (rows.length === 0) {
    const tr = el("tr");
    const td = el("td", "empty", "No open cases in this view");
    td.colSpan = 6;
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  const now = Date.now();
  for (const row of rows) {
    const tr = el("tr");
    tr.tabIndex = 0;
    if (queueState.selectedId === row.id) {
      tr.classList.add("selected");
    }
    const idTd = el("td", "case-id mono", L.formatCaseId(row.id));
    const statusTd = el("td");
    statusTd.appendChild(pill(STATUS_LABELS, row.status, "pill"));
    const sevTd = el("td", "sev-cell");
    sevTd.appendChild(severityChip(row.severity || row.splunk_severity));
    const ruleTd = el("td", "queue-rule", L.ruleLabel(row));
    const ageTd = el("td", null, L.formatQueueAge(row.created_at, now));
    const alertTd = el("td", "alert-id", row.alert_id);
    tr.append(idTd, statusTd, sevTd, ruleTd, ageTd, alertTd);
    tr.addEventListener("click", () => {
      selectQueueCase(row.id);
    });
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectQueueCase(row.id);
      }
    });
    body.appendChild(tr);
  }
}

function appendFormattedText(parent, text) {
  const L = legionQueue();
  const parts = L
    ? L.splitTechnicalTokens(text)
    : [{ value: String(text == null ? "" : text), mono: false }];
  for (const part of parts) {
    if (!part.value) {
      continue;
    }
    if (part.mono) {
      parent.appendChild(el("span", "mono", part.value));
    } else {
      parent.appendChild(document.createTextNode(part.value));
    }
  }
}

function appendProse(parent, text, className) {
  const node = el("p", className || "prose");
  appendFormattedText(node, text);
  parent.appendChild(node);
  return node;
}

function appendCollapsible(parent, text, charLimit) {
  const L = legionQueue();
  const raw = String(text || "");
  if (!raw) {
    return;
  }
  if (!L || !L.shouldCollapse(raw, charLimit)) {
    appendProse(parent, raw);
    return;
  }
  const details = el("details", "collapsible");
  const summary = el("summary", "collapsible-summary");
  const teaser = el("span", "collapsible-teaser");
  appendFormattedText(teaser, L.teaserText(raw, charLimit));
  const toggle = el("span", "collapsible-toggle", "Show more");
  summary.append(teaser, toggle);
  const full = el("div", "collapsible-full");
  appendProse(full, raw);
  details.append(summary, full);
  details.addEventListener("toggle", () => {
    toggle.textContent = details.open ? "Show less" : "Show more";
  });
  parent.appendChild(details);
}

function appendFact(dl, label, fill, wide) {
  const dd = el("dd");
  fill(dd);
  if (!dd.childNodes.length) {
    return;
  }
  const fact = el("div", wide ? "fact wide" : "fact");
  fact.append(el("dt", null, label), dd);
  dl.appendChild(fact);
}

function renderDetailHeader(container, view, L) {
  container.replaceChildren();
  const meta = el("div", "detail-header-meta");
  meta.appendChild(el("span", "mono detail-kicker", `Case #${view.id}`));
  meta.appendChild(el("span", "mono detail-alert", view.alert_id || "no alert id"));
  meta.appendChild(el("span", "detail-age", L.formatQueueAge(view.created_at)));
  container.appendChild(meta);
  const badges = el("div", "detail-badges");
  badges.appendChild(severityChip(view.severity || view.splunk_severity));
  badges.appendChild(pill(STATUS_LABELS, view.status, "pill"));
  const facts = L.extractKeyFacts(view);
  for (const flag of facts.flags) {
    badges.appendChild(el("span", "chip flag-chip", flag));
  }
  const approval = L.approvalState(view);
  if (approval && approval !== "—") {
    badges.appendChild(el("span", `approval-badge tone-${L.approvalTone(view)}`, approval));
  }
  container.appendChild(badges);
  if (view.summary) {
    appendCollapsible(container, view.summary);
  }
}

function renderKeyFacts(container, view, L) {
  container.replaceChildren();
  container.appendChild(el("h3", null, "Key facts"));
  const facts = L.extractKeyFacts(view);
  const dl = el("dl", "facts-grid");
  if (facts.rule) {
    appendFact(
      dl,
      "Rule",
      (dd) => {
        dd.textContent = facts.rule;
      },
      true,
    );
  }
  if (facts.users.length) {
    appendFact(dl, facts.users.length === 1 ? "User" : "Users", (dd) => {
      const list = el("span", "entity-list");
      for (const user of facts.users) {
        list.appendChild(el("span", "chip entity-chip mono", user));
      }
      dd.appendChild(list);
    });
  }
  const assets = [...facts.hosts, ...facts.ips];
  if (assets.length) {
    appendFact(
      dl,
      "Assets",
      (dd) => {
        const list = el("span", "entity-list");
        for (const asset of assets) {
          list.appendChild(el("span", "chip entity-chip mono", asset));
        }
        dd.appendChild(list);
      },
      true,
    );
  }
  if (facts.techniques.length) {
    appendFact(
      dl,
      "MITRE",
      (dd) => {
        const row = el("span", "chip-row");
        for (const tech of facts.techniques) {
          row.appendChild(el("span", "chip tech-chip", tech));
        }
        dd.appendChild(row);
      },
      true,
    );
  }
  if (facts.confidence) {
    appendFact(dl, "Confidence", (dd) => {
      dd.textContent = facts.confidence;
    });
  }
  if (facts.verdict) {
    appendFact(dl, "Verdict", (dd) => {
      dd.appendChild(pill(VERDICT_LABELS, String(view.verdict || "").toLowerCase(), "verdict"));
    });
  }
  if (!dl.childNodes.length) {
    container.appendChild(el("p", "empty", "No key facts recorded yet."));
    return;
  }
  container.appendChild(dl);
}

function renderCenturionNotes(container, view, L) {
  container.replaceChildren();
  container.appendChild(el("h3", null, "CENTURION plan & notes"));
  const notes = L.formatCenturionNotes(view);
  if (!notes.length) {
    container.appendChild(el("p", "empty", "No plan or notes from CENTURION yet."));
    return;
  }
  for (const note of notes) {
    const card = el("article", "note-card");
    appendCollapsible(card, note.text);
    container.appendChild(card);
  }
}

function renderFindings(container, view, L) {
  container.replaceChildren();
  container.appendChild(el("h3", null, "Findings"));
  const findings = view.findings || [];
  if (!findings.length) {
    container.appendChild(el("p", "empty", "No findings recorded yet."));
    return;
  }
  const list = el("div", "finding-list");
  for (const finding of findings) {
    const model = L.findingCardModel(finding, view.evidence);
    const kindClass = model.kind ? `kind-${model.kind}` : "";
    const card = el("article", `finding-card ${kindClass}`.trim());
    const meta = el("div", "finding-meta");
    if (model.kind) {
      meta.appendChild(el("span", `chip kind-chip kind-${model.kind}`, L.humanizeLabel(model.kind)));
    }
    meta.appendChild(el("span", "finding-author", model.author));
    if (model.confidence) {
      meta.appendChild(el("span", "finding-conf", model.confidence));
    }
    card.appendChild(meta);
    appendCollapsible(card, model.statement, 220);
    if (model.evidenceRefs.length) {
      const refs = el("div", "chip-row evidence-refs");
      for (const ref of model.evidenceRefs) {
        refs.appendChild(el("span", "chip ref-chip mono", ref));
      }
      card.appendChild(refs);
    }
    list.appendChild(card);
  }
  container.appendChild(list);
}

function renderDisposition(container, view, L) {
  container.replaceChildren();
  container.appendChild(el("h3", null, "Proposed disposition"));
  const model = L.dispositionModel(view);
  if (!model) {
    container.appendChild(el("p", "empty", "No disposition proposed yet."));
    return;
  }
  const card = el("article", `disposition-card tone-${model.tone}`);
  const rows = el("dl", "facts-grid disposition-facts");
  appendFact(rows, "Disposition", (dd) => {
    dd.textContent = model.action;
  });
  if (model.soarAction && model.soarAction !== model.action) {
    appendFact(rows, "SOAR action", (dd) => {
      const node = el("span");
      appendFormattedText(node, model.soarAction);
      dd.appendChild(node);
    });
  }
  card.appendChild(rows);
  if (model.rationale) {
    card.appendChild(el("h4", "disposition-rationale-label", "Rationale"));
    appendCollapsible(card, model.rationale, 200);
  }
  card.appendChild(el("p", `approval-badge tone-${model.tone}`, model.approval));
  container.appendChild(card);
}

function renderEvidence(container, view, L) {
  container.replaceChildren();
  container.appendChild(el("h3", null, "Evidence"));
  const items = L.evidenceListItems(view.evidence);
  const count = items.length;
  container.appendChild(
    el("p", "evidence-count", `${count} item${count === 1 ? "" : "s"}`),
  );
  if (!count) {
    return;
  }
  const list = el("ul", "evidence-list");
  for (const item of items) {
    const li = el("li");
    li.appendChild(el("span", "chip source-chip", item.source));
    li.appendChild(el("span", "mono", item.ref));
    list.appendChild(li);
  }
  container.appendChild(list);
}

function updateGrokButton(view) {
  const link = document.getElementById("queue-grok-link");
  const L = legionQueue();
  if (!link || !L || !view) {
    return;
  }
  const built = L.buildGrokLink(getLinkTemplate(), view.id, view.alert_id);
  if (L.isAllowedLinkScheme(built)) {
    link.href = built;
    link.removeAttribute("aria-disabled");
  } else {
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
  }
}

function renderQueueDetail(view) {
  const L = legionQueue();
  const body = document.getElementById("queue-detail-body");
  const subtitle = document.getElementById("queue-detail-sub");
  const title = document.getElementById("queue-detail-title");
  const actions = document.getElementById("queue-actions");
  if (!body || !L || !view) {
    body?.classList.add("hidden");
    actions?.classList.add("hidden");
    return;
  }
  body.classList.remove("hidden");
  actions?.classList.remove("hidden");
  if (title) {
    title.textContent = view.title || `Case #${view.id}`;
  }
  if (subtitle) {
    subtitle.textContent = "";
  }
  const header = document.getElementById("queue-detail-header");
  const facts = document.getElementById("queue-key-facts");
  const notes = document.getElementById("queue-centurion");
  const findings = document.getElementById("queue-findings");
  const disposition = document.getElementById("queue-disposition-block");
  const evidence = document.getElementById("queue-evidence");
  if (header) {
    renderDetailHeader(header, view, L);
  }
  if (facts) {
    renderKeyFacts(facts, view, L);
  }
  if (disposition) {
    renderDisposition(disposition, view, L);
  }
  if (notes) {
    renderCenturionNotes(notes, view, L);
  }
  if (findings) {
    renderFindings(findings, view, L);
  }
  if (evidence) {
    renderEvidence(evidence, view, L);
  }
  updateGrokButton(view);
}

async function selectQueueCase(caseId) {
  queueState.selectedId = caseId;
  renderQueueTable();
  const body = document.getElementById("queue-detail-body");
  body?.classList.add("hidden");
  document.getElementById("queue-actions")?.classList.add("hidden");
  try {
    const response = await fetch(`${CASES_URL}/${caseId}/view`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return;
    }
    const view = await response.json();
    queueState.selectedView = view;
    renderQueueDetail(view);
  } catch (_err) {
    /* keep list usable */
  }
}

async function refreshQueue() {
  if (activeTab !== "queue") {
    return;
  }
  try {
    queueState.cases = await fetchOpenCases();
    renderQueueTable();
    if (queueState.selectedId != null) {
      const still = queueState.cases.some((row) => row.id === queueState.selectedId);
      if (still) {
        await selectQueueCase(queueState.selectedId);
      } else {
        queueState.selectedId = null;
        queueState.selectedView = null;
        document.getElementById("queue-detail-body")?.classList.add("hidden");
        document.getElementById("queue-actions")?.classList.add("hidden");
        const title = document.getElementById("queue-detail-title");
        if (title) {
          title.textContent = "Case detail";
        }
        const subtitle = document.getElementById("queue-detail-sub");
        if (subtitle) {
          subtitle.textContent = "Select a case to load its view.";
        }
      }
    }
    document.getElementById("live-indicator")?.classList.remove("stale");
    const updated = document.getElementById("last-updated");
    if (updated) {
      updated.textContent = `Live · updated ${clock(new Date())}`;
    }
  } catch (_err) {
    document.getElementById("live-indicator")?.classList.add("stale");
  }
}

function initQueueFilter() {
  const group = document.getElementById("queue-filter");
  if (!group) {
    return;
  }
  for (const input of group.querySelectorAll('input[type="radio"]')) {
    input.checked = input.value === queueState.filter;
    input.addEventListener("change", () => {
      if (!input.checked) {
        return;
      }
      queueState.filter = input.value;
      saveQueueFilter(queueState.filter);
      renderQueueTable();
    });
  }
}

function initQueueActions() {
  const copyBtn = document.getElementById("queue-copy-id");
  const status = document.getElementById("queue-copy-status");
  copyBtn?.addEventListener("click", async () => {
    if (queueState.selectedId == null) {
      return;
    }
    const text = String(queueState.selectedId);
    try {
      await navigator.clipboard.writeText(text);
      if (status) {
        status.textContent = "Case id copied";
        status.classList.remove("hidden");
        window.setTimeout(() => status.classList.add("hidden"), 2000);
      }
    } catch (_err) {
      if (status) {
        status.textContent = "Could not copy";
        status.classList.remove("hidden");
      }
    }
  });
}

initFilter();
initTabs();
initQueueFilter();
initQueueActions();
