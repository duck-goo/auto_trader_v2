import test from "node:test";
import assert from "node:assert/strict";

import {
  createSnapshotSourceController,
  SNAPSHOT_SOURCE_MODES,
} from "./snapshotSourceController.js";

test("stale server result is ignored after sample selection", () => {
  const controller = createSnapshotSourceController(SNAPSHOT_SOURCE_MODES.SAMPLE);

  const ticket = controller.beginServerRequest();
  controller.markManualSource(SNAPSHOT_SOURCE_MODES.SAMPLE);

  assert.equal(controller.tryCommitServerResult(ticket), false);
  assert.equal(controller.getMode(), SNAPSHOT_SOURCE_MODES.SAMPLE);
});

test("successful server result commits api mode when revision still matches", () => {
  const controller = createSnapshotSourceController(SNAPSHOT_SOURCE_MODES.SAMPLE);

  const ticket = controller.beginServerRequest();

  assert.equal(controller.tryCommitServerResult(ticket), true);
  assert.equal(controller.getMode(), SNAPSHOT_SOURCE_MODES.API);
});

test("api mutation invalidates older in-flight server result", () => {
  const controller = createSnapshotSourceController(SNAPSHOT_SOURCE_MODES.SAMPLE);

  const ticket = controller.beginServerRequest();
  controller.markApiMutation();

  assert.equal(controller.tryCommitServerResult(ticket), false);
  assert.equal(controller.getMode(), SNAPSHOT_SOURCE_MODES.API);
});

test("file selection becomes the latest manual source", () => {
  const controller = createSnapshotSourceController(SNAPSHOT_SOURCE_MODES.SAMPLE);

  controller.markManualSource(SNAPSHOT_SOURCE_MODES.FILE);

  assert.equal(controller.getMode(), SNAPSHOT_SOURCE_MODES.FILE);
  assert.equal(controller.getRevision(), 1);
});
