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

  it("orders critical before high in the same status, and oldest first on a severity tie", () => {
    const cases = [
      { id: 2, status: "DISPOSITION_PROPOSED", severity: "high", created_at: "2026-01-01T00:00:00Z" },
      { id: 1, status: "DISPOSITION_PROPOSED", severity: "critical", created_at: "2026-01-02T00:00:00Z" },
      { id: 4, status: "NEEDS_REVIEW", severity: "medium", created_at: "2026-01-03T00:00:00Z" },
      { id: 3, status: "NEEDS_REVIEW", severity: "medium", created_at: "2026-01-01T00:00:00Z" },
    ];
    const sorted = queue.sortQueueCases(cases);
    assert.deepEqual(
      sorted.map((c) => c.id),
      [1, 2, 3, 4],
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
      { id: 5, status: "SUBMITTED", severity: "low", created_at: "t5" },
      { id: 6, status: "REJECTED", severity: "low", created_at: "t6" },
    ];
    const open = queue.applyQueueFilter(cases, "all");
    assert.deepEqual(open.map((c) => c.id).sort(), [1, 3, 4]);
    assert.ok(!open.some((c) => ["AUTO_RESOLVED", "SUBMITTED", "REJECTED"].includes(c.status)));
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

describe("resolveLinkTemplate", () => {
  const fallback = "grokbot://app/v1/agent?id=8818388d-d88b-4b48-a907-11dcc6b8547d";

  it("falls back when the template is missing, empty, whitespace, or an unsubstituted literal", () => {
    assert.equal(queue.resolveLinkTemplate(undefined, fallback), fallback);
    assert.equal(queue.resolveLinkTemplate({}, fallback), fallback);
    assert.equal(queue.resolveLinkTemplate({ grokbotCenturionLinkTemplate: "" }, fallback), fallback);
    assert.equal(queue.resolveLinkTemplate({ grokbotCenturionLinkTemplate: "   " }, fallback), fallback);
    assert.equal(
      queue.resolveLinkTemplate(
        { grokbotCenturionLinkTemplate: "${GROKBOT_CENTURION_LINK_TEMPLATE}" },
        fallback,
      ),
      fallback,
    );
  });

  it("returns a configured template", () => {
    assert.equal(
      queue.resolveLinkTemplate({ grokbotCenturionLinkTemplate: "https://example.com/{case_id}" }, fallback),
      "https://example.com/{case_id}",
    );
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

  it("builds the exact encoded URL for special characters in the alert id", () => {
    const url = queue.buildGrokLink(
      "grokbot://x?case={case_id}&alert={alert_id}&q={text}",
      7,
      "a&b #c",
    );
    assert.equal(
      url,
      "grokbot://x?case=7&alert=a%26b%20%23c&q=Let's%20work%20case%207%20(a%26b%20%23c)",
    );
  });

  it("uses 'no alert id' when the alert id is missing", () => {
    const template = "grokbot://x?case={case_id}&alert={alert_id}&q={text}";
    const expected =
      "grokbot://x?case=7&alert=no%20alert%20id&q=Let's%20work%20case%207%20(no%20alert%20id)";
    assert.equal(queue.buildGrokLink(template, 7, null), expected);
    assert.equal(queue.buildGrokLink(template, 7, undefined), expected);
    assert.equal(queue.buildGrokLink(template, 7, ""), expected);
    const url = queue.buildGrokLink(template, 7, null);
    assert.ok(!url.includes("null"));
    assert.ok(!url.includes("undefined"));
    assert.ok(!url.includes("(null)"));
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