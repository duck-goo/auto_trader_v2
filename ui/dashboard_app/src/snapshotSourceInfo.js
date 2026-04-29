import { SNAPSHOT_SOURCE_MODES } from "./snapshotSourceController.js";

export const SNAPSHOT_SOURCE_UPDATE_KINDS = {
  FULL: "full",
  PARTIAL_CONTROLS: "partial-controls",
  PARTIAL_STRATEGY: "partial-strategy",
};

const MAX_SOURCE_DETAIL_DISPLAY_LENGTH = 44;
const SOURCE_DETAIL_FILENAME_PREFIX_LENGTH = 18;

function buildFilenameSuffixDisplayLabel(fileName, maxLength) {
  const safeFileName = fileName || "";
  if (!safeFileName || safeFileName.length <= maxLength) {
    return safeFileName;
  }

  const extensionIndex = safeFileName.lastIndexOf(".");
  const hasExtension =
    extensionIndex > 0 && extensionIndex < safeFileName.length - 1;
  const extension = hasExtension ? safeFileName.slice(extensionIndex) : "";
  const stem = hasExtension
    ? safeFileName.slice(0, extensionIndex)
    : safeFileName;
  const tokens = stem.split(/([_\-\s]+)/).filter(Boolean);

  let suffix = extension;
  for (let index = tokens.length - 1; index >= 0; index -= 1) {
    const candidate = `${tokens[index]}${suffix}`;
    if (candidate.length > maxLength) {
      break;
    }
    suffix = candidate;
  }

  const normalizedSuffix = suffix.replace(/^[_\-\s]+/, "");
  if (!normalizedSuffix || normalizedSuffix === extension) {
    return safeFileName.slice(-maxLength);
  }
  return normalizedSuffix;
}

function buildDetailDisplayLabel(detailLabel, detailLabelKind) {
  const safeDetailLabel = detailLabel || "";
  if (!safeDetailLabel) {
    return "";
  }

  if (
    detailLabelKind !== "filename" ||
    safeDetailLabel.length <= MAX_SOURCE_DETAIL_DISPLAY_LENGTH
  ) {
    return safeDetailLabel;
  }

  const suffixLength =
    MAX_SOURCE_DETAIL_DISPLAY_LENGTH -
    SOURCE_DETAIL_FILENAME_PREFIX_LENGTH -
    3;
  const suffixDisplayLabel = buildFilenameSuffixDisplayLabel(
    safeDetailLabel,
    suffixLength,
  );
  return `${safeDetailLabel.slice(0, SOURCE_DETAIL_FILENAME_PREFIX_LENGTH)}...${suffixDisplayLabel}`;
}

function partialUpdateDetail(updateKind) {
  switch (updateKind) {
    case SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS:
      return {
        toolbarCopy:
          "This view includes a partial controls update. Restore Full Live View to return to the live API snapshot.",
        reloadDetail:
          "After reviewing Kill Switch, restore the full live API view.",
        reviewLabel: "Review Controls First",
      };
    case SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY:
      return {
        toolbarCopy:
          "This view includes a partial buy strategy update. Restore Full Live View to return to the live API snapshot.",
        reloadDetail:
          "After reviewing buy strategy, restore the full live API view.",
        reviewLabel: "Review Strategy First",
      };
    case SNAPSHOT_SOURCE_UPDATE_KINDS.FULL:
    default:
      return {
        toolbarCopy:
          "This view includes partial updates. Restore Full Live View to return to the live API snapshot.",
        reloadDetail:
          "After reviewing the affected section, restore the full live API view.",
        reviewLabel: "Review Partial Update First",
      };
  }
}

function modeLabel(mode) {
  switch (mode) {
    case SNAPSHOT_SOURCE_MODES.API:
      return "API";
    case SNAPSHOT_SOURCE_MODES.FILE:
      return "FILE";
    case SNAPSHOT_SOURCE_MODES.SAMPLE:
    default:
      return "SAMPLE";
  }
}

function updateLabel(updateKind) {
  switch (updateKind) {
    case SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS:
      return "CONTROLS ONLY";
    case SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY:
      return "STRATEGY ONLY";
    case SNAPSHOT_SOURCE_UPDATE_KINDS.FULL:
    default:
      return "LIVE SNAPSHOT";
  }
}

