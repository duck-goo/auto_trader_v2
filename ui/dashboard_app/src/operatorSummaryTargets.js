import { SNAPSHOT_SOURCE_UPDATE_KINDS } from "./snapshotSourceInfo.js";

export const DASHBOARD_CARD_KEYS = {
  startupCheck: "startup-check",
  scanLivePreview: "scan-live-preview",
  scanLiveExecute: "scan-live-execute",
  scanRehearsalValidation: "scan-rehearsal-validation",
  executionBuyPreview: "execution-buy-preview",
  executionBuyExecute: "execution-buy-execute",
  executionSellPreview: "execution-sell-preview",
  executionSellExecute: "execution-sell-execute",
  recoveryMaintenancePreview: "recovery-maintenance-preview",
  recoveryMaintenanceExecute: "recovery-maintenance-execute",
  recoveryStaleSignalCleanupReview: "recovery-stale-signal-review",
  recoveryExecutionReview: "recovery-execution-review",
};

const DASHBOARD_CARD_ID_PREFIX = "dashboard-card-";

export function dashboardCardElementId(cardKey) {
  if (!cardKey) {
    return undefined;
  }
  return `${DASHBOARD_CARD_ID_PREFIX}${String(cardKey)}`;
}

function createTarget({ sectionId, label, cardKey, focusId }) {
  return {
    sectionId,
    label,
    focusId: focusId || dashboardCardElementId(cardKey),
  };
}

export const DASHBOARD_SECTION_TARGETS = {
  startup: createTarget({
    sectionId: "startup-safety-gate",
    label: "Startup Gate",
    cardKey: DASHBOARD_CARD_KEYS.startupCheck,
  }),
  strategy: createTarget({
    sectionId: "buy-strategy-selection",
    label: "Strategy",
    focusId: "buy-strategy-select",
  }),
  controls: createTarget({
    sectionId: "emergency-controls",
    label: "Controls",
    focusId: "kill-switch-note-input",
  }),
  scanPreview: createTarget({
    sectionId: "scan-and-session-state",
    label: "Trading Preview",
    cardKey: DASHBOARD_CARD_KEYS.scanLivePreview,
  }),
  scanExecute: createTarget({
    sectionId: "scan-and-session-state",
    label: "Trading Execute",
    cardKey: DASHBOARD_CARD_KEYS.scanLiveExecute,
  }),
  executionBuyPreview: createTarget({
    sectionId: "direct-execution-state",
    label: "Buy Preview",
    cardKey: DASHBOARD_CARD_KEYS.executionBuyPreview,
  }),
  executionBuyExecute: createTarget({
    sectionId: "direct-execution-state",
    label: "Buy Execute",
    cardKey: DASHBOARD_CARD_KEYS.executionBuyExecute,
  }),
  executionSellPreview: createTarget({
    sectionId: "direct-execution-state",
    label: "Sell Preview",
    cardKey: DASHBOARD_CARD_KEYS.executionSellPreview,
  }),
  executionSellExecute: createTarget({
    sectionId: "direct-execution-state",
    label: "Sell Execute",
    cardKey: DASHBOARD_CARD_KEYS.executionSellExecute,
  }),
  recoveryMaintenancePreview: createTarget({
    sectionId: "recovery-and-maintenance",
    label: "Maintenance Preview",
    cardKey: DASHBOARD_CARD_KEYS.recoveryMaintenancePreview,
  }),
  recoveryMaintenanceExecute: createTarget({
    sectionId: "recovery-and-maintenance",
    label: "Maintenance Execute",
    cardKey: DASHBOARD_CARD_KEYS.recoveryMaintenanceExecute,
  }),
  recoveryStaleSignalCleanupReview: createTarget({
    sectionId: "recovery-and-maintenance",
    label: "Stale Cleanup Review",
    cardKey: DASHBOARD_CARD_KEYS.recoveryStaleSignalCleanupReview,
  }),
  recoveryReview: createTarget({
    sectionId: "recovery-and-maintenance",
    label: "Manual Recovery Review",
    cardKey: DASHBOARD_CARD_KEYS.recoveryExecutionReview,
  }),
  actions: createTarget({
    sectionId: "immediate-action-items",
    label: "Action Items",
    focusId: "first-action-item-card",
  }),
};

