export function parsePairBadgeValues(value) {
  if (typeof value !== "string") {
    return [];
  }

  const segments = value
    .split(",")
    .map((segment) => segment.trim())
    .filter(Boolean);

  if (!segments.length) {
    return [];
  }

  const uniqueSegments = [];
  for (const segment of segments) {
    if (!uniqueSegments.includes(segment)) {
      uniqueSegments.push(segment);
    }
  }

  return uniqueSegments;
}

export function buildBadgeListTitle(
  value,
  {
    titlePrefix = "Items",
    formatValue = (badge) => badge,
  } = {},
) {
  const badges = parsePairBadgeValues(value);
  if (!badges.length) {
    return undefined;
  }

  const normalizedPrefix =
    typeof titlePrefix === "string" && titlePrefix.trim()
      ? titlePrefix.trim()
      : "Items";
  const normalizedBadges = badges
    .map((badge) => {
      const formattedBadge = formatValue(badge);
      if (formattedBadge === null || formattedBadge === undefined) {
        return "";
      }

      return String(formattedBadge).trim();
    })
    .filter(Boolean);

  if (!normalizedBadges.length) {
    return undefined;
  }

  return `${normalizedPrefix}: ${normalizedBadges.join(", ")}`;
}

export function buildBadgePreviewMetadata(value, maxVisible = 3) {
  const badges = parsePairBadgeValues(value);
  if (!badges.length) {
    return {
      badges: [],
      hiddenValues: [],
    };
  }

  const safeMaxVisible = Number.isInteger(maxVisible) && maxVisible > 0 ? maxVisible : 3;
  if (badges.length <= safeMaxVisible) {
    return {
      badges,
      hiddenValues: [],
    };
  }

  const hiddenValues = badges.slice(safeMaxVisible);
  return {
    badges: [...badges.slice(0, safeMaxVisible), `+${hiddenValues.length}`],
    hiddenValues,
  };
}

export function buildBadgePreviewValues(value, maxVisible = 3) {
  return buildBadgePreviewMetadata(value, maxVisible).badges;
}
