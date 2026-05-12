import test from "node:test";
import assert from "node:assert/strict";

import { SNAPSHOT_SOURCE_UPDATE_KINDS } from "./snapshotSourceInfo.js";
import {
  buildSourceInfoJumpTarget,
  buildOperatorJumpTargets,
  OPERATOR_TARGET_GROUPS,
  DASHBOARD_SECTION_TARGETS,
} from "./operatorSummaryTargets.js";

function projectTargets(targets) {
  return targets.map((target) => ({
    sectionId: target.sectionId,
    focusId: target.focusId,
    label: target.label,
    group: target.group,
  }));
}

test("maps startup mismatch to startup gate and action items", () => {
  const targets = buildOperatorJumpTargets({
    startup_open_entry_lot_position_mismatch: true,
    primary_attention_flag: "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
    primary_action_code: "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.startup,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps sell execute failure flag to sell execute card", () => {
  const targets = buildOperatorJumpTargets({
    primary_attention_flag: "EXECUTE_SELL_SIGNALS_EXECUTE_FAILED",
    primary_action_code: "REVIEW_SELL_EXECUTION_FAILURE",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.executionSellExecute,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.executionSellPreview,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps generic sell execution review to sell preview card", () => {
  const targets = buildOperatorJumpTargets({
    primary_attention_flag: "",
    primary_action_code: "REVIEW_SELL_EXECUTION_FAILURE",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.executionSellPreview,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps trading session setup rerun to trading preview card", () => {
  const targets = buildOperatorJumpTargets({
    primary_attention_flag: "TRADING_SESSION_PREVIEW_TIMING2_SETUP_NOT_READY",
    primary_action_code: "RERUN_TRADING_SESSION_WITH_TIMING2_SETUP",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.scanPreview,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps manual recovery review to recovery review card", () => {
  const targets = buildOperatorJumpTargets({
    primary_attention_flag: "MANUAL_RECOVERY_REQUIRED",
    primary_action_code: "REVIEW_EXECUTION_RECOVERY",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.recoveryReview,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps stale cleanup review action to stale cleanup review card", () => {
  const targets = buildOperatorJumpTargets({
    primary_attention_flag: "STALE_SIGNAL_CLEANUP_BLOCKED_ITEMS",
    primary_action_code: "REVIEW_STALE_SIGNAL_CLEANUP",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.recoveryStaleSignalCleanupReview,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps kill switch review to controls", () => {
  const targets = buildOperatorJumpTargets({
    primary_attention_flag: "KILL_SWITCH_ENABLED",
    primary_action_code: "REVIEW_KILL_SWITCH",
  });

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.controls,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("adds secondary targets from related action codes", () => {
  const targets = buildOperatorJumpTargets(
    {
      startup_open_entry_lot_position_mismatch: true,
      primary_attention_flag: "STARTUP_OPEN_ENTRY_LOT_POSITION_MISMATCH",
      primary_action_code: "REVIEW_OPEN_ENTRY_LOT_RECONCILE_BLOCK",
    },
    {
      relatedActionCodes: [
        "REVIEW_KILL_SWITCH",
        "REVIEW_SELL_EXECUTION_FAILURE",
        "REVIEW_EXECUTION_RECOVERY",
      ],
    },
  );

  assert.deepEqual(projectTargets(targets), [
    {
      ...DASHBOARD_SECTION_TARGETS.startup,
      group: OPERATOR_TARGET_GROUPS.PRIMARY,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.controls,
      group: OPERATOR_TARGET_GROUPS.RELATED,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.executionSellPreview,
      group: OPERATOR_TARGET_GROUPS.RELATED,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.recoveryReview,
      group: OPERATOR_TARGET_GROUPS.RELATED,
    },
    {
      ...DASHBOARD_SECTION_TARGETS.actions,
      group: OPERATOR_TARGET_GROUPS.FALLBACK,
    },
  ]);
});

test("maps partial controls source info to controls jump target", () => {
  const target = buildSourceInfoJumpTarget({
    updateKind: SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
  });

  assert.deepEqual(target, DASHBOARD_SECTION_TARGETS.controls);
});

test("maps partial strategy source info to strategy jump target", () => {
  const target = buildSourceInfoJumpTarget({
    updateKind: SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY,
  });

  assert.deepEqual(target, DASHBOARD_SECTION_TARGETS.strategy);
});
