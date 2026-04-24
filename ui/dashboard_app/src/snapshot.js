export function asText(value, fallback = "-") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return String(value);
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}

export function normalizeSnapshot(snapshot) {
  const safeSnapshot =
    snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
      ? snapshot
      : {};

  return {
    trade_date: safeSnapshot.trade_date || "-",
    generated_at: safeSnapshot.generated_at || null,
    sources:
      safeSnapshot.sources &&
      typeof safeSnapshot.sources === "object" &&
      !Array.isArray(safeSnapshot.sources)
        ? safeSnapshot.sources
        : {},
    overview:
      safeSnapshot.overview &&
      typeof safeSnapshot.overview === "object" &&
      !Array.isArray(safeSnapshot.overview)
        ? safeSnapshot.overview
        : {},
    controls:
      safeSnapshot.controls &&
      typeof safeSnapshot.controls === "object" &&
      !Array.isArray(safeSnapshot.controls)
        ? safeSnapshot.controls
        : {},
    scan:
      safeSnapshot.scan &&
      typeof safeSnapshot.scan === "object" &&
      !Array.isArray(safeSnapshot.scan)
        ? safeSnapshot.scan
        : {},
    executions:
      safeSnapshot.executions &&
      typeof safeSnapshot.executions === "object" &&
      !Array.isArray(safeSnapshot.executions)
        ? safeSnapshot.executions
        : {},
    recovery:
      safeSnapshot.recovery &&
      typeof safeSnapshot.recovery === "object" &&
      !Array.isArray(safeSnapshot.recovery)
        ? safeSnapshot.recovery
        : {},
    rehearsal:
      safeSnapshot.rehearsal &&
      typeof safeSnapshot.rehearsal === "object" &&
      !Array.isArray(safeSnapshot.rehearsal)
        ? safeSnapshot.rehearsal
        : {},
    actions:
      safeSnapshot.actions &&
      typeof safeSnapshot.actions === "object" &&
      !Array.isArray(safeSnapshot.actions)
        ? safeSnapshot.actions
        : {},
  };
}

export function statusClassName(level) {
  switch (level) {
    case "READY":
      return "status-ready";
    case "WARNING":
      return "status-warning";
    case "CRITICAL":
      return "status-critical";
    case "FAILED":
      return "status-failed";
    case "NO_DATA":
      return "status-no-data";
    default:
      return "status-missing";
  }
}