function buildSourceInfo({
  baseMode,
  baseLabel,
  displayLabel,
  detailLabel,
  detailLabelKind,
  updateKind,
}) {
  return {
    baseMode,
    baseLabel,
    label: displayLabel,
    detailLabel: detailLabel || "",
    detailLabelKind: detailLabelKind || "",
    detailDisplayLabel: buildDetailDisplayLabel(
      detailLabel || "",
      detailLabelKind || "",
    ),
    updateKind,
    modeLabel: modeLabel(baseMode),
    updateLabel: updateLabel(updateKind),
    isPartial: updateKind !== SNAPSHOT_SOURCE_UPDATE_KINDS.FULL,
  };
}

export function buildSampleSnapshotSourceInfo() {
  return buildSourceInfo({
    baseMode: SNAPSHOT_SOURCE_MODES.SAMPLE,
    baseLabel: "Sample rehearsal snapshot",
    displayLabel: "Sample rehearsal snapshot",
    updateKind: SNAPSHOT_SOURCE_UPDATE_KINDS.FULL,
  });
}

export function buildFileSnapshotSourceInfo(fileName) {
  const baseLabel = "Uploaded review snapshot";
  return buildSourceInfo({
    baseMode: SNAPSHOT_SOURCE_MODES.FILE,
    baseLabel,
    displayLabel: baseLabel,
    detailLabel: fileName || "",
    detailLabelKind: fileName ? "filename" : "",
    updateKind: SNAPSHOT_SOURCE_UPDATE_KINDS.FULL,
  });
}

export function buildApiSnapshotSourceInfo(tradeDate) {
  const safeTradeDate = tradeDate || "-";
  const baseLabel = `Live API snapshot (${safeTradeDate})`;
  return buildSourceInfo({
    baseMode: SNAPSHOT_SOURCE_MODES.API,
    baseLabel,
    displayLabel: baseLabel,
    updateKind: SNAPSHOT_SOURCE_UPDATE_KINDS.FULL,
  });
}

export function buildPartialApiUpdateSourceInfo(currentInfo, partialUpdateKind) {
  const baseMode =
    currentInfo?.baseMode || SNAPSHOT_SOURCE_MODES.API;
  const baseLabel =
    currentInfo?.baseLabel || currentInfo?.label || "Dashboard snapshot";
  const detailLabel = currentInfo?.detailLabel || "";
  const detailLabelKind = currentInfo?.detailLabelKind || "";
  const suffix =
    partialUpdateKind === SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS
      ? "API controls update"
      : "API buy strategy update";

  return buildSourceInfo({
    baseMode,
    baseLabel,
    displayLabel: `${baseLabel} + ${suffix}`,
    detailLabel,
    detailLabelKind,
    updateKind: partialUpdateKind,
  });
}

export function shouldOfferFullSnapshotReload(sourceInfo, apiStatus) {
  return sourceInfo?.isPartial === true && apiStatus === "connected";
}

export function buildFullSnapshotReloadSuccessMessage() {
  return "Live snapshot restored from API.";
}

export function shouldOfferSourceDetailCopy(sourceInfo) {
  return (
    sourceInfo?.detailLabelKind === "filename" &&
    Boolean(sourceInfo?.detailLabel)
  );
}

export function shouldOfferSourceDetailExpansion(sourceInfo) {
  return (
    sourceInfo?.detailLabelKind === "filename" &&
    Boolean(sourceInfo?.detailLabel) &&
    sourceInfo?.detailDisplayLabel !== sourceInfo?.detailLabel
  );
}

export function buildSourceDetailCopySuccessMessage() {
  return "Full file name copied.";
}

export function buildSourceDetailCopyFailureMessage(showingExpandedFallback = false) {
  if (showingExpandedFallback) {
    return "Unable to copy full file name. Full name shown below.";
  }
  return "Unable to copy full file name.";
}

export function buildSourceAttentionNotice(sourceInfo) {
  switch (sourceInfo?.baseMode) {
    case SNAPSHOT_SOURCE_MODES.SAMPLE:
      return {
        badgeLabel: "SAMPLE DATA",
        detail: "UI rehearsal only. Load from Server before operational review.",
      };
    case SNAPSHOT_SOURCE_MODES.FILE:
      return {
        badgeLabel: "UPLOADED FILE",
        detail: "Check trade date before relying on this view.",
      };
    default:
      return null;
  }
}

