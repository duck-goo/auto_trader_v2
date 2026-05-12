function normalizeCount(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 0;
  }

  if (value <= 0) {
    return 0;
  }

  return Math.floor(value);
}

function buildCountTitle(count, singularLabel, pluralLabel = singularLabel) {
  if (count === 1) {
    return `1 ${singularLabel}`;
  }

  return `${count} ${pluralLabel}`;
}

function buildCountSummaryPart(
  count,
  singularLabel,
  pluralLabel = singularLabel,
) {
  const normalizedCount = normalizeCount(count);
  if (normalizedCount <= 0) {
    return null;
  }

  return buildCountTitle(normalizedCount, singularLabel, pluralLabel);
}

function buildHeaderBadgeTitle({
  totalCount,
  blockedCount,
  previewReadyCount,
  cleanedCount,
}) {
  const summaryParts = [
    buildCountSummaryPart(
      totalCount,
      "stale cleanup review item",
      "stale cleanup review items",
    ),
    buildCountSummaryPart(
      blockedCount,
      "blocked stale cleanup item",
      "blocked stale cleanup items",
    ),
    buildCountSummaryPart(
      previewReadyCount,
      "preview-ready stale cleanup item",
      "preview-ready stale cleanup items",
    ),
    buildCountSummaryPart(
      cleanedCount,
      "cleaned stale cleanup item",
      "cleaned stale cleanup items",
    ),
  ].filter(Boolean);

  if (!summaryParts.length) {
    return null;
  }

  return `${summaryParts.join(". ")}.`;
}

export function buildStaleCleanupHeaderBadges({
  totalCount,
  blockedCount,
  previewReadyCount,
  cleanedCount,
}) {
  const normalizedTotalCount = normalizeCount(totalCount);
  const normalizedBlockedCount = normalizeCount(blockedCount);
  const normalizedPreviewReadyCount = normalizeCount(previewReadyCount);
  const normalizedCleanedCount = normalizeCount(cleanedCount);

  if (normalizedBlockedCount > 0) {
    return [
      {
        key: "blocked-count",
        label: `blocked ${normalizedBlockedCount}`,
        tone: "critical",
        title: buildHeaderBadgeTitle({
          totalCount: normalizedTotalCount,
          blockedCount: normalizedBlockedCount,
          previewReadyCount: normalizedPreviewReadyCount,
          cleanedCount: normalizedCleanedCount,
        }),
      },
    ];
  }

  if (normalizedPreviewReadyCount > 0) {
    return [
      {
        key: "preview-ready-count",
        label: `preview-ready ${normalizedPreviewReadyCount}`,
        tone: "warning",
        title: buildHeaderBadgeTitle({
          totalCount: normalizedTotalCount,
          blockedCount: normalizedBlockedCount,
          previewReadyCount: normalizedPreviewReadyCount,
          cleanedCount: normalizedCleanedCount,
        }),
      },
    ];
  }

  if (normalizedCleanedCount > 0) {
    return [
      {
        key: "cleaned-count",
        label: `cleaned ${normalizedCleanedCount}`,
        tone: "ready",
        title: buildHeaderBadgeTitle({
          totalCount: normalizedTotalCount,
          blockedCount: normalizedBlockedCount,
          previewReadyCount: normalizedPreviewReadyCount,
          cleanedCount: normalizedCleanedCount,
        }),
      },
    ];
  }

  if (normalizedTotalCount > 0) {
    return [
      {
        key: "total-count",
        label: `stale cleanup review ${normalizedTotalCount}`,
        tone: "neutral",
        title: buildHeaderBadgeTitle({
          totalCount: normalizedTotalCount,
          blockedCount: normalizedBlockedCount,
          previewReadyCount: normalizedPreviewReadyCount,
          cleanedCount: normalizedCleanedCount,
        }),
      },
    ];
  }

  return [];
}
