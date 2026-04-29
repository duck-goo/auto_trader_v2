import test from "node:test";
import assert from "node:assert/strict";

import { copyTextToClipboard } from "./copyTextToClipboard.js";

test("copies text with navigator clipboard when available", async () => {
  let copiedText = "";
  const fakeNavigator = {
    clipboard: {
      async writeText(value) {
        copiedText = value;
      },
    },
  };

  const result = await copyTextToClipboard("ops_snapshot.json", {
    navigator: fakeNavigator,
    document: null,
  });

  assert.equal(copiedText, "ops_snapshot.json");
  assert.deepEqual(result, { method: "navigator" });
});

test("falls back to legacy clipboard when navigator clipboard is unavailable", async () => {
  const appended = [];
  let removedNode = null;
  let execCommandValue = "";
  const fakeTextarea = {
    value: "",
    style: {},
    setAttribute() {},
    focus() {},
    select() {},
  };
  const fakeDocument = {
    body: {
      appendChild(node) {
        appended.push(node);
      },
      removeChild(node) {
        removedNode = node;
      },
    },
    createElement(tagName) {
      assert.equal(tagName, "textarea");
      return fakeTextarea;
    },
    execCommand(commandName) {
      execCommandValue = commandName;
      return true;
    },
  };

  const result = await copyTextToClipboard("ops_snapshot.json", {
    navigator: null,
    document: fakeDocument,
  });

  assert.equal(fakeTextarea.value, "ops_snapshot.json");
  assert.equal(execCommandValue, "copy");
  assert.equal(appended.length, 1);
  assert.equal(removedNode, fakeTextarea);
  assert.deepEqual(result, { method: "legacy" });
});

test("falls back to legacy clipboard when navigator clipboard write fails", async () => {
  const fakeNavigator = {
    clipboard: {
      async writeText() {
        throw new Error("blocked");
      },
    },
  };
  let legacyCopied = false;
  const fakeTextarea = {
    value: "",
    style: {},
    setAttribute() {},
    focus() {},
    select() {},
  };
  const fakeDocument = {
    body: {
      appendChild() {},
      removeChild() {},
    },
    createElement() {
      return fakeTextarea;
    },
    execCommand(commandName) {
      legacyCopied = commandName === "copy";
      return true;
    },
  };

  const result = await copyTextToClipboard("ops_snapshot.json", {
    navigator: fakeNavigator,
    document: fakeDocument,
  });

  assert.equal(legacyCopied, true);
  assert.deepEqual(result, { method: "legacy" });
});

test("throws when no clipboard path is available", async () => {
  await assert.rejects(
    copyTextToClipboard("ops_snapshot.json", {
      navigator: null,
      document: null,
    }),
    /Clipboard write is unavailable\./,
  );
});