export function buildLoadFromServerPrompt(sourceInfo) {
  switch (sourceInfo?.baseMode) {
    case SNAPSHOT_SOURCE_MODES.SAMPLE:
      return {
        buttonLabel: "Load Live API Snapshot",
        detail: "Recommended before operational review.",
      };
    case SNAPSHOT_SOURCE_MODES.FILE:
      return {
        buttonLabel: "Replace With Live API Snapshot",
        detail: "Recommended when you need the latest local API view.",
      };
    default:
      return null;
  }
}

export function buildReloadFullSnapshotPrompt(sourceInfo) {
  if (sourceInfo?.isPartial !== true) {
    return null;
  }

  const detail = partialUpdateDetail(sourceInfo.updateKind);
  return {
    buttonLabel: "Restore Full Live View",
    detail: detail.reloadDetail,
    reviewLabel: detail.reviewLabel,
    successMessage: buildFullSnapshotReloadSuccessMessage(),
  };
}

export function buildManualSourcePrompt(sourceInfo) {
  switch (sourceInfo?.baseMode) {
    case SNAPSHOT_SOURCE_MODES.SAMPLE:
      return {
        detail: "Keep sample mode only for rehearsal or UI checks.",
      };
    case SNAPSHOT_SOURCE_MODES.FILE:
      return {
        detail: "Use uploaded files only for offline review.",
      };
    default:
      return null;
  }
}

export function buildAutoRefreshPrompt(sourceInfo, apiStatus, autoRefreshEnabled) {
  if (sourceInfo?.baseMode === SNAPSHOT_SOURCE_MODES.API) {
    return null;
  }

  if (autoRefreshEnabled) {
    return {
      detail:
        "Polling is running. This view will switch to the live API snapshot after the next successful refresh.",
    };
  }

  if (apiStatus === "connected") {
    return {
      detail: "Auto refresh can restore live monitoring from the next API poll.",
    };
  }

  return {
    detail: "Load a live API snapshot before starting auto refresh.",
  };
}

export function buildAutoRefreshButtonLabel(sourceInfo, autoRefreshEnabled) {
  if (sourceInfo?.baseMode === SNAPSHOT_SOURCE_MODES.API) {
    return autoRefreshEnabled ? "Stop Auto Refresh" : "Start Auto Refresh";
  }

  return autoRefreshEnabled ? "Stop Live Polling" : "Start Live Polling";
}

export function buildToolbarSourceCopy(sourceInfo) {
  if (sourceInfo?.isPartial === true) {
    return partialUpdateDetail(sourceInfo.updateKind).toolbarCopy;
  }

  switch (sourceInfo?.baseMode) {
    case SNAPSHOT_SOURCE_MODES.SAMPLE:
      return "Sample rehearsal snapshot is active. Switch back to the live API snapshot before operational review.";
    case SNAPSHOT_SOURCE_MODES.FILE:
      return "Uploaded review snapshot is active. Use it for offline review, then replace it with the live API snapshot.";
    case SNAPSHOT_SOURCE_MODES.API:
    default:
      return "The local dashboard API is active. File upload remains available for offline review.";
  }
}

export function shouldEmphasizeLiveToolbar(sourceInfo) {
  if (sourceInfo?.isPartial === true) {
    return true;
  }

  return sourceInfo?.baseMode === SNAPSHOT_SOURCE_MODES.SAMPLE ||
    sourceInfo?.baseMode === SNAPSHOT_SOURCE_MODES.FILE;
}

export function shouldUsePartialRestoreEmphasis(sourceInfo) {
  return sourceInfo?.isPartial === true;
}

export function elevateStatusLevelForSourceAttention(statusLevel, attentionNotice) {
  const safeStatusLevel = statusLevel || "NO_DATA";
  if (!attentionNotice) {
    return safeStatusLevel;
  }

  switch (safeStatusLevel) {
    case "CRITICAL":
    case "FAILED":
    case "WARNING":
      return safeStatusLevel;
    default:
      return "WARNING";
  }
}