export const OPERATOR_TARGET_GROUPS = {
  PRIMARY: "primary",
  RELATED: "related",
  FALLBACK: "fallback",
};

export function buildSourceInfoJumpTarget(sourceInfo) {
  switch (sourceInfo?.updateKind) {
    case SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS:
      return DASHBOARD_SECTION_TARGETS.controls;
    case SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY:
      return DASHBOARD_SECTION_TARGETS.strategy;
    default:
      return null;
  }
}

function includesToken(value, token) {
  return String(value || "").includes(token);
}

function resolveTradingSessionTarget(primaryFlag, primaryActionCode) {
  if (includesToken(primaryFlag, "TRADING_SESSION_EXECUTE")) {
    return DASHBOARD_SECTION_TARGETS.scanExecute;
  }
  if (includesToken(primaryFlag, "TRADING_SESSION_PREVIEW")) {
    return DASHBOARD_SECTION_TARGETS.scanPreview;
  }
  if (
    primaryActionCode === "RERUN_TRADING_SESSION_WITH_TIMING2_SETUP" ||
    primaryActionCode === "REVIEW_TRADING_SESSION_BLOCK" ||
    primaryActionCode === "REVIEW_TRADING_SESSION_FAILURE"
  ) {
    return DASHBOARD_SECTION_TARGETS.scanPreview;
  }
  return null;
}

function resolveExecutionTarget(primaryFlag, primaryActionCode) {
  if (includesToken(primaryFlag, "EXECUTE_BUY_SIGNALS_EXECUTE")) {
    return DASHBOARD_SECTION_TARGETS.executionBuyExecute;
  }
  if (includesToken(primaryFlag, "EXECUTE_BUY_SIGNALS_PREVIEW")) {
    return DASHBOARD_SECTION_TARGETS.executionBuyPreview;
  }
  if (includesToken(primaryFlag, "EXECUTE_SELL_SIGNALS_EXECUTE")) {
    return DASHBOARD_SECTION_TARGETS.executionSellExecute;
  }
  if (includesToken(primaryFlag, "EXECUTE_SELL_SIGNALS_PREVIEW")) {
    return DASHBOARD_SECTION_TARGETS.executionSellPreview;
  }
  if (
    primaryActionCode === "REVIEW_BUY_EXECUTION_BLOCK" ||
    primaryActionCode === "REVIEW_BUY_EXECUTION_FAILURE"
  ) {
    return DASHBOARD_SECTION_TARGETS.executionBuyPreview;
  }
  if (
    primaryActionCode === "REVIEW_SELL_EXECUTION_BLOCK" ||
    primaryActionCode === "REVIEW_SELL_EXECUTION_FAILURE"
  ) {
    return DASHBOARD_SECTION_TARGETS.executionSellPreview;
  }
  return null;
}

function resolveRecoveryTarget(primaryFlag, primaryActionCode) {
  if (
    primaryFlag === "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS" ||
    primaryActionCode === "REVIEW_STALE_SIGNAL_CLEANUP"
  ) {
    return DASHBOARD_SECTION_TARGETS.recoveryStaleSignalCleanupReview;
  }
  if (includesToken(primaryFlag, "ORDER_MAINTENANCE_EXECUTE")) {
    return DASHBOARD_SECTION_TARGETS.recoveryMaintenanceExecute;
  }
  if (
    includesToken(primaryFlag, "ORDER_MAINTENANCE_PREVIEW") ||
    primaryActionCode === "RERUN_ORDER_MAINTENANCE_PREVIEW"
  ) {
    return DASHBOARD_SECTION_TARGETS.recoveryMaintenancePreview;
  }
  if (
    primaryFlag === "MANUAL_RECOVERY_REQUIRED" ||
    includesToken(primaryFlag, "EXECUTION_RECOVERY_REVIEW") ||
    primaryActionCode === "REVIEW_EXECUTION_RECOVERY"
  ) {
    return DASHBOARD_SECTION_TARGETS.recoveryReview;
  }
  return null;
}

function collectSignalTokens(primaryValue, relatedValues) {
  const rows = [];
  const seen = new Set();
  const values = [primaryValue, ...(Array.isArray(relatedValues) ? relatedValues : [])];
  for (const value of values) {
    const text = String(value || "");
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    rows.push(text);
  }
  return rows;
}

