import {
  buildApiSnapshotSourceInfo,
  buildFileSnapshotSourceInfo,
  buildPartialApiUpdateSourceInfo,
  buildSampleSnapshotSourceInfo,
  SNAPSHOT_SOURCE_UPDATE_KINDS,
} from "./snapshotSourceInfo.js";

const DEFAULT_DEBUG_FILE_NAME = "uploaded_review_snapshot.json";
const DEFAULT_DEBUG_LONG_FILE_NAME =
  "very_long_operational_review_snapshot_filename_2026_04_29_final.json";

export function resolveInitialTradeDate(search, fallbackTradeDate) {
  const params = new URLSearchParams(search || "");
  return params.get("trade_date") || fallbackTradeDate || "-";
}

export function buildPreviewExitUrl(pathname, tradeDate) {
  const safePathname = pathname || "/";
  if (!tradeDate) {
    return safePathname;
  }

  const params = new URLSearchParams();
  params.set("trade_date", tradeDate);
  return `${safePathname}?${params.toString()}`;
}

function resolvePreviewUpdateKind(rawValue) {
  switch (rawValue) {
    case "partial-controls":
      return SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS;
    case "partial-strategy":
      return SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY;
    default:
      return SNAPSHOT_SOURCE_UPDATE_KINDS.FULL;
  }
}

function buildPreviewNotice(previewMode, updateKind) {
  const sourceLabel =
    previewMode === "file-long"
      ? "long uploaded file preview"
      : previewMode === "file"
        ? "uploaded file preview"
        : previewMode === "sample"
          ? "sample preview"
          : "API preview";

  const updateLabel =
    updateKind === SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS
      ? "partial controls update"
      : updateKind === SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY
        ? "partial buy strategy update"
        : "full source state";

  return {
    detail: `Preview mode is active for ${sourceLabel} with ${updateLabel}. Exit Preview Mode before normal review.`,
    title: "Preview Mode Active",
  };
}

export function resolveDebugSourcePreview(search, defaultTradeDate) {
  const params = new URLSearchParams(search || "");
  const previewMode = params.get("source_preview");
  if (!previewMode) {
    return null;
  }

  const tradeDate = resolveInitialTradeDate(search, defaultTradeDate);
  const requestedFileName = params.get("source_filename") || "";
  const updateKind = resolvePreviewUpdateKind(params.get("source_update"));

  let baseSourceInfo = null;
  switch (previewMode) {
    case "sample":
      baseSourceInfo = buildSampleSnapshotSourceInfo();
      break;
    case "api":
      baseSourceInfo = buildApiSnapshotSourceInfo(tradeDate);
      break;
    case "file":
      baseSourceInfo = buildFileSnapshotSourceInfo(
        requestedFileName || DEFAULT_DEBUG_FILE_NAME,
      );
      break;
    case "file-long":
      baseSourceInfo = buildFileSnapshotSourceInfo(
        requestedFileName || DEFAULT_DEBUG_LONG_FILE_NAME,
      );
      break;
    default:
      return null;
  }

  return {
    apiStatus:
      updateKind === SNAPSHOT_SOURCE_UPDATE_KINDS.FULL &&
      previewMode !== "api"
        ? "unknown"
        : "connected",
    notice: buildPreviewNotice(previewMode, updateKind),
    suppressConnectedBanner: true,
    skipInitialApiLoad: true,
    sourceInfo:
      updateKind === SNAPSHOT_SOURCE_UPDATE_KINDS.FULL
        ? baseSourceInfo
        : buildPartialApiUpdateSourceInfo(baseSourceInfo, updateKind),
    tradeDate,
  };
}
