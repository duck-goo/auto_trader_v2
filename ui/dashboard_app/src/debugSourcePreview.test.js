import test from "node:test";
import assert from "node:assert/strict";

import { SNAPSHOT_SOURCE_UPDATE_KINDS } from "./snapshotSourceInfo.js";
import { resolveDebugSourcePreview } from "./debugSourcePreview.js";

test("returns null when source preview is not requested", () => {
  assert.equal(resolveDebugSourcePreview("", "2026-04-29"), null);
});

test("builds long file source preview from query params", () => {
  const preview = resolveDebugSourcePreview(
    "?source_preview=file-long",
    "2026-04-29",
  );

  assert.equal(preview.tradeDate, "2026-04-29");
  assert.equal(preview.apiStatus, "unknown");
  assert.equal(preview.notice.title, "Preview Mode Active");
  assert.match(preview.notice.detail, /long uploaded file preview/i);
  assert.equal(preview.skipInitialApiLoad, true);
  assert.equal(preview.sourceInfo.label, "Uploaded review snapshot");
  assert.equal(
    preview.sourceInfo.detailLabel,
    "very_long_operational_review_snapshot_filename_2026_04_29_final.json",
  );
  assert.equal(preview.sourceInfo.updateKind, SNAPSHOT_SOURCE_UPDATE_KINDS.FULL);
});

test("builds partial controls file source preview with connected api status", () => {
  const preview = resolveDebugSourcePreview(
    "?source_preview=file-long&source_update=partial-controls&trade_date=2026-04-29",
    "2026-04-20",
  );

  assert.equal(preview.tradeDate, "2026-04-29");
  assert.equal(preview.apiStatus, "connected");
  assert.match(preview.notice.detail, /partial controls update/i);
  assert.equal(
    preview.sourceInfo.label,
    "Uploaded review snapshot + API controls update",
  );
  assert.equal(
    preview.sourceInfo.updateKind,
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );
});

test("builds api source preview with explicit trade date", () => {
  const preview = resolveDebugSourcePreview(
    "?source_preview=api&trade_date=2026-04-18",
    "2026-04-20",
  );

  assert.equal(preview.tradeDate, "2026-04-18");
  assert.equal(preview.apiStatus, "connected");
  assert.match(preview.notice.detail, /API preview/i);
  assert.equal(preview.sourceInfo.label, "Live API snapshot (2026-04-18)");
});
