import assert from "node:assert/strict";
import { describe, it } from "node:test";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const queue = require(path.resolve(import.meta.dirname, "../../console/queue.js"));

describe("formatConfidence", () => {
  it("formats ratios and leaves missing values empty", () => {
    assert.equal(queue.formatConfidence(0.85), "85%");
    assert.equal(queue.formatConfidence(1), "100%");
    assert.equal(queue.formatConfidence(0), "0%");
    assert.equal(queue.formatConfidence(80), "80%");
    assert.equal(queue.formatConfidence(null), null);
    assert.equal(queue.formatConfidence(undefined), null);
    assert.equal(queue.formatConfidence("n/a"), null);
  });
});

describe("humanizeLabel", () => {
  it("turns underscored tokens into a readable label", () => {
    assert.equal(queue.humanizeLabel("privileged_account_collaborative"), "Privileged account collaborative");
    assert.equal(queue.humanizeLabel("false_positive"), "False positive");
    assert.equal(queue.humanizeLabel(""), "");
  });
});

describe("extractKeyFacts", () => {
  it("pulls rule, entities, MITRE, confidence and flags from a case view", () => {
    const view = {
      rule: "privileged_account_collaborative",
      confidence: 0.85,
      verdict: "malicious",
      privileged_account: true,
      crown_jewel_asset: true,
      findings: [
        { statement: "T1105 (Ingress Tool Transfer) on JMP-01." },
        { statement: "T1567 likely followed." },
      ],
      evidence: [
        {
          source: "auth",
          content: {
            entities: { user: "adm.mtan", src_ip: "203.0.113.45", hosts: ["VPN-GW-01"] },
            user: "adm.mtan",
            src_ip: "203.0.113.45",
          },
        },
        {
          source: "edr",
          content: { host: "JMP-01", entities: { hosts: ["JMP-01", "FIN-DB-01"] } },
        },
        {
          source: "triage_assessment",
          content: {
            alert_mitre_techniques: ["T1078", "T1021"],
            assessment: { mitre_techniques: ["T1078", "T1021.001"] },
            user: "should-not-appear",
          },
        },
      ],
    };
    const facts = queue.extractKeyFacts(view);
    assert.equal(facts.rule, "Privileged account collaborative");
    assert.deepEqual(facts.users, ["adm.mtan"]);
    assert.deepEqual(facts.hosts, ["VPN-GW-01", "JMP-01", "FIN-DB-01"]);
    assert.deepEqual(facts.ips, ["203.0.113.45"]);
    assert.deepEqual(facts.techniques, ["T1021", "T1021.001", "T1078", "T1105", "T1567"]);
    assert.equal(facts.confidence, "85%");
    assert.deepEqual(facts.flags, ["Privileged account", "Crown jewel"]);
    assert.equal(facts.verdict, "Malicious");
  });

  it("does not treat mid-sentence MITRE mentions as techniques", () => {
    const facts = queue.extractKeyFacts({
      evidence: [
        {
          source: "triage_assessment",
          content: { assessment: { mitre_techniques: ["T1078"] } },
        },
      ],
      findings: [
        { statement: "A single push is not enough to claim T1621." },
        { statement: "T1105 (Ingress Tool Transfer) is supported." },
      ],
    });
    assert.deepEqual(facts.techniques, ["T1078", "T1105"]);
  });

  it("handles a sparse view without throwing", () => {
    const facts = queue.extractKeyFacts({});
    assert.equal(facts.rule, "");
    assert.deepEqual(facts.users, []);
    assert.deepEqual(facts.hosts, []);
    assert.deepEqual(facts.ips, []);
    assert.deepEqual(facts.techniques, []);
    assert.equal(facts.confidence, null);
    assert.deepEqual(facts.flags, []);
    assert.equal(facts.verdict, "");
    assert.deepEqual(queue.extractKeyFacts(null).techniques, []);
  });
});

describe("collapsible text", () => {
  it("collapses long or multi-line notes and cuts the teaser on a word", () => {
    assert.equal(queue.shouldCollapse("short"), false);
    assert.equal(queue.shouldCollapse("x".repeat(361)), true);
    assert.equal(queue.shouldCollapse("a\nb\nc\nd\ne"), true);
    const text = `${"word ".repeat(80)}end`;
    const teaser = queue.teaserText(text, 40);
    assert.ok(teaser.endsWith("…"));
    assert.ok(!teaser.includes("word…") || teaser.endsWith("…"));
    assert.ok(teaser.length <= 42);
    assert.equal(queue.teaserText("already short", 40), "already short");
    const letter =
      "First paragraph stays intact and is long enough.\n\nSecond paragraph is hidden behind the fold and continues for a while.";
    assert.equal(queue.teaserText(letter, 80), "First paragraph stays intact and is long enough.…");
  });
});

