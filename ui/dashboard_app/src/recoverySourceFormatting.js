const RECOVERY_SOURCE_LABELS = {
  "order_maintenance.preview": "Maintenance Preview",
  "order_maintenance.execute": "Maintenance Execute",
  manual_input: "Manual Input",
};

export function formatRecoverySourceLabel(sourceLabel) {
  if (typeof sourceLabel !== "string") {
    return "-";
  }

  const normalizedSourceLabel = sourceLabel.trim();
  if (!normalizedSourceLabel) {
    return "-";
  }

  return RECOVERY_SOURCE_LABELS[normalizedSourceLabel] || normalizedSourceLabel;
}
