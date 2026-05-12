import test from "node:test";
import assert from "node:assert/strict";

import { formatRecoverySourceLabel } from "./recoverySourceFormatting.js";

test("formats known recovery source labels into friendly labels", () => {
  assert.equal(
    formatRecoverySourceLabel("order_maintenance.preview"),
    "Maintenance Preview"
  );
  assert.equal(
    formatRecoverySourceLabel("order_maintenance.execute"),
    "Maintenance Execute"
  );
  assert.equal(formatRecoverySourceLabel("manual_input"), "Manual Input");
});

test("keeps unknown recovery source labels intact", () => {
  assert.equal(formatRecoverySourceLabel("custom_source"), "custom_source");
});

test("returns fallback label for invalid values", () => {
  assert.equal(formatRecoverySourceLabel(""), "-");
  assert.equal(formatRecoverySourceLabel("   "), "-");
  assert.equal(formatRecoverySourceLabel(null), "-");
});
