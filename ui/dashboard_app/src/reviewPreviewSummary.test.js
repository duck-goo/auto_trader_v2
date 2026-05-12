import test from "node:test";
import assert from "node:assert/strict";

import { buildReviewItemPreviewSummary } from "./reviewPreviewSummary.js";

test("returns null when there are no preview items", () => {
  assert.equal(buildReviewItemPreviewSummary([], 3), null);
  assert.equal(buildReviewItemPreviewSummary(null, 3), null);
});

test("returns null when the preview already shows every item", () => {
  assert.equal(
    buildReviewItemPreviewSummary(
      [{ outcome: "BLOCKED" }, { outcome: "CLEANED" }],
      2,
      {
        blockedCount: 1,
        previewReadyCount: 0,
        cleanedCount: 1,
      },
    ),
    null,
  );
});

test("builds a compact summary label and hidden outcome tooltip", () => {
  assert.deepEqual(
    buildReviewItemPreviewSummary(
      [
        { outcome: "BLOCKED" },
        { outcome: "PREVIEW_READY" },
        { outcome: "CLEANED" },
      ],
      5,
      {
        blockedCount: 2,
        previewReadyCount: 2,
        cleanedCount: 1,
      },
    ),
    {
      label: "+2 more",
      title:
        "Showing 3 of 5 stale cleanup review items. Hidden items by status: 1 blocked, 1 preview-ready.",
    },
  );
});

test("omits hidden outcome details when hidden counts are unavailable", () => {
  assert.deepEqual(
    buildReviewItemPreviewSummary([{ outcome: "BLOCKED" }], 2),
    {
      label: "+1 more",
      title: "Showing 1 of 2 stale cleanup review items.",
    },
  );
});
