import test from "node:test";
import assert from "node:assert/strict";

import { SNAPSHOT_SOURCE_MODES } from "./snapshotSourceController.js";
import {
  buildAutoRefreshButtonLabel,
  buildAutoRefreshPrompt,
  buildFullSnapshotReloadSuccessMessage,
  buildLoadFromServerPrompt,
  buildManualSourcePrompt,
  buildReloadFullSnapshotPrompt,
  buildSourceDetailCopyFailureMessage,
  buildSourceDetailCopySuccessMessage,
  buildSourceAttentionNotice,
  buildToolbarSourceCopy,
  shouldEmphasizeLiveToolbar,
  shouldOfferSourceDetailCopy,
  shouldOfferSourceDetailExpansion,
  shouldUsePartialRestoreEmphasis,
  elevateStatusLevelForSourceAttention,
  buildApiSnapshotSourceInfo,
  buildFileSnapshotSourceInfo,
  buildPartialApiUpdateSourceInfo,
  buildSampleSnapshotSourceInfo,
  SNAPSHOT_SOURCE_UPDATE_KINDS,
  shouldOfferFullSnapshotReload,
} from "./snapshotSourceInfo.js";

test("builds sample snapshot source info", () => {
  const info = buildSampleSnapshotSourceInfo();

  assert.equal(info.baseMode, SNAPSHOT_SOURCE_MODES.SAMPLE);
  assert.equal(info.label, "Sample rehearsal snapshot");
  assert.equal(info.updateLabel, "LIVE SNAPSHOT");
  assert.equal(info.isPartial, false);
});

test("builds api snapshot source info with trade date", () => {
  const info = buildApiSnapshotSourceInfo("2026-04-20");

  assert.equal(info.baseMode, SNAPSHOT_SOURCE_MODES.API);
  assert.equal(info.label, "Live API snapshot (2026-04-20)");
  assert.equal(info.modeLabel, "API");
  assert.equal(info.updateKind, SNAPSHOT_SOURCE_UPDATE_KINDS.FULL);
});

test("builds file snapshot source info", () => {
  const info = buildFileSnapshotSourceInfo("ops_snapshot.json");

  assert.equal(info.baseMode, SNAPSHOT_SOURCE_MODES.FILE);
  assert.equal(info.label, "Uploaded review snapshot");
  assert.equal(info.detailLabel, "ops_snapshot.json");
  assert.equal(info.detailDisplayLabel, "ops_snapshot.json");
  assert.equal(info.detailLabelKind, "filename");
  assert.equal(info.modeLabel, "FILE");
});

test("builds default file snapshot source info", () => {
  const info = buildFileSnapshotSourceInfo();

  assert.equal(info.baseMode, SNAPSHOT_SOURCE_MODES.FILE);
  assert.equal(info.label, "Uploaded review snapshot");
  assert.equal(info.detailLabel, "");
  assert.equal(info.detailDisplayLabel, "");
  assert.equal(info.detailLabelKind, "");
  assert.equal(info.modeLabel, "FILE");
});

test("offers source detail copy only for file sources with filename", () => {
  assert.equal(
    shouldOfferSourceDetailCopy(buildFileSnapshotSourceInfo("ops_snapshot.json")),
    true,
  );
  assert.equal(shouldOfferSourceDetailCopy(buildFileSnapshotSourceInfo()), false);
  assert.equal(
    shouldOfferSourceDetailCopy(buildApiSnapshotSourceInfo("2026-04-20")),
    false,
  );
});

test("offers source detail expansion only for truncated file detail", () => {
  assert.equal(
    shouldOfferSourceDetailExpansion(
      buildFileSnapshotSourceInfo(
        "very_long_operational_review_snapshot_filename_2026_04_29_final.json",
      ),
    ),
    true,
  );
  assert.equal(
    shouldOfferSourceDetailExpansion(buildFileSnapshotSourceInfo("ops_snapshot.json")),
    false,
  );
  assert.equal(
    shouldOfferSourceDetailExpansion(buildApiSnapshotSourceInfo("2026-04-20")),
    false,
  );
});

