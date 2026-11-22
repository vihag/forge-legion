import assert from "node:assert/strict";
import { describe, it } from "node:test";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const queue = require(path.resolve(import.meta.dirname, "../../console/queue.js"));

describe("queue sort", () => {
  it("orders disposition proposed before needs review, then severity, then age", () => {
    const cases = [
      { id: 3, status: "NEEDS_REVIEW", severity: "high", created_at: "2026-01-02T00:00:00Z" },
      { id: 1, status: "DISPOSITION_PROPOSED", severity: "medium", created_at: "2026-01-03T00:00:00Z" },
      { id: 2, status: "DISPOSITION_PROPOSED", severity: "critical", created_at: "2026-01-01T00:00:00Z" },
      { id: 4, status: "INVESTIGATING", severity: "critical", created_at: "2026-01-01T00:00:00Z" },
    ];
    const sorted = queue.sortQueueCases(cases);
    assert.deepEqual(
      sorted.map((c) => c.id),
      [2, 1, 3, 4],
    );
  });
});

describe("queue filter", () => {
  it("excludes closed statuses and applies radio filters", () => {
    const cases = [
      { id: 1, status: "DISPOSITION_PROPOSED", severity: "low", created_at: "t1" },
      { id: 2, status: "AUTO_RESOLVED", severity: "low", created_at: "t2" },
      { id: 3, status: "INVESTIGATING", severity: "low", created_at: "t3" },
      { id: 4, status: "NEEDS_REVIEW", severity: "low", created_at: "t4" },
    ];
    const open = queue.applyQueueFilter(cases, "all");
    assert.equal(open.length, 3);
    const approvals = queue.applyQueueFilter(cases, "approvals");
    assert.deepEqual(approvals.map((c) => c.id), [1]);
    const mixed = queue.applyQueueFilter(cases, "needs_investigating");
    assert.deepEqual(mixed.map((c) => c.id).sort(), [3, 4]);
  });
});

describe("formatQueueAge", () => {
  it("formats short durations", () => {
    const now = Date.parse("2026-01-01T00:12:00Z");
    assert.equal(queue.formatQueueAge("2026-01-01T00:00:00Z", now), "12 min");
    assert.equal(queue.formatQueueAge("2026-01-01T00:11:30Z", now), "30s");
  });
});

describe("buildGrokLink", () => {
  it("substitutes encoded placeholders", () => {
    const url = queue.buildGrokLink(
      "grokbot://x?id=1&case={case_id}&alert={alert_id}&q={text}",
      42,
      "SPL-1",
    );
    assert.ok(url.includes("case=42"));
    assert.ok(url.includes(encodeURIComponent("SPL-1")));
    assert.ok(url.includes(encodeURIComponent("Let's work case 42 (SPL-1)")));
  });
});

describe("isAllowedLinkScheme", () => {
  it("allows grokbot and https only", () => {
    assert.equal(queue.isAllowedLinkScheme("grokbot://app/v1/agent"), true);
    assert.equal(queue.isAllowedLinkScheme("https://example.com/c"), true);
    assert.equal(queue.isAllowedLinkScheme("http://example.com"), false);
    assert.equal(queue.isAllowedLinkScheme("javascript:alert(1)"), false);
  });
});

describe("centurion helpers", () => {
  it("picks centurion audit and findings", () => {
    const findings = [
      { author_id: "centurion", statement: "a" },
      { author_id: "other", statement: "b" },
    ];
    const audit = [
      { type: "case.plan", actor: { id: "bot" } },
      { type: "case.triaged", actor: { id: "centurion" } },
    ];
    assert.equal(queue.centurionFindings(findings).length, 1);
    assert.equal(queue.centurionAudit(audit).length, 2);
  });
});

describe("approvalState", () => {
  it("shows waiting when disposition proposed", () => {
    assert.equal(
      queue.approvalState({ status: "DISPOSITION_PROPOSED", decision: null }),
      "Waiting for analyst approval",
    );
    assert.match(
      queue.approvalState({
        status: "APPROVED",
        decision: { decision: "approve", analyst_id: "vihag" },
      }),
      /Approved by vihag/,
    );
  });
});