export function buildOperatorJumpTargets(summary, options = {}) {
  const primaryFlag = String(summary?.primary_attention_flag || "");
  const primaryActionCode = String(summary?.primary_action_code || "");
  const primaryFlags = collectSignalTokens(primaryFlag, []);
  const primaryActionCodes = collectSignalTokens(primaryActionCode, []);
  const relatedFlags = collectSignalTokens("", options.relatedAttentionFlags || []);
  const relatedActionCodes = collectSignalTokens("", options.relatedActionCodes || []);
  const targets = [];
  const seen = new Set();

  function addTarget(target, group) {
    if (!target) {
      return;
    }
    const dedupeKey = `${target.sectionId}:${target.focusId || ""}`;
    if (seen.has(dedupeKey)) {
      return;
    }
    seen.add(dedupeKey);
    targets.push({
      ...target,
      group,
    });
  }

  if (
    summary?.startup_open_entry_lot_position_mismatch === true ||
    primaryFlag.startsWith("STARTUP_") ||
    primaryActionCode === "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK" ||
    primaryActionCode === "REVIEW_STARTUP_CHECK"
  ) {
    addTarget(DASHBOARD_SECTION_TARGETS.startup, OPERATOR_TARGET_GROUPS.PRIMARY);
  }

  for (const flag of primaryFlags) {
    if (includesToken(flag, "KILL_SWITCH")) {
      addTarget(DASHBOARD_SECTION_TARGETS.controls, OPERATOR_TARGET_GROUPS.PRIMARY);
    }
    addTarget(
      resolveTradingSessionTarget(flag, ""),
      OPERATOR_TARGET_GROUPS.PRIMARY,
    );
    addTarget(resolveExecutionTarget(flag, ""), OPERATOR_TARGET_GROUPS.PRIMARY);
    addTarget(resolveRecoveryTarget(flag, ""), OPERATOR_TARGET_GROUPS.PRIMARY);
  }

  for (const actionCode of primaryActionCodes) {
    if (includesToken(actionCode, "KILL_SWITCH")) {
      addTarget(DASHBOARD_SECTION_TARGETS.controls, OPERATOR_TARGET_GROUPS.PRIMARY);
    }
    addTarget(
      resolveTradingSessionTarget("", actionCode),
      OPERATOR_TARGET_GROUPS.PRIMARY,
    );
    addTarget(
      resolveExecutionTarget("", actionCode),
      OPERATOR_TARGET_GROUPS.PRIMARY,
    );
    addTarget(resolveRecoveryTarget("", actionCode), OPERATOR_TARGET_GROUPS.PRIMARY);
  }

  for (const flag of relatedFlags) {
    if (includesToken(flag, "KILL_SWITCH")) {
      addTarget(DASHBOARD_SECTION_TARGETS.controls, OPERATOR_TARGET_GROUPS.RELATED);
    }
    addTarget(
      resolveTradingSessionTarget(flag, ""),
      OPERATOR_TARGET_GROUPS.RELATED,
    );
    addTarget(resolveExecutionTarget(flag, ""), OPERATOR_TARGET_GROUPS.RELATED);
    addTarget(resolveRecoveryTarget(flag, ""), OPERATOR_TARGET_GROUPS.RELATED);
  }

  for (const actionCode of relatedActionCodes) {
    if (includesToken(actionCode, "KILL_SWITCH")) {
      addTarget(DASHBOARD_SECTION_TARGETS.controls, OPERATOR_TARGET_GROUPS.RELATED);
    }
    addTarget(
      resolveTradingSessionTarget("", actionCode),
      OPERATOR_TARGET_GROUPS.RELATED,
    );
    addTarget(
      resolveExecutionTarget("", actionCode),
      OPERATOR_TARGET_GROUPS.RELATED,
    );
    addTarget(resolveRecoveryTarget("", actionCode), OPERATOR_TARGET_GROUPS.RELATED);
  }

  if (
    primaryActionCodes.length ||
    primaryFlags.length ||
    relatedActionCodes.length ||
    relatedFlags.length
  ) {
    addTarget(DASHBOARD_SECTION_TARGETS.actions, OPERATOR_TARGET_GROUPS.FALLBACK);
  }

  return targets;
}