test("builds source detail copy status messages", () => {
  assert.equal(buildSourceDetailCopySuccessMessage(), "Full file name copied.");
  assert.equal(
    buildSourceDetailCopyFailureMessage(),
    "Unable to copy full file name.",
  );
  assert.equal(
    buildSourceDetailCopyFailureMessage(true),
    "Unable to copy full file name. Full name shown below.",
  );
});

test("builds shortened file detail display label for long filename", () => {
  const fileName =
    "very_long_operational_review_snapshot_filename_2026_04_29_final.json";
  const info = buildFileSnapshotSourceInfo(fileName);

  assert.equal(info.detailLabel, fileName);
  assert.equal(
    info.detailDisplayLabel,
    "very_long_operatio...2026_04_29_final.json",
  );
  assert.equal(info.detailLabelKind, "filename");
});

test("builds shortened file detail display label for hyphenated filename", () => {
  const fileName =
    "post-close-review-export-2026-04-29-final-report.json";
  const info = buildFileSnapshotSourceInfo(fileName);

  assert.equal(info.detailLabel, fileName);
  assert.equal(
    info.detailDisplayLabel,
    "post-close-review-...04-29-final-report.json",
  );
  assert.equal(info.detailLabelKind, "filename");
});

test("falls back to raw trailing tail for dense filename", () => {
  const fileName =
    "ultralongoperationalreviewsnapshotfilename20260429final.json";
  const info = buildFileSnapshotSourceInfo(fileName);

  assert.equal(info.detailLabel, fileName);
  assert.equal(
    info.detailDisplayLabel,
    "ultralongoperation...ename20260429final.json",
  );
  assert.equal(info.detailLabelKind, "filename");
});

test("partial controls update preserves base source", () => {
  const baseInfo = buildSampleSnapshotSourceInfo();
  const info = buildPartialApiUpdateSourceInfo(
    baseInfo,
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.equal(info.baseMode, SNAPSHOT_SOURCE_MODES.SAMPLE);
  assert.equal(info.baseLabel, "Sample rehearsal snapshot");
  assert.equal(info.label, "Sample rehearsal snapshot + API controls update");
  assert.equal(info.updateLabel, "CONTROLS ONLY");
  assert.equal(info.isPartial, true);
});

test("partial controls update preserves file detail label", () => {
  const baseInfo = buildFileSnapshotSourceInfo("ops_snapshot.json");
  const info = buildPartialApiUpdateSourceInfo(
    baseInfo,
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.equal(info.baseMode, SNAPSHOT_SOURCE_MODES.FILE);
  assert.equal(info.baseLabel, "Uploaded review snapshot");
  assert.equal(info.label, "Uploaded review snapshot + API controls update");
  assert.equal(info.detailLabel, "ops_snapshot.json");
  assert.equal(info.detailDisplayLabel, "ops_snapshot.json");
  assert.equal(info.detailLabelKind, "filename");
  assert.equal(info.updateLabel, "CONTROLS ONLY");
});

test("partial strategy update does not stack previous suffix into base label", () => {
  const baseInfo = buildApiSnapshotSourceInfo("2026-04-20");
  const partialInfo = buildPartialApiUpdateSourceInfo(
    baseInfo,
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );
  const nextInfo = buildPartialApiUpdateSourceInfo(
    partialInfo,
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY,
  );

  assert.equal(nextInfo.baseLabel, "Live API snapshot (2026-04-20)");
  assert.equal(
    nextInfo.label,
    "Live API snapshot (2026-04-20) + API buy strategy update",
  );
  assert.equal(nextInfo.updateLabel, "STRATEGY ONLY");
});

test("offers full snapshot reload only for partial source with connected api", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.equal(shouldOfferFullSnapshotReload(partialInfo, "connected"), true);
  assert.equal(shouldOfferFullSnapshotReload(partialInfo, "failed"), false);
  assert.equal(
    shouldOfferFullSnapshotReload(buildApiSnapshotSourceInfo("2026-04-20"), "connected"),
    false,
  );
});

test("builds full snapshot reload success message", () => {
  assert.equal(
    buildFullSnapshotReloadSuccessMessage(),
    "Live snapshot restored from API.",
  );
});

test("builds sample attention notice", () => {
  assert.deepEqual(buildSourceAttentionNotice(buildSampleSnapshotSourceInfo()), {
    badgeLabel: "SAMPLE DATA",
    detail: "UI rehearsal only. Load from Server before operational review.",
  });
});

test("builds file attention notice", () => {
  assert.deepEqual(buildSourceAttentionNotice(buildFileSnapshotSourceInfo("ops.json")), {
    badgeLabel: "UPLOADED FILE",
    detail: "Check trade date before relying on this view.",
  });
});

test("does not build source attention notice for api source", () => {
  assert.equal(buildSourceAttentionNotice(buildApiSnapshotSourceInfo("2026-04-20")), null);
});

test("builds load from server prompt for sample source", () => {
  assert.deepEqual(buildLoadFromServerPrompt(buildSampleSnapshotSourceInfo()), {
    buttonLabel: "Load Live API Snapshot",
    detail: "Recommended before operational review.",
  });
});

test("builds load from server prompt for file source", () => {
  assert.deepEqual(buildLoadFromServerPrompt(buildFileSnapshotSourceInfo("ops.json")), {
    buttonLabel: "Replace With Live API Snapshot",
    detail: "Recommended when you need the latest local API view.",
  });
});

test("does not build load from server prompt for api source", () => {
  assert.equal(buildLoadFromServerPrompt(buildApiSnapshotSourceInfo("2026-04-20")), null);
});

test("builds reload full snapshot prompt for partial source", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.deepEqual(buildReloadFullSnapshotPrompt(partialInfo), {
    buttonLabel: "Restore Full Live View",
    detail: "After reviewing Kill Switch, restore the full live API view.",
    reviewLabel: "Review Controls First",
    successMessage: "Live snapshot restored from API.",
  });
});

