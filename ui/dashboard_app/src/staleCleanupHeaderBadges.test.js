import test from "node:test";
import assert from "node:assert/strict";

import { buildStaleCleanupHeaderBadges } from "./staleCleanupHeaderBadges.js";

test("prefers blocked count over other stale cleanup counts", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      totalCount: 9,
      blockedCount: 2,
      previewReadyCount: 3,
      cleanedCount: 4,
    }),
    [
      {
        key: "blocked-count",
        label: "blocked 2",
        tone: "critical",
        title:
          "9 stale cleanup review items. 2 blocked stale cleanup items. 3 preview-ready stale cleanup items. 4 cleaned stale cleanup items.",
      },
    ]
  );
});

test("uses preview count when blocked count is absent", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      totalCount: 7,
      blockedCount: 0,
      previewReadyCount: 3,
      cleanedCount: 4,
    }),
    [
      {
        key: "preview-ready-count",
        label: "preview-ready 3",
        tone: "warning",
        title:
          "7 stale cleanup review items. 3 preview-ready stale cleanup items. 4 cleaned stale cleanup items.",
      },
    ]
  );
});

test("uses cleaned count when it is the only positive count", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      totalCount: 4,
      blockedCount: 0,
      previewReadyCount: 0,
      cleanedCount: 4,
    }),
    [
      {
        key: "cleaned-count",
        label: "cleaned 4",
        tone: "ready",
        title: "4 stale cleanup review items. 4 cleaned stale cleanup items.",
      },
    ]
  );
});

test("returns empty list for invalid or empty counts", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      blockedCount: null,
      previewReadyCount: undefined,
      cleanedCount: 0,
    }),
    []
  );
});

test("uses singular tooltip labels for count 1", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      totalCount: 1,
      blockedCount: 1,
      previewReadyCount: 0,
      cleanedCount: 0,
    }),
    [
      {
        key: "blocked-count",
        label: "blocked 1",
        tone: "critical",
        title: "1 stale cleanup review item. 1 blocked stale cleanup item.",
      },
    ]
  );
});

test("omits total review count when it is not provided", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      blockedCount: 2,
      previewReadyCount: 0,
      cleanedCount: 0,
    }),
    [
      {
        key: "blocked-count",
        label: "blocked 2",
        tone: "critical",
        title: "2 blocked stale cleanup items.",
      },
    ]
  );
});

test("falls back to total review count when outcome counts are unavailable", () => {
  assert.deepEqual(
    buildStaleCleanupHeaderBadges({
      totalCount: 5,
      blockedCount: 0,
      previewReadyCount: 0,
      cleanedCount: 0,
    }),
    [
      {
        key: "total-count",
        label: "stale cleanup review 5",
        tone: "neutral",
        title: "5 stale cleanup review items.",
      },
    ]
  );
});