describe("splitTechnicalTokens", () => {
  it("marks IPs, MITRE IDs, hosts, URLs, hashes and dotted usernames as mono", () => {
    const text =
      "adm.mtan from 203.0.113.45 used T1078.004 on VPN-GW-01 and fetched https://dl.example.net/tools/rclone.zip ev EV-AUTH-0003 hash 0123456789abcdef0123456789abcdef";
    const parts = queue.splitTechnicalTokens(text);
    const mono = parts.filter((p) => p.mono).map((p) => p.value);
    assert.ok(mono.includes("adm.mtan"));
    assert.ok(mono.includes("203.0.113.45"));
    assert.ok(mono.includes("T1078.004"));
    assert.ok(mono.includes("VPN-GW-01"));
    assert.ok(mono.includes("https://dl.example.net/tools/rclone.zip"));
    assert.ok(mono.includes("EV-AUTH-0003"));
    assert.ok(mono.includes("0123456789abcdef0123456789abcdef"));
    assert.equal(parts.map((p) => p.value).join(""), text);
  });

  it("does not treat domain names as usernames", () => {
    const parts = queue.splitTechnicalTokens("download from dl.example.net after adm.mtan signed in");
    const mono = parts.filter((p) => p.mono).map((p) => p.value);
    assert.ok(mono.includes("adm.mtan"));
    assert.ok(!mono.includes("dl.example"));
    assert.ok(!mono.includes("example.net"));
    assert.equal(parts.map((p) => p.value).join(""), "download from dl.example.net after adm.mtan signed in");
  });
});

describe("formatCenturionNotes", () => {
  it("keeps plan prose and drops raw JSON-only audit payloads", () => {
    const notes = queue.formatCenturionNotes({
      audit: [
        {
          type: "case.plan",
          actor: { id: "centurion" },
          payload: { plan: "Isolate JMP-01.\nReset adm.mtan." },
        },
        {
          type: "case.note",
          actor: { id: "analyst-bot" },
          payload: { note: "Waiting on VPN session IDs." },
        },
        {
          type: "case.plan",
          actor: { id: "centurion" },
          payload: { disposition_id: 1, action: "escalate" },
        },
        {
          type: "case.triaged",
          actor: { id: "centurion" },
          payload: { severity: "high" },
        },
      ],
    });
    assert.equal(notes.length, 2);
    assert.equal(notes[0].text, "Isolate JMP-01.\nReset adm.mtan.");
    assert.equal(notes[1].text, "Waiting on VPN session IDs.");
    const dumped = JSON.stringify(notes);
    assert.ok(!dumped.includes("disposition_id"));
    assert.ok(!dumped.includes("severity"));
  });
});

describe("finding and evidence models", () => {
  it("labels SENTINEL authors and maps evidence ids to refs", () => {
    const evidence = [
      { id: 24, source: "auth", ref: "EV-AUTH-0002" },
      { id: 25, source: "edr", ref: "EV-EDR-0001" },
    ];
    const model = queue.findingCardModel(
      {
        kind: "hypothesis",
        author_id: "mcp:threat-sentinel",
        confidence: 0.75,
        statement: "T1078 on adm.mtan.",
        evidence_ids: [24, 25, 99],
      },
      evidence,
    );
    assert.equal(model.kind, "hypothesis");
    assert.equal(model.author, "Threat SENTINEL");
    assert.equal(model.confidence, "75%");
    assert.deepEqual(model.evidenceRefs, ["EV-AUTH-0002", "EV-EDR-0001", "99"]);
    assert.deepEqual(queue.evidenceListItems(evidence), [
      { id: 24, source: "auth", ref: "EV-AUTH-0002" },
      { id: 25, source: "edr", ref: "EV-EDR-0001" },
    ]);
    assert.deepEqual(queue.evidenceListItems(undefined), []);
  });
});

describe("dispositionModel and approvalState", () => {
  it("builds a waiting card and a submitted SOAR label", () => {
    const waiting = queue.dispositionModel({
      status: "DISPOSITION_PROPOSED",
      proposed_disposition: {
        action: "escalate",
        rationale: "Contain and escalate.",
      },
      decision: null,
      soar: null,
    });
    assert.equal(waiting.action, "Escalate");
    assert.equal(waiting.soarAction, "Escalate");
    assert.equal(waiting.rationale, "Contain and escalate.");
    assert.equal(waiting.approval, "Waiting for analyst approval in Grok Bot");
    assert.equal(waiting.tone, "waiting");

    const submitted = queue.dispositionModel({
      status: "SUBMITTED",
      proposed_disposition: { action: "contain", rationale: "Isolate the host." },
      decision: { decision: "approve", analyst_id: "vihag" },
      soar: { soar_case_id: "SOAR-000001" },
    });
    assert.equal(submitted.action, "Contain");
    assert.equal(submitted.soarAction, "Submitted as SOAR-000001");
    assert.equal(submitted.approval, "Approved by vihag");
    assert.equal(submitted.tone, "approved");

    assert.equal(queue.dispositionModel({ status: "TRIAGED" }), null);
  });
});