test("builds reload full snapshot prompt for partial strategy source", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY,
  );

  assert.deepEqual(buildReloadFullSnapshotPrompt(partialInfo), {
    buttonLabel: "Restore Full Live View",
    detail: "After reviewing buy strategy, restore the full live API view.",
    reviewLabel: "Review Strategy First",
    successMessage: "Live snapshot restored from API.",
  });
});

test("does not build reload full snapshot prompt for full source", () => {
  assert.equal(
    buildReloadFullSnapshotPrompt(buildApiSnapshotSourceInfo("2026-04-20")),
    null,
  );
});

test("builds manual source prompt for sample source", () => {
  assert.deepEqual(buildManualSourcePrompt(buildSampleSnapshotSourceInfo()), {
    detail: "Keep sample mode only for rehearsal or UI checks.",
  });
});

test("builds manual source prompt for file source", () => {
  assert.deepEqual(buildManualSourcePrompt(buildFileSnapshotSourceInfo("ops.json")), {
    detail: "Use uploaded files only for offline review.",
  });
});

test("does not build manual source prompt for api source", () => {
  assert.equal(buildManualSourcePrompt(buildApiSnapshotSourceInfo("2026-04-20")), null);
});

test("builds auto refresh prompt for sample source without api connection", () => {
  assert.deepEqual(
    buildAutoRefreshPrompt(buildSampleSnapshotSourceInfo(), "unknown", false),
    {
      detail: "Load a live API snapshot before starting auto refresh.",
    },
  );
});

test("builds auto refresh prompt for file source with api connection", () => {
  assert.deepEqual(
    buildAutoRefreshPrompt(buildFileSnapshotSourceInfo("ops.json"), "connected", false),
    {
      detail: "Auto refresh can restore live monitoring from the next API poll.",
    },
  );
});

test("builds auto refresh prompt for non-api source while polling", () => {
  assert.deepEqual(
    buildAutoRefreshPrompt(buildSampleSnapshotSourceInfo(), "connected", true),
    {
      detail:
        "Polling is running. This view will switch to the live API snapshot after the next successful refresh.",
    },
  );
});

