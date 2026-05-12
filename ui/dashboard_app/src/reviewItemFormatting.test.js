import test from "node:test";
import assert from "node:assert/strict";

import {
  formatReviewItemAgeLabel,
  formatReviewItemOutcomeLabel,
  formatReviewItemReasonLabel,
  formatReviewItemScopeLabel,
  getReviewItemOutcomeTone,
} from "./reviewItemFormatting.js";

test("returns null for invalid age values", () => {
  assert.equal(formatReviewItemAgeLabel(null), null);
  assert.equal(formatReviewItemAgeLabel(undefined), null);
  assert.equal(formatReviewItemAgeLabel("421"), null);
  assert.equal(formatReviewItemAgeLabel(-1), null);
  assert.equal(formatReviewItemAgeLabel(Number.NaN), null);
});

test("formats sub-minute age in seconds", () => {
  assert.equal(formatReviewItemAgeLabel(59), "age 59s");
});

test("formats minute-level ages without trailing zero seconds", () => {
  assert.equal(formatReviewItemAgeLabel(60), "age 1m");
  assert.equal(formatReviewItemAgeLabel(421), "age 7m 1s");
});

test("formats hour-level ages", () => {
  assert.equal(formatReviewItemAgeLabel(3600), "age 1h");
  assert.equal(formatReviewItemAgeLabel(3661), "age 1h 1m 1s");
});

test("formats known review item reason codes into friendly labels", () => {
  assert.equal(
    formatReviewItemReasonLabel("STALE_SIGNAL_AGE_EXCEEDED"),
    "stale age"
  );
  assert.equal(
    formatReviewItemReasonLabel("INVALID_SIGNAL_SCANNED_AT"),
    "invalid scanned_at"
  );
  assert.equal(
    formatReviewItemReasonLabel("SIGNAL_TIMESTAMP_IN_FUTURE"),
    "future timestamp"
  );
});

test("keeps unknown reason codes intact", () => {
  assert.equal(
    formatReviewItemReasonLabel("UNEXPECTED_REASON_CODE"),
    "UNEXPECTED_REASON_CODE"
  );
  assert.equal(formatReviewItemReasonLabel(""), "-");
  assert.equal(formatReviewItemReasonLabel(null), "-");
});

test("formats known review item scopes into friendly labels", () => {
  assert.equal(formatReviewItemScopeLabel("buy"), "buy signal");
  assert.equal(formatReviewItemScopeLabel("sell"), "sell signal");
});

test("keeps unknown review item scopes intact", () => {
  assert.equal(formatReviewItemScopeLabel("custom_scope"), "custom_scope");
  assert.equal(formatReviewItemScopeLabel(""), "-");
  assert.equal(formatReviewItemScopeLabel(null), "-");
});

test("formats known review item outcomes into friendly labels", () => {
  assert.equal(formatReviewItemOutcomeLabel("BLOCKED"), "blocked");
  assert.equal(formatReviewItemOutcomeLabel("PREVIEW_READY"), "preview-ready");
  assert.equal(formatReviewItemOutcomeLabel("CLEANED"), "cleaned");
});

test("keeps unknown review item outcomes intact", () => {
  assert.equal(
    formatReviewItemOutcomeLabel("UNEXPECTED_OUTCOME"),
    "UNEXPECTED_OUTCOME"
  );
  assert.equal(formatReviewItemOutcomeLabel(""), "-");
  assert.equal(formatReviewItemOutcomeLabel(null), "-");
});

test("maps known review item outcomes to visual tones", () => {
  assert.equal(getReviewItemOutcomeTone("BLOCKED"), "blocked");
  assert.equal(getReviewItemOutcomeTone("PREVIEW_READY"), "preview-ready");
  assert.equal(getReviewItemOutcomeTone("CLEANED"), "cleaned");
});

test("falls back to neutral tone for unknown outcomes", () => {
  assert.equal(getReviewItemOutcomeTone("UNEXPECTED_OUTCOME"), "neutral");
  assert.equal(getReviewItemOutcomeTone(""), "neutral");
  assert.equal(getReviewItemOutcomeTone(null), "neutral");
});
