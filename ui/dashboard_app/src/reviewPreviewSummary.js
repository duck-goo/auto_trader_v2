function normalizeCount(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 0;
  }

  if (value <= 0) {
    return 0;
  }

  return Math.floor(value);
}

function countShownOutcomes(items) {
  const counts = {
    BLOCKED: 0,
    PREVIEW_READY: 0,
    CLEANED: 0,
  };

  if (!Array.isArray(items)) {
    return counts;
  }

  for (const item of items) {
    if (!item || typeof item !== "object") {
      continue;
    }

    const outcome =
      typeof item.outcome === "string" ? item.outcome.trim() : "";
    if (outcome && Object.prototype.hasOwnProperty.call(counts, outcome)) {
      counts[outcome] += 1;
    }
  }

  return counts;
}

function buildHiddenOutcomeParts(hiddenCounts) {
  const parts = [];

  if (hiddenCounts.BLOCKED > 0) {
    parts.push(`${hiddenCounts.BLOCKED} blocked`);
  }

  if (hiddenCounts.PREVIEW_READY > 0) {
    parts.push(`${hiddenCounts.PREVIEW_READY} preview-ready`);
  }

  if (hiddenCounts.CLEANED > 0) {
    parts.push(`${hiddenCounts.CLEANED} cleaned`);
  }

  return parts;
}

export function buildReviewItemPreviewSummary(items, totalCount, counts = {}) {
  const rows = Array.isArray(items) ? items : [];
  const shownCount = rows.length;
  if (shownCount <= 0) {
    return null;
  }

  const normalizedTotalCount = normalizeCount(totalCount);
  if (normalizedTotalCount <= shownCount) {
    return null;
  }

  const hiddenCount = normalizedTotalCount - shownCount;
  const shownOutcomeCounts = countShownOutcomes(rows);
  const hiddenOutcomeCounts = {
    BLOCKED: Math.max(
      0,
      normalizeCount(counts.blockedCount) - shownOutcomeCounts.BLOCKED,
    ),
    PREVIEW_READY: Math.max(
      0,
      normalizeCount(counts.previewReadyCount) -
        shownOutcomeCounts.PREVIEW_READY,
    ),
    CLEANED: Math.max(
      0,
      normalizeCount(counts.cleanedCount) - shownOutcomeCounts.CLEANED,
    ),
  };
  const hiddenOutcomeParts = buildHiddenOutcomeParts(hiddenOutcomeCounts);
  const titleParts = [
    `Showing ${shownCount} of ${normalizedTotalCount} stale cleanup review items.`,
  ];

  if (hiddenOutcomeParts.length > 0) {
    titleParts.push(`Hidden items by status: ${hiddenOutcomeParts.join(", ")}.`);
  }

  return {
    label: `+${hiddenCount} more`,
    title: titleParts.join(" "),
  };
}