test("does not build auto refresh prompt for api source", () => {
  assert.equal(
    buildAutoRefreshPrompt(buildApiSnapshotSourceInfo("2026-04-20"), "connected", false),
    null,
  );
});

test("builds start live polling label for sample source", () => {
  assert.equal(
    buildAutoRefreshButtonLabel(buildSampleSnapshotSourceInfo(), false),
    "Start Live Polling",
  );
});

test("builds stop live polling label for file source while polling", () => {
  assert.equal(
    buildAutoRefreshButtonLabel(buildFileSnapshotSourceInfo("ops.json"), true),
    "Stop Live Polling",
  );
});

test("keeps auto refresh label for api source", () => {
  assert.equal(
    buildAutoRefreshButtonLabel(buildApiSnapshotSourceInfo("2026-04-20"), false),
    "Start Auto Refresh",
  );
});

test("builds toolbar copy for api source", () => {
  assert.equal(
    buildToolbarSourceCopy(buildApiSnapshotSourceInfo("2026-04-20")),
    "The local dashboard API is active. File upload remains available for offline review.",
  );
});

test("builds toolbar copy for sample source", () => {
  assert.equal(
    buildToolbarSourceCopy(buildSampleSnapshotSourceInfo()),
    "Sample rehearsal snapshot is active. Switch back to the live API snapshot before operational review.",
  );
});

test("builds toolbar copy for file source", () => {
  assert.equal(
    buildToolbarSourceCopy(buildFileSnapshotSourceInfo("ops.json")),
    "Uploaded review snapshot is active. Use it for offline review, then replace it with the live API snapshot.",
  );
});

test("builds toolbar copy for partial source", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.equal(
    buildToolbarSourceCopy(partialInfo),
    "This view includes a partial controls update. Restore Full Live View to return to the live API snapshot.",
  );
});

test("builds toolbar copy for partial strategy source", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY,
  );

  assert.equal(
    buildToolbarSourceCopy(partialInfo),
    "This view includes a partial buy strategy update. Restore Full Live View to return to the live API snapshot.",
  );
});

test("emphasizes live toolbar for sample source", () => {
  assert.equal(shouldEmphasizeLiveToolbar(buildSampleSnapshotSourceInfo()), true);
});

test("emphasizes live toolbar for file source", () => {
  assert.equal(shouldEmphasizeLiveToolbar(buildFileSnapshotSourceInfo("ops.json")), true);
});

test("emphasizes live toolbar for partial source", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.equal(shouldEmphasizeLiveToolbar(partialInfo), true);
});

test("does not emphasize live toolbar for full api source", () => {
  assert.equal(
    shouldEmphasizeLiveToolbar(buildApiSnapshotSourceInfo("2026-04-20")),
    false,
  );
});

test("uses stronger restore emphasis for partial source", () => {
  const partialInfo = buildPartialApiUpdateSourceInfo(
    buildApiSnapshotSourceInfo("2026-04-20"),
    SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  );

  assert.equal(shouldUsePartialRestoreEmphasis(partialInfo), true);
});

test("does not use stronger restore emphasis for sample source", () => {
  assert.equal(
    shouldUsePartialRestoreEmphasis(buildSampleSnapshotSourceInfo()),
    false,
  );
});

test("elevates ready status to warning when source attention exists", () => {
  const attentionNotice = buildSourceAttentionNotice(buildSampleSnapshotSourceInfo());

  assert.equal(
    elevateStatusLevelForSourceAttention("READY", attentionNotice),
    "WARNING",
  );
});

test("keeps critical status when source attention exists", () => {
  const attentionNotice = buildSourceAttentionNotice(buildFileSnapshotSourceInfo("ops.json"));

  assert.equal(
    elevateStatusLevelForSourceAttention("CRITICAL", attentionNotice),
    "CRITICAL",
  );
});

test("keeps ready status when source attention does not exist", () => {
  assert.equal(
    elevateStatusLevelForSourceAttention("READY", null),
    "READY",
  );
});
