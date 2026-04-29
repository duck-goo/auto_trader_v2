export const SNAPSHOT_SOURCE_MODES = {
  API: "api",
  SAMPLE: "sample",
  FILE: "file",
};

export function createSnapshotSourceController(
  initialMode = SNAPSHOT_SOURCE_MODES.SAMPLE,
) {
  let revision = 0;
  let mode = initialMode;

  return {
    getMode() {
      return mode;
    },
    getRevision() {
      return revision;
    },
    beginServerRequest() {
      return {
        revision,
      };
    },
    markManualSource(nextMode) {
      mode = nextMode;
      revision += 1;
      return {
        mode,
        revision,
      };
    },
    markApiMutation() {
      mode = SNAPSHOT_SOURCE_MODES.API;
      revision += 1;
      return {
        mode,
        revision,
      };
    },
    tryCommitServerResult(ticket) {
      if (
        !ticket ||
        typeof ticket !== "object" ||
        ticket.revision !== revision
      ) {
        return false;
      }
      mode = SNAPSHOT_SOURCE_MODES.API;
      return true;
    },
  };
}
