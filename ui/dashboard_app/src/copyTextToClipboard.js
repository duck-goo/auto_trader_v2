function canUseNavigatorClipboard(browserNavigator) {
  return Boolean(
    browserNavigator?.clipboard &&
      typeof browserNavigator.clipboard.writeText === "function",
  );
}

function canUseLegacyClipboard(browserDocument) {
  return Boolean(
    browserDocument &&
      browserDocument.body &&
      typeof browserDocument.createElement === "function" &&
      typeof browserDocument.execCommand === "function",
  );
}

function copyTextWithLegacyClipboard(text, browserDocument) {
  if (!canUseLegacyClipboard(browserDocument)) {
    return false;
  }

  const textarea = browserDocument.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";

  browserDocument.body.appendChild(textarea);
  try {
    if (typeof textarea.focus === "function") {
      textarea.focus();
    }
    if (typeof textarea.select === "function") {
      textarea.select();
    }
    return browserDocument.execCommand("copy") === true;
  } finally {
    browserDocument.body.removeChild(textarea);
  }
}

export async function copyTextToClipboard(
  text,
  {
    navigator: browserNavigator = globalThis.navigator,
    document: browserDocument = globalThis.document,
  } = {},
) {
  const safeText = String(text ?? "");
  let navigatorClipboardError = null;

  if (canUseNavigatorClipboard(browserNavigator)) {
    try {
      await browserNavigator.clipboard.writeText(safeText);
      return { method: "navigator" };
    } catch (error) {
      navigatorClipboardError = error;
    }
  }

  if (copyTextWithLegacyClipboard(safeText, browserDocument)) {
    return { method: "legacy" };
  }

  if (navigatorClipboardError) {
    throw navigatorClipboardError;
  }

  throw new Error("Clipboard write is unavailable.");
}
