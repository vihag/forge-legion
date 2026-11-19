"use strict";

const METRICS_URL = "/case/v1/metrics";
const POLL_MS = 5000;

function formatTiming(block) {
  if (!block || block.n === 0) {
    return "n=0";
  }
  const median = block.median === null ? "—" : block.median;
  const average = block.average === null ? "—" : block.average;
  return `n=${block.n}, median=${median}s, avg=${average}s`;
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) {
    node.textContent = value == null ? "—" : String(value);
  }
}

function setField(path, value) {
  const el = document.querySelector(`[data-field="${path}"]`);
  if (el) {
    el.textContent = value == null ? "—" : String(value);
  }
}

function renderUplifts(uplifts) {
  const node = document.getElementById("uplift-summary");
  if (!node) {
    return;
  }
  const keys = Object.keys(uplifts || {});
  if (keys.length === 0) {
    node.textContent = "0";
    return;
  }
  node.textContent = keys.map((key) => `${key}: ${uplifts[key]}`).join(", ");
}

function renderRecent(recent) {
  const body = document.querySelector("#recent-table tbody");
  if (!body) {
    return;
  }
  body.replaceChildren();
  for (const row of recent || []) {
    const tr = document.createElement("tr");
    const cells = [
      row.id,
      row.alert_id,
      row.status,
      row.route || "—",
      row.verdict || "—",
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = String(text);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function renderAgents(agents) {
  const list = document.getElementById("agents-list");
  if (!list) {
    return;
  }
  list.replaceChildren();
  for (const agent of agents || []) {
    const li = document.createElement("li");
    li.textContent = `${agent.actor_id} (${agent.type}) — ${agent.events} events, last ${agent.last_seen}`;
    list.appendChild(li);
  }
}

function applyMetrics(metrics) {
  setField("counts.total", metrics.counts.total);
  setField("counts.auto_resolved", metrics.counts.auto_resolved);
  setField("counts.collaborative_open", metrics.counts.collaborative_open);
  setField("counts.needs_review", metrics.counts.needs_review);
  renderUplifts(metrics.uplifts);

  const banner = document.getElementById("auto-off-banner");
  const minutesTile = document.getElementById("tile-minutes");
  if (metrics.auto_resolve_enabled) {
    banner?.classList.add("hidden");
    setField("minutes_saved_estimate", metrics.minutes_saved_estimate);
    minutesTile?.classList.remove("off");
  } else {
    banner?.classList.remove("hidden");
    setField("minutes_saved_estimate", "auto-resolve off");
    minutesTile?.classList.add("off");
  }

  setField("assumptions.minutes_per_alert", metrics.assumptions.minutes_per_alert);
  setText("#time-triage", formatTiming(metrics.time_to_triage_s));
  setText("#time-ai-only", formatTiming(metrics.time_to_resolve_s.ai_only));
  setText("#time-ai-human", formatTiming(metrics.time_to_resolve_s.ai_plus_human));
  renderRecent(metrics.recent);
  renderAgents(metrics.agents);
}

async function refresh() {
  try {
    const response = await fetch(METRICS_URL, { headers: { Accept: "application/json" } });
    if (!response.ok) {
      return;
    }
    const metrics = await response.json();
    applyMetrics(metrics);
  } catch (_err) {
    /* polling continues */
  }
}

refresh();
window.setInterval(refresh, POLL_MS);