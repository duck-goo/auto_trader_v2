function normalizeAgeSeconds(ageSeconds) {
  if (typeof ageSeconds !== "number" || !Number.isFinite(ageSeconds)) {
    return null;
  }

  if (ageSeconds < 0) {
    return null;
  }

  return Math.floor(ageSeconds);
}

const REVIEW_ITEM_REASON_LABELS = {
  STALE_SIGNAL_AGE_EXCEEDED: "stale age",
  INVALID_SIGNAL_SCANNED_AT: "invalid scanned_at",
  SIGNAL_TIMESTAMP_IN_FUTURE: "future timestamp",
};

const REVIEW_ITEM_SCOPE_LABELS = {
  buy: "buy signal",
  sell: "sell signal",
};

const REVIEW_ITEM_OUTCOME_LABELS = {
  BLOCKED: "blocked",
  PREVIEW_READY: "preview-ready",
  CLEANED: "cleaned",
};

const REVIEW_ITEM_OUTCOME_TONES = {
  BLOCKED: "blocked",
  PREVIEW_READY: "preview-ready",
  CLEANED: "cleaned",
};

export function formatReviewItemAgeLabel(ageSeconds) {
  const normalizedAgeSeconds = normalizeAgeSeconds(ageSeconds);
  if (normalizedAgeSeconds === null) {
    return null;
  }

  const hours = Math.floor(normalizedAgeSeconds / 3600);
  const minutes = Math.floor((normalizedAgeSeconds % 3600) / 60);
  const seconds = normalizedAgeSeconds % 60;
  const parts = [];

  if (hours > 0) {
    parts.push(`${hours}h`);
  }

  if (minutes > 0) {
    parts.push(`${minutes}m`);
  }

  if (seconds > 0 || parts.length === 0) {
    parts.push(`${seconds}s`);
  }

  return `age ${parts.join(" ")}`;
}

export function formatReviewItemReasonLabel(reasonCode) {
  if (typeof reasonCode !== "string") {
    return "-";
  }

  const normalizedReasonCode = reasonCode.trim();
  if (!normalizedReasonCode) {
    return "-";
  }

  return (
    REVIEW_ITEM_REASON_LABELS[normalizedReasonCode] || normalizedReasonCode
  );
}

export function formatReviewItemScopeLabel(scope) {
  if (typeof scope !== "string") {
    return "-";
  }

  const normalizedScope = scope.trim();
  if (!normalizedScope) {
    return "-";
  }

  return REVIEW_ITEM_SCOPE_LABELS[normalizedScope] || normalizedScope;
}

export function formatReviewItemOutcomeLabel(outcome) {
  if (typeof outcome !== "string") {
    return "-";
  }

  const normalizedOutcome = outcome.trim();
  if (!normalizedOutcome) {
    return "-";
  }

  return REVIEW_ITEM_OUTCOME_LABELS[normalizedOutcome] || normalizedOutcome;
}

export function getReviewItemOutcomeTone(outcome) {
  if (typeof outcome !== "string") {
    return "neutral";
  }

  const normalizedOutcome = outcome.trim();
  if (!normalizedOutcome) {
    return "neutral";
  }

  return REVIEW_ITEM_OUTCOME_TONES[normalizedOutcome] || "neutral";
}
