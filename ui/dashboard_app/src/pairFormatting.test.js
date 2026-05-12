import test from "node:test";
import assert from "node:assert/strict";

import {
  buildBadgeListTitle,
  buildBadgePreviewMetadata,
  buildBadgePreviewValues,
  parsePairBadgeValues,
} from "./pairFormatting.js";

test("returns empty list for invalid values", () => {
  assert.deepEqual(parsePairBadgeValues(null), []);
  assert.deepEqual(parsePairBadgeValues(undefined), []);
  assert.deepEqual(parsePairBadgeValues(123), []);
  assert.deepEqual(parsePairBadgeValues(""), []);
  assert.deepEqual(parsePairBadgeValues("   "), []);
});

test("splits comma-delimited badge values", () => {
  assert.deepEqual(parsePairBadgeValues("005930, 000660, 035420"), [
    "005930",
    "000660",
    "035420",
  ]);
});

test("deduplicates badge values while preserving order", () => {
  assert.deepEqual(parsePairBadgeValues("005930, 000660, 005930"), [
    "005930",
    "000660",
  ]);
});

test("returns undefined badge list title for invalid values", () => {
  assert.equal(buildBadgeListTitle(null), undefined);
  assert.equal(buildBadgeListTitle(""), undefined);
});

test("builds a prefixed badge list title", () => {
  assert.equal(
    buildBadgeListTitle("005930, 000660, 035420", {
      titlePrefix: "All symbols",
    }),
    "All symbols: 005930, 000660, 035420",
  );
});

test("applies formatting when building a badge list title", () => {
  assert.equal(
    buildBadgeListTitle("A, B", {
      titlePrefix: "All reasons",
      formatValue: (value) => value.toLowerCase(),
    }),
    "All reasons: a, b",
  );
});

test("builds compact badge preview when values exceed the max visible count", () => {
  assert.deepEqual(
    buildBadgePreviewValues("005930, 000660, 035420, 051910"),
    ["005930", "000660", "035420", "+1"],
  );
});

test("returns full badge list when preview compaction is not needed", () => {
  assert.deepEqual(
    buildBadgePreviewValues("005930, 000660, 035420"),
    ["005930", "000660", "035420"],
  );
});

test("returns hidden badge values when preview compaction is applied", () => {
  assert.deepEqual(
    buildBadgePreviewMetadata("005930, 000660, 035420, 051910"),
    {
      badges: ["005930", "000660", "035420", "+1"],
      hiddenValues: ["051910"],
    },
  );
});

test("returns empty hidden badge values when preview compaction is not needed", () => {
  assert.deepEqual(
    buildBadgePreviewMetadata("005930, 000660, 035420"),
    {
      badges: ["005930", "000660", "035420"],
      hiddenValues: [],
    },
  );
});
