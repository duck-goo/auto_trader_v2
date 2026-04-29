import {
  startTransition,
  useDeferredValue,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { fetchDashboardSnapshot, setBuyStrategy, setKillSwitch } from "./api";
import {
  buildSourceInfoJumpTarget,
  buildOperatorJumpTargets,
  DASHBOARD_CARD_KEYS,
  OPERATOR_TARGET_GROUPS,
  dashboardCardElementId,
} from "./operatorSummaryTargets.js";
import {
  createSnapshotSourceController,
  SNAPSHOT_SOURCE_MODES,
} from "./snapshotSourceController.js";
import {
  buildAutoRefreshButtonLabel,
  buildAutoRefreshPrompt,
  buildFullSnapshotReloadSuccessMessage,
  buildLoadFromServerPrompt,
  buildManualSourcePrompt,
  buildReloadFullSnapshotPrompt,
  buildSourceDetailCopyFailureMessage,
  buildSourceDetailCopySuccessMessage,
  buildSourceAttentionNotice,
  buildToolbarSourceCopy,
  shouldEmphasizeLiveToolbar,
  shouldOfferSourceDetailCopy,
  shouldOfferSourceDetailExpansion,
  shouldUsePartialRestoreEmphasis,
  elevateStatusLevelForSourceAttention,
  buildApiSnapshotSourceInfo,
  buildFileSnapshotSourceInfo,
  buildPartialApiUpdateSourceInfo,
  buildSampleSnapshotSourceInfo,
  SNAPSHOT_SOURCE_UPDATE_KINDS,
  shouldOfferFullSnapshotReload,
} from "./snapshotSourceInfo.js";
import { copyTextToClipboard } from "./copyTextToClipboard.js";
import { resolveDebugSourcePreview } from "./debugSourcePreview.js";
import { sampleSnapshot } from "./sampleSnapshot";
import { asArray, asText, normalizeSnapshot, statusClassName } from "./snapshot";

const DEFAULT_REFRESH_INTERVAL_SECONDS = 30;
const MIN_REFRESH_INTERVAL_SECONDS = 10;
const MAX_REFRESH_INTERVAL_SECONDS = 300;
const SOURCE_STATUS_MESSAGE_CLEAR_DELAY_MS = 4000;
const initialDebugSourcePreview =
  typeof window !== "undefined"
    ? resolveDebugSourcePreview(window.location.search, sampleSnapshot.trade_date)
    : null;

function clampRefreshInterval(value) {
  if (!Number.isFinite(value)) {
    return DEFAULT_REFRESH_INTERVAL_SECONDS;
  }
  return Math.min(
    MAX_REFRESH_INTERVAL_SECONDS,
    Math.max(MIN_REFRESH_INTERVAL_SECONDS, Math.trunc(value)),
  );
}

function formatTimestamp(value) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(date);
}

function apiStatusLevel(status) {
  switch (status) {
    case "connected":
      return "READY";
    case "refreshing":
      return "WARNING";
    case "failed":
      return "FAILED";
    default:
      return "MISSING";
  }
}

function StatusBadge({ level, label }) {
  const safeLevel = level || "MISSING";
  return (
    <span className={`status-badge ${statusClassName(safeLevel)}`}>
      {label || safeLevel}
    </span>
  );
}

function sourceModeStatusLevel(baseMode) {
  switch (baseMode) {
    case SNAPSHOT_SOURCE_MODES.API:
      return "READY";
    case SNAPSHOT_SOURCE_MODES.FILE:
      return "WARNING";
    case SNAPSHOT_SOURCE_MODES.SAMPLE:
    default:
      return "WARNING";
  }
}

function jumpToDashboardTarget(target) {
  if (!target || typeof document === "undefined") {
    return false;
  }

  const sectionElement = document.getElementById(target.sectionId);
  if (!(sectionElement instanceof HTMLElement)) {
    return false;
  }

  const focusCandidate = target.focusId
    ? document.getElementById(target.focusId)
    : null;
  const focusElement =
    focusCandidate instanceof HTMLElement ? focusCandidate : sectionElement;
  const scrollElement =
    focusCandidate instanceof HTMLElement ? focusCandidate : sectionElement;

  if (typeof focusElement.focus === "function") {
    focusElement.focus({ preventScroll: true });
  }
  scrollElement.scrollIntoView({
    behavior: "smooth",
    block: "start",
  });
  return true;
}

function MetricCard({ label, value, subtext, statusLevel }) {
  return (
    <article className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{asText(value)}</div>
      <div className="metric-meta">
        {statusLevel ? <StatusBadge level={statusLevel} /> : <span>{asText(subtext)}</span>}
      </div>
      {subtext && statusLevel ? <div className="metric-subtext">{subtext}</div> : null}
    </article>
  );
}

function PairList({ pairs }) {
  return (
    <div className="pair-list">
      {pairs.map((pair) => (
        <div className="pair-row" key={pair.key}>
          <span className="pair-key">{pair.key}</span>
          <strong className="pair-value">{asText(pair.value)}</strong>
        </div>
      ))}
    </div>
  );
}

function FlagList({ flags }) {
  const rows = asArray(flags);
  if (!rows.length) {
    return <p className="empty-copy">No warning flags.</p>;
  }

  return (
    <div className="flag-list">
      {rows.map((flag) => (
        <span
          className={`flag-pill ${
            String(flag).includes("CRITICAL") ? "flag-critical" : "flag-warning"
          }`}
          key={flag}
        >
          {flag}
        </span>
      ))}
    </div>
  );
}

function InfoCard({ label, title, statusLevel, pairs, flags, cardId, cardKey }) {
  const resolvedCardId = cardId || dashboardCardElementId(cardKey);
  return (
    <article
      id={resolvedCardId}
      className="info-card"
      tabIndex={resolvedCardId ? -1 : undefined}
    >
      <div className="info-card-header">
        <div>
          <p className="info-label">{label}</p>
          <h3>{title}</h3>
        </div>
        <StatusBadge level={statusLevel} />
      </div>
      <PairList pairs={pairs} />
      <FlagList flags={flags} />
    </article>
  );
}

function Section({ eyebrow, title, children, wide = false, sectionId }) {
  return (
    <section
      id={sectionId}
      className={`panel section-card ${wide ? "section-wide" : ""}`}
      tabIndex={-1}
    >
      <div className="section-heading">
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function ActionsSection({ actions }) {
  const items = asArray(actions?.items);
  if (!items.length) {
    return <p className="empty-copy">No immediate action items.</p>;
  }

  return (
    <div className="action-stack">
      {items.map((item, index) => (
        <article
          id={index === 0 ? "first-action-item-card" : undefined}
          className="action-card"
          key={item.action_code}
          tabIndex={index === 0 ? -1 : undefined}
        >
          <div className="action-card-header">
            <h3>{item.action_code}</h3>
            <StatusBadge level={item.severity || "WARNING"} />
          </div>
          <p className="action-summary">{asText(item.summary)}</p>
          {item.detail ? <p className="action-detail">{asText(item.detail)}</p> : null}
          {item.suggested_command ? (
            <pre className="action-command">
              <code>{item.suggested_command}</code>
            </pre>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function OperatorSummaryBanner({ summary, overviewStatusLevel, attentionNotice }) {
  const statusLevel = elevateStatusLevelForSourceAttention(
    summary.status_level || overviewStatusLevel || "NO_DATA",
    attentionNotice,
  );
  const headline = asText(summary.headline, "No operator summary available.");
  const detail = asText(summary.detail, "");
  const jumpTargets = buildOperatorJumpTargets(summary, {
    relatedActionCodes: asArray(summary.related_action_codes),
    relatedAttentionFlags: asArray(summary.related_attention_flags),
  });
  const jumpGroups = [
    {
      key: OPERATOR_TARGET_GROUPS.PRIMARY,
      label: "Primary",
      targets: jumpTargets.filter((target) => target.group === OPERATOR_TARGET_GROUPS.PRIMARY),
    },
    {
      key: OPERATOR_TARGET_GROUPS.RELATED,
      label: "Related",
      targets: jumpTargets.filter((target) => target.group === OPERATOR_TARGET_GROUPS.RELATED),
    },
    {
      key: OPERATOR_TARGET_GROUPS.FALLBACK,
      label: "Fallback",
      targets: jumpTargets.filter((target) => target.group === OPERATOR_TARGET_GROUPS.FALLBACK),
    },
  ].filter((group) => group.targets.length > 0);
  const summaryPairs = [
    {
      key: "Primary Flag",
      value: summary.primary_attention_flag,
    },
    {
      key: "Action",
      value: summary.primary_action_code,
    },
    {
      key: "Affected Symbols",
      value: summary.affected_symbols,
    },
    {
      key: "Dispatch",
      value: summary.dispatch_outcome,
    },
  ].filter((pair) => pair.value !== null && pair.value !== undefined && pair.value !== "");

  return (
    <section className={`panel operator-banner ${statusClassName(statusLevel)}`}>
      <div className="operator-banner-top">
        <div className="operator-banner-copy">
          <p className="eyebrow">Operator Summary</p>
          <h2>{headline}</h2>
          {detail ? <p>{detail}</p> : null}
          {attentionNotice ? (
            <div className="operator-attention-note" role="note">
              <span className="operator-attention-badge">
                {attentionNotice.badgeLabel}
              </span>
              <span className="operator-attention-detail">
                {attentionNotice.detail}
              </span>
            </div>
          ) : null}
        </div>
        <div className="operator-banner-status">
          <StatusBadge level={statusLevel} />
        </div>
      </div>
      {summaryPairs.length ? (
        <div className="operator-pill-row">
          {summaryPairs.map((pair) => (
            <div className="operator-pill" key={pair.key}>
              <span>{pair.key}</span>
              <strong>{asText(pair.value)}</strong>
            </div>
          ))}
        </div>
      ) : null}
      {jumpGroups.length ? (
        <div className="operator-jump-groups">
          {jumpGroups.map((group) => (
            <div className="operator-jump-group" key={group.key}>
              <p className="operator-jump-group-label">{group.label}</p>
              <div className="operator-jump-row">
                {group.targets.map((target) => (
                  <a
                    className={`operator-jump-button operator-jump-button-${group.key}`}
                    href={`#${target.sectionId}`}
                    key={`${target.sectionId}:${target.focusId || target.label}`}
                    onClick={(event) => {
                      if (jumpToDashboardTarget(target)) {
                        event.preventDefault();
                      }
                    }}
                  >
                    Jump to {target.label}
                  </a>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function App() {
  const inputId = useId();
  const [snapshot, setSnapshot] = useState(() => normalizeSnapshot(sampleSnapshot));
  const [sourceInfo, setSourceInfo] = useState(
    () => initialDebugSourcePreview?.sourceInfo || buildSampleSnapshotSourceInfo(),
  );
  const [errorMessage, setErrorMessage] = useState("");
  const [tradeDate, setTradeDate] = useState(
    initialDebugSourcePreview?.tradeDate || sampleSnapshot.trade_date,
  );
  const [isLoading, setIsLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState(
    initialDebugSourcePreview?.apiStatus || "unknown",
  );
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(false);
  const [refreshIntervalSeconds, setRefreshIntervalSeconds] = useState(
    DEFAULT_REFRESH_INTERVAL_SECONDS,
  );
  const [sourceStatusMessage, setSourceStatusMessage] = useState("");
  const [sourceDetailCopyStatusMessage, setSourceDetailCopyStatusMessage] = useState("");
  const [isSourceDetailExpanded, setIsSourceDetailExpanded] = useState(false);
  const [lastSuccessfulRefreshAt, setLastSuccessfulRefreshAt] = useState("");
  const [lastRefreshErrorAt, setLastRefreshErrorAt] = useState("");
  const [killSwitchNote, setKillSwitchNote] = useState("dashboard emergency stop");
  const [resumeConfirmation, setResumeConfirmation] = useState("");
  const [isKillSwitchUpdating, setIsKillSwitchUpdating] = useState(false);
  const [killSwitchStatusMessage, setKillSwitchStatusMessage] = useState("");
  const [selectedBuyStrategy, setSelectedBuyStrategy] = useState("both");
  const [buyStrategyNote, setBuyStrategyNote] = useState("");
  const [isBuyStrategyUpdating, setIsBuyStrategyUpdating] = useState(false);
  const [buyStrategyStatusMessage, setBuyStrategyStatusMessage] = useState("");
  const requestInFlightRef = useRef(false);
  const abortControllerRef = useRef(null);
  const killSwitchAbortControllerRef = useRef(null);
  const buyStrategyAbortControllerRef = useRef(null);
  const snapshotSourceControllerRef = useRef(
    createSnapshotSourceController(SNAPSHOT_SOURCE_MODES.SAMPLE),
  );

  const deferredSnapshot = useDeferredValue(snapshot);
  const overview = deferredSnapshot.overview || {};
  const operatorSummary = deferredSnapshot.operator_summary || {};
  const startup = deferredSnapshot.startup || {};
  const controls = deferredSnapshot.controls || {};
  const strategy = deferredSnapshot.strategy || {};
  const scan = deferredSnapshot.scan || {};
  const executions = deferredSnapshot.executions || {};
  const recovery = deferredSnapshot.recovery || {};
  const rehearsal = deferredSnapshot.rehearsal || {};
  const sources = deferredSnapshot.sources || {};
  const actions = deferredSnapshot.actions || {};
  const sourceJumpTarget = buildSourceInfoJumpTarget(sourceInfo);
  const canReloadFullSnapshot = shouldOfferFullSnapshotReload(
    sourceInfo,
    apiStatus,
  );
  const autoRefreshPrompt = buildAutoRefreshPrompt(
    sourceInfo,
    apiStatus,
    autoRefreshEnabled,
  );
  const autoRefreshButtonLabel = buildAutoRefreshButtonLabel(
    sourceInfo,
    autoRefreshEnabled,
  );
  const reloadFullSnapshotPrompt = buildReloadFullSnapshotPrompt(sourceInfo);
  const toolbarSourceCopy = buildToolbarSourceCopy(sourceInfo);
  const liveToolbarEmphasis = shouldEmphasizeLiveToolbar(sourceInfo);
  const partialRestoreEmphasis = shouldUsePartialRestoreEmphasis(sourceInfo);
  const loadFromServerPrompt =
    reloadFullSnapshotPrompt || buildLoadFromServerPrompt(sourceInfo);
  const manualSourcePrompt = buildManualSourcePrompt(sourceInfo);
  const sourceAttentionNotice = buildSourceAttentionNotice(sourceInfo);
  const debugSourcePreviewNotice = initialDebugSourcePreview?.notice || null;
  const sourceReviewJumpLabel =
    reloadFullSnapshotPrompt?.reviewLabel ||
    (sourceJumpTarget ? `Jump to ${sourceJumpTarget.label} first` : "");
  const canCopySourceDetail = shouldOfferSourceDetailCopy(sourceInfo);
  const canExpandSourceDetail = shouldOfferSourceDetailExpansion(sourceInfo);
  const sourceDetailCopySuccess =
    sourceDetailCopyStatusMessage === buildSourceDetailCopySuccessMessage();
  const sourceDetailCopyExpandedFallback =
    sourceDetailCopyStatusMessage ===
    buildSourceDetailCopyFailureMessage(true);
  const sourceDetailCopyButtonLabel = sourceDetailCopyStatusMessage
    ? sourceDetailCopySuccess
      ? "Copied"
      : sourceDetailCopyExpandedFallback
        ? "Shown Below"
        : "Copy Failed"
    : "Copy Full Name";
  const operatorSummaryWithRelated = {
    ...operatorSummary,
    related_action_codes: asArray(actions.top_action_codes).filter(
      (code) => code && code !== operatorSummary.primary_action_code,
    ),
    related_attention_flags: asArray(actions.items)
      .map((item) => item?.flag)
      .filter((flag) => flag && flag !== operatorSummary.primary_attention_flag),
  };

  function cancelInFlightRequest() {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    requestInFlightRef.current = false;
    setIsLoading(false);
  }

  useEffect(() => {
    if (!sourceStatusMessage) {
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      setSourceStatusMessage("");
    }, SOURCE_STATUS_MESSAGE_CLEAR_DELAY_MS);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [sourceStatusMessage]);

  useEffect(() => {
    if (!sourceDetailCopyStatusMessage) {
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      setSourceDetailCopyStatusMessage("");
    }, SOURCE_STATUS_MESSAGE_CLEAR_DELAY_MS);

    return () => {
      window.clearTimeout(timerId);
    };
  }, [sourceDetailCopyStatusMessage]);

  useEffect(() => {
    setSourceDetailCopyStatusMessage("");
    setIsSourceDetailExpanded(false);
  }, [sourceInfo.label, sourceInfo.detailLabel, sourceInfo.updateKind]);

  async function loadFromServer(nextTradeDate, options = {}) {
    const safeTradeDate = nextTradeDate || tradeDate;
    if (requestInFlightRef.current) {
      return false;
    }

    const loadTicket = snapshotSourceControllerRef.current.beginServerRequest();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    requestInFlightRef.current = true;
    setIsLoading(true);
    setSourceStatusMessage("");
    if (options.background) {
      setApiStatus("refreshing");
    }
    try {
      const payload = await fetchDashboardSnapshot({
        tradeDate: safeTradeDate,
        signal: controller.signal,
      });
      if (!snapshotSourceControllerRef.current.tryCommitServerResult(loadTicket)) {
        return false;
      }
      setErrorMessage("");
      setApiStatus("connected");
      setLastRefreshErrorAt("");
      setLastSuccessfulRefreshAt(new Date().toISOString());
      setSourceStatusMessage(options.successMessage || "");
      startTransition(() => {
        setSnapshot(normalizeSnapshot(payload));
        setSourceInfo(buildApiSnapshotSourceInfo(safeTradeDate));
      });
      return true;
    } catch (error) {
      if (controller.signal.aborted) {
        return false;
      }

      setApiStatus("failed");
      setLastRefreshErrorAt(new Date().toISOString());
      if (!options.silent) {
        const detail =
          error instanceof Error ? error.message : String(error);
        setErrorMessage(
          options.background
            ? `Auto refresh stopped after API load failed: ${detail}`
            : `Snapshot API load failed: ${detail}`,
        );
      }
      if (options.background) {
        setAutoRefreshEnabled(false);
      }
      return false;
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      requestInFlightRef.current = false;
      setIsLoading(false);
    }
  }

  async function handleCopySourceDetail() {
    if (!shouldOfferSourceDetailCopy(sourceInfo)) {
      return;
    }

    try {
      await copyTextToClipboard(sourceInfo.detailLabel);
      setSourceDetailCopyStatusMessage(buildSourceDetailCopySuccessMessage());
    } catch {
      if (canExpandSourceDetail) {
        setIsSourceDetailExpanded(true);
      }
      setSourceDetailCopyStatusMessage(
        buildSourceDetailCopyFailureMessage(canExpandSourceDetail),
      );
    }
  }

  function handleExitDebugSourcePreview() {
    if (typeof window === "undefined") {
      return;
    }
    window.location.assign(window.location.pathname);
  }

  function loadSample() {
    cancelInFlightRequest();
    snapshotSourceControllerRef.current.markManualSource(
      SNAPSHOT_SOURCE_MODES.SAMPLE,
    );
    setErrorMessage("");
    setApiStatus("unknown");
    setAutoRefreshEnabled(false);
    setSourceStatusMessage("");
    setKillSwitchStatusMessage("");
    setBuyStrategyStatusMessage("");
    startTransition(() => {
      setSnapshot(normalizeSnapshot(sampleSnapshot));
      setSourceInfo(buildSampleSnapshotSourceInfo());
    });
  }

  function applyKillSwitchControls(nextControls) {
    if (!nextControls || typeof nextControls !== "object") {
      return;
    }
    snapshotSourceControllerRef.current.markApiMutation();
    setSourceStatusMessage("");
    startTransition(() => {
      setSnapshot((currentSnapshot) =>
        normalizeSnapshot({
          ...currentSnapshot,
          controls: {
            ...(currentSnapshot.controls || {}),
            ...nextControls,
          },
        }),
      );
      setSourceInfo((currentSourceInfo) =>
        buildPartialApiUpdateSourceInfo(
          currentSourceInfo,
          SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_CONTROLS,
        ),
      );
    });
  }

  async function handleKillSwitchUpdate(nextEnabled) {
    if (isKillSwitchUpdating) {
      return;
    }

    const note = killSwitchNote.trim();
    if (!nextEnabled && resumeConfirmation !== "RESUME") {
      setKillSwitchStatusMessage("Type RESUME before disabling Kill Switch.");
      return;
    }
    if (!nextEnabled && !note) {
      setKillSwitchStatusMessage("A review note is required before resume.");
      return;
    }

    const controller = new AbortController();
    killSwitchAbortControllerRef.current = controller;
    setIsKillSwitchUpdating(true);
    setKillSwitchStatusMessage("");
    setErrorMessage("");

    try {
      const result = await setKillSwitch({
        enabled: nextEnabled,
        note: note || "dashboard emergency stop",
        tradeDate,
        signal: controller.signal,
      });
      setApiStatus("connected");
      setLastSuccessfulRefreshAt(new Date().toISOString());
      setLastRefreshErrorAt("");
      applyKillSwitchControls(result.controls);
      setKillSwitchStatusMessage(
        nextEnabled ? "Kill Switch enabled." : "Kill Switch disabled.",
      );
      if (!nextEnabled) {
        setResumeConfirmation("");
      }
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const detail = error instanceof Error ? error.message : String(error);
      setApiStatus("failed");
      setAutoRefreshEnabled(false);
      setLastRefreshErrorAt(new Date().toISOString());
      setErrorMessage(`Kill Switch update failed: ${detail}`);
    } finally {
      if (killSwitchAbortControllerRef.current === controller) {
        killSwitchAbortControllerRef.current = null;
      }
      setIsKillSwitchUpdating(false);
    }
  }

  function applyBuyStrategy(nextStrategy) {
    if (!nextStrategy || typeof nextStrategy !== "object") {
      return;
    }
    snapshotSourceControllerRef.current.markApiMutation();
    setSourceStatusMessage("");
    startTransition(() => {
      setSnapshot((currentSnapshot) =>
        normalizeSnapshot({
          ...currentSnapshot,
          strategy: {
            ...(currentSnapshot.strategy || {}),
            ...nextStrategy,
          },
        }),
      );
      setSourceInfo((currentSourceInfo) =>
        buildPartialApiUpdateSourceInfo(
          currentSourceInfo,
          SNAPSHOT_SOURCE_UPDATE_KINDS.PARTIAL_STRATEGY,
        ),
      );
    });
  }

  async function handleBuyStrategyUpdate() {
    if (isBuyStrategyUpdating) {
      return;
    }

    const controller = new AbortController();
    buyStrategyAbortControllerRef.current = controller;
    setIsBuyStrategyUpdating(true);
    setBuyStrategyStatusMessage("");
    setErrorMessage("");

    try {
      const result = await setBuyStrategy({
        buyStrategy: selectedBuyStrategy,
        note: buyStrategyNote.trim(),
        tradeDate,
        signal: controller.signal,
      });
      setApiStatus("connected");
      setLastSuccessfulRefreshAt(new Date().toISOString());
      setLastRefreshErrorAt("");
      applyBuyStrategy(result.strategy);
      setBuyStrategyStatusMessage("Buy strategy saved for the next run.");
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      const detail = error instanceof Error ? error.message : String(error);
      setApiStatus("failed");
      setAutoRefreshEnabled(false);
      setLastRefreshErrorAt(new Date().toISOString());
      setErrorMessage(`Buy strategy update failed: ${detail}`);
    } finally {
      if (buyStrategyAbortControllerRef.current === controller) {
        buyStrategyAbortControllerRef.current = null;
      }
      setIsBuyStrategyUpdating(false);
    }
  }

  function handleFileChange(event) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    cancelInFlightRequest();
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result));
        snapshotSourceControllerRef.current.markManualSource(
          SNAPSHOT_SOURCE_MODES.FILE,
        );
        setErrorMessage("");
        setApiStatus("unknown");
        setAutoRefreshEnabled(false);
        setSourceStatusMessage("");
        startTransition(() => {
          setSnapshot(normalizeSnapshot(parsed));
          setSourceInfo(buildFileSnapshotSourceInfo(file.name));
        });
      } catch (error) {
        setErrorMessage(
          `JSON parse failed: ${
            error instanceof Error ? error.message : String(error)
          }`
        );
      }
    };
    reader.onerror = () => {
      setErrorMessage("File read failed.");
    };
    reader.readAsText(file, "utf-8");
    event.target.value = "";
  }

  useEffect(() => {
    if (initialDebugSourcePreview?.skipInitialApiLoad) {
      return undefined;
    }
    void loadFromServer(tradeDate, { silent: true });
    // We only want one initial API attempt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!autoRefreshEnabled) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      void loadFromServer(tradeDate, {
        background: true,
        silent: false,
      });
    }, refreshIntervalSeconds * 1000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [autoRefreshEnabled, refreshIntervalSeconds, tradeDate]);

  useEffect(() => {
    const nextBuyStrategy =
      strategy.buy_strategy || strategy.effective_buy_strategy || "both";
    if (["timing1", "timing2", "both"].includes(nextBuyStrategy)) {
      setSelectedBuyStrategy(nextBuyStrategy);
    }
  }, [strategy.buy_strategy, strategy.effective_buy_strategy]);

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort();
      killSwitchAbortControllerRef.current?.abort();
      buyStrategyAbortControllerRef.current?.abort();
    };
  }, []);

  const overviewCards = [
    {
      label: "Health",
      value: overview.health_outcome || overview.status_level,
      subtext: `report_outcome=${asText(overview.report_outcome)}`,
      statusLevel: overview.status_level,
    },
    {
      label: "Artifacts",
      value: overview.artifact_count,
      subtext: `flags=${asText(overview.attention_flag_count, "0")}`,
    },
    {
      label: "Critical Flags",
      value: overview.critical_flag_count,
      subtext: `warning=${asText(overview.warning_flag_count, "0")}`,
    },
    {
      label: "Action Required",
      value: overview.action_required ? "YES" : "NO",
      subtext:
        asArray(overview.top_action_codes).join(", ") || "No immediate action",
      statusLevel: overview.action_required ? "WARNING" : "READY",
    },
  ];

  const scanCards = [
    {
      label: "Live Preview",
      title: "run_trading_session.preview",
      cardKey: scan.live_preview?.card_key || DASHBOARD_CARD_KEYS.scanLivePreview,
      row: scan.live_preview || {},
      pairs: [
        { key: "session_outcome", value: scan.live_preview?.session_outcome },
        { key: "polling_stop_reason", value: scan.live_preview?.polling_stop_reason },
        { key: "timing2_setup_ready", value: scan.live_preview?.timing2_setup_ready },
        {
          key: "timing2_setup_signal_count",
          value: scan.live_preview?.timing2_setup_signal_count,
        },
      ],
    },
    {
      label: "Live Execute",
      title: "run_trading_session.execute",
      cardKey: scan.live_execute?.card_key || DASHBOARD_CARD_KEYS.scanLiveExecute,
      row: scan.live_execute || {},
      pairs: [
        { key: "session_outcome", value: scan.live_execute?.session_outcome },
        { key: "polling_stop_reason", value: scan.live_execute?.polling_stop_reason },
        { key: "timing2_setup_ready", value: scan.live_execute?.timing2_setup_ready },
        { key: "polling_exit_code", value: scan.live_execute?.polling_exit_code },
      ],
    },
    {
      label: "Rehearsal Validation",
      title: "mock trading verification",
      cardKey:
        scan.rehearsal_validation?.card_key ||
        DASHBOARD_CARD_KEYS.scanRehearsalValidation,
      row: scan.rehearsal_validation || {},
      pairs: [
        {
          key: "session_outcome",
          value: scan.rehearsal_validation?.session_outcome,
        },
        {
          key: "polling_stop_reason",
          value: scan.rehearsal_validation?.polling_stop_reason,
        },
        {
          key: "timing2_setup_ready",
          value: scan.rehearsal_validation?.timing2_setup_ready,
        },
        {
          key: "timing2_30s_verified",
          value: scan.rehearsal_validation?.timing2_30s_verified,
        },
      ],
    },
  ];

  const executionCards = [
    {
      label: "Buy Preview",
      title: "execute_buy_signals.preview",
      cardKey:
        executions.buy_preview?.card_key || DASHBOARD_CARD_KEYS.executionBuyPreview,
      row: executions.buy_preview || {},
    },
    {
      label: "Buy Execute",
      title: "execute_buy_signals.execute",
      cardKey:
        executions.buy_execute?.card_key || DASHBOARD_CARD_KEYS.executionBuyExecute,
      row: executions.buy_execute || {},
    },
    {
      label: "Sell Preview",
      title: "execute_sell_signals.preview",
      cardKey:
        executions.sell_preview?.card_key || DASHBOARD_CARD_KEYS.executionSellPreview,
      row: executions.sell_preview || {},
    },
    {
      label: "Sell Execute",
      title: "execute_sell_signals.execute",
      cardKey:
        executions.sell_execute?.card_key || DASHBOARD_CARD_KEYS.executionSellExecute,
      row: executions.sell_execute || {},
    },
  ];

  const recoveryCards = [
    {
      label: "Maintenance Preview",
      title: "order_maintenance.preview",
      cardKey:
        recovery.order_maintenance_preview?.card_key ||
        DASHBOARD_CARD_KEYS.recoveryMaintenancePreview,
      row: recovery.order_maintenance_preview || {},
    },
    {
      label: "Maintenance Execute",
      title: "order_maintenance.execute",
      cardKey:
        recovery.order_maintenance_execute?.card_key ||
        DASHBOARD_CARD_KEYS.recoveryMaintenanceExecute,
      row: recovery.order_maintenance_execute || {},
    },
    {
      label: "Recovery Review",
      title: "execution_recovery.review",
      cardKey:
        recovery.execution_recovery_review?.card_key ||
        DASHBOARD_CARD_KEYS.recoveryExecutionReview,
      row: recovery.execution_recovery_review || {},
    },
  ];

  const apiReadyForKillSwitch = apiStatus === "connected";
  const apiReadyForStrategy = apiStatus === "connected";
  const killSwitchEnabled = controls.kill_switch_enabled === true;
  const currentBuyStrategy =
    strategy.buy_strategy || strategy.effective_buy_strategy || "both";
  const disableKillSwitchBlocked =
    !apiReadyForKillSwitch ||
    !killSwitchEnabled ||
    resumeConfirmation !== "RESUME" ||
    !killSwitchNote.trim() ||
    isKillSwitchUpdating;

  return (
    <div className="app-shell">
      <header className="panel hero-card">
        <div className="hero-copy">
          <p className="eyebrow">Auto Trader V2</p>
          <h1>Dashboard App</h1>
          <p className="hero-text">
            React dashboard shell backed by dashboard_snapshot.json. If the local
            snapshot API is running, the app can pull the current trade-date state
            without manual file upload.
          </p>
        </div>
        <div className="hero-status">
          <div className="hero-status-card">
            <span className="hero-status-label">Trade Date</span>
            <strong className="hero-status-value">{deferredSnapshot.trade_date}</strong>
            <StatusBadge
              level={overview.status_level || "NO_DATA"}
              label={overview.health_outcome || overview.status_level || "NO DATA"}
            />
            <p className="hero-status-meta">
              highest_severity={asText(overview.highest_severity)}
            </p>
          </div>
        </div>
      </header>

      {debugSourcePreviewNotice ? (
        <section className="info-banner info-banner-debug">
          <div className="info-banner-row">
            <div className="info-banner-copy-block">
              <strong>{debugSourcePreviewNotice.title}</strong>
              <span>{debugSourcePreviewNotice.detail}</span>
            </div>
            <button
              className="secondary-button info-banner-button"
              type="button"
              onClick={handleExitDebugSourcePreview}
            >
              Exit Preview Mode
            </button>
          </div>
        </section>
      ) : null}

      <section className="panel toolbar-card">
        <div>
          <p className="eyebrow">Input</p>
          <h2>Snapshot Source</h2>
          <p className="toolbar-copy">{toolbarSourceCopy}</p>
        </div>

        <div
          className={`toolbar-actions ${
            liveToolbarEmphasis ? "toolbar-actions-priority" : ""
          }`}
        >
          <div className="toolbar-form">
            <label className="field-label" htmlFor="trade-date-input">
              Trade Date
            </label>
            <input
              id="trade-date-input"
              className="text-input"
              type="text"
              inputMode="numeric"
              placeholder="YYYY-MM-DD"
              value={tradeDate}
              onChange={(event) => setTradeDate(event.target.value)}
            />
          </div>
          <div className="toolbar-form">
            <label className="field-label" htmlFor="refresh-interval-input">
              Refresh Sec
            </label>
            <input
              id="refresh-interval-input"
              className="text-input"
              type="number"
              min={MIN_REFRESH_INTERVAL_SECONDS}
              max={MAX_REFRESH_INTERVAL_SECONDS}
              step="5"
              value={refreshIntervalSeconds}
              onChange={(event) => {
                setRefreshIntervalSeconds(
                  clampRefreshInterval(Number.parseInt(event.target.value, 10)),
                );
              }}
            />
          </div>
          <div
            className={`toolbar-action-stack toolbar-action-stack-live ${
              liveToolbarEmphasis ? "toolbar-action-stack-live-priority" : ""
            } ${partialRestoreEmphasis ? "toolbar-action-stack-live-restore" : ""}`}
          >
            <button
              className={`primary-button ${
                loadFromServerPrompt ? "primary-button-attention" : ""
              } ${partialRestoreEmphasis ? "primary-button-restore" : ""}`}
              type="button"
              onClick={() => {
                void loadFromServer(
                  tradeDate,
                  reloadFullSnapshotPrompt?.successMessage
                    ? {
                        successMessage: reloadFullSnapshotPrompt.successMessage,
                      }
                    : {},
                );
              }}
              disabled={isLoading}
            >
              {isLoading
                ? "Loading..."
                : loadFromServerPrompt?.buttonLabel || "Load From Server"}
            </button>
            {loadFromServerPrompt ? (
              <div className="toolbar-action-support">
                <p className="toolbar-action-note">{loadFromServerPrompt.detail}</p>
                {reloadFullSnapshotPrompt && sourceJumpTarget ? (
                  <a
                    className="toolbar-inline-link"
                    href={`#${sourceJumpTarget.sectionId}`}
                  onClick={(event) => {
                    if (jumpToDashboardTarget(sourceJumpTarget)) {
                      event.preventDefault();
                    }
                  }}
                >
                  {sourceReviewJumpLabel}
                </a>
                ) : null}
              </div>
            ) : null}
          </div>
          <div
            className={`toolbar-action-stack toolbar-action-stack-manual ${
              liveToolbarEmphasis ? "toolbar-action-stack-manual-priority" : ""
            }`}
          >
            <div className="toolbar-manual-buttons">
              <label
                className={`secondary-button file-button ${
                  manualSourcePrompt ? "secondary-button-subdued" : ""
                }`}
                htmlFor={inputId}
              >
                Load JSON File
              </label>
              <input
                id={inputId}
                className="hidden-input"
                type="file"
                accept=".json,application/json"
                onChange={handleFileChange}
              />
              <button
                className={`secondary-button ${
                  manualSourcePrompt ? "secondary-button-subdued" : ""
                }`}
                type="button"
                onClick={loadSample}
              >
                Load Sample
              </button>
            </div>
            {manualSourcePrompt ? (
              <p className="toolbar-action-note toolbar-action-note-subtle">
                {manualSourcePrompt.detail}
              </p>
            ) : null}
          </div>
          <div className="toolbar-action-stack">
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                setErrorMessage("");
                setAutoRefreshEnabled((current) => !current);
              }}
              disabled={apiStatus !== "connected" && !autoRefreshEnabled}
            >
              {autoRefreshButtonLabel}
            </button>
            {autoRefreshPrompt ? (
              <p className="toolbar-action-note toolbar-action-note-subtle">
                {autoRefreshPrompt.detail}
              </p>
            ) : null}
          </div>
        </div>

        <div className="toolbar-meta">
          <div className="source-meta-block">
            <span className="meta-label">Current Source</span>
            <div className="source-pill-row">
              <StatusBadge
                level={sourceModeStatusLevel(sourceInfo.baseMode)}
                label={sourceInfo.modeLabel}
              />
              <span
                className={`source-pill ${
                  sourceInfo.isPartial ? "source-pill-partial" : "source-pill-full"
                } ${partialRestoreEmphasis ? "source-pill-partial-restore" : ""}`}
              >
                {sourceInfo.updateLabel}
              </span>
              {sourceJumpTarget ? (
                <a
                  className="source-quick-link"
                  href={`#${sourceJumpTarget.sectionId}`}
                  onClick={(event) => {
                    if (jumpToDashboardTarget(sourceJumpTarget)) {
                      event.preventDefault();
                    }
                  }}
                >
                  {sourceReviewJumpLabel || `Jump to ${sourceJumpTarget.label}`}
                </a>
              ) : null}
              {canReloadFullSnapshot ? (
                <button
                  className="source-quick-link source-quick-button"
                  type="button"
                  onClick={() => {
                    void loadFromServer(tradeDate, {
                      successMessage:
                        reloadFullSnapshotPrompt?.successMessage ||
                        buildFullSnapshotReloadSuccessMessage(),
                    });
                  }}
                  disabled={isLoading}
                >
                  {isLoading
                    ? "Reloading..."
                    : reloadFullSnapshotPrompt?.buttonLabel || "Reload Full Snapshot"}
                </button>
              ) : null}
              </div>
              <strong className="source-current-label">{sourceInfo.label}</strong>
              {sourceInfo.detailLabel ? (
                <div className="source-current-detail-stack">
                  <div className="source-current-detail-row">
                  <span
                    className={`source-current-detail ${
                      sourceInfo.detailLabelKind === "filename"
                        ? "source-current-detail-filename"
                        : ""
                    }`}
                    title={sourceInfo.detailLabel}
                  >
                    {sourceInfo.detailDisplayLabel || sourceInfo.detailLabel}
                  </span>
                    {canCopySourceDetail || canExpandSourceDetail ? (
                      <div className="source-detail-actions">
                        {canCopySourceDetail ? (
                          <button
                            className={`source-detail-copy-button ${
                              sourceDetailCopyStatusMessage
                                ? sourceDetailCopySuccess
                                  ? "source-detail-copy-button-success"
                                  : "source-detail-copy-button-failed"
                                : ""
                            }`}
                            type="button"
                            onClick={() => {
                              void handleCopySourceDetail();
                            }}
                            aria-label={
                              sourceDetailCopyStatusMessage || "Copy full file name"
                            }
                            title={sourceDetailCopyStatusMessage || "Copy full file name"}
                          >
                            {sourceDetailCopyButtonLabel}
                          </button>
                        ) : null}
                        {canExpandSourceDetail ? (
                          <button
                            className="source-detail-copy-button source-detail-expand-button"
                            type="button"
                            onClick={() => {
                              setIsSourceDetailExpanded((current) => !current);
                            }}
                            aria-expanded={isSourceDetailExpanded}
                          >
                            {isSourceDetailExpanded ? "Hide Full Name" : "Show Full Name"}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  {canExpandSourceDetail && isSourceDetailExpanded ? (
                    <div
                      className="source-current-detail-expanded source-current-detail-filename"
                      title={sourceInfo.detailLabel}
                    >
                      {sourceInfo.detailLabel}
                    </div>
                  ) : null}
                </div>
              ) : null}
            {sourceAttentionNotice ? (
              <div className="source-attention-note" role="note">
                <span className="source-attention-badge">
                  {sourceAttentionNotice.badgeLabel}
                </span>
                <span className="source-attention-detail">
                  {sourceAttentionNotice.detail}
                </span>
              </div>
            ) : null}
            {sourceStatusMessage ? (
              <span
                className="source-status-badge"
                role="status"
                aria-label={sourceStatusMessage}
                title={sourceStatusMessage}
              >
                RESTORED
              </span>
            ) : null}
          </div>
          <div>
            <span className="meta-label">Generated At</span>
            <strong>{formatTimestamp(deferredSnapshot.generated_at)}</strong>
          </div>
          <div>
            <span className="meta-label">API Status</span>
            <StatusBadge
              level={apiStatusLevel(apiStatus)}
              label={autoRefreshEnabled ? `${apiStatus} / polling` : apiStatus}
            />
          </div>
          <div>
            <span className="meta-label">Last Success</span>
            <strong>{formatTimestamp(lastSuccessfulRefreshAt)}</strong>
          </div>
          <div>
            <span className="meta-label">Last Error</span>
            <strong>{formatTimestamp(lastRefreshErrorAt)}</strong>
          </div>
        </div>
      </section>

      {apiStatus === "connected" ? (
        <section className="info-banner info-banner-ready">
          {autoRefreshEnabled
            ? `Local snapshot API connected. Auto refresh is running every ${refreshIntervalSeconds} seconds.`
            : "Local snapshot API connected. Manual reload is available from the toolbar."}
        </section>
      ) : null}

      {errorMessage ? <section className="error-banner">{errorMessage}</section> : null}

      <OperatorSummaryBanner
        summary={operatorSummaryWithRelated}
        overviewStatusLevel={overview.status_level}
        attentionNotice={sourceAttentionNotice}
      />

      <main className="dashboard-grid">
        <Section
          eyebrow="Overview"
          title="Overall Trading State"
          sectionId="overall-trading-state"
        >
          <div className="metric-grid">
            {overviewCards.map((card) => (
              <MetricCard key={card.label} {...card} />
            ))}
          </div>
        </Section>

        <Section
          eyebrow="Startup"
          title="Startup Safety Gate"
          wide
          sectionId="startup-safety-gate"
        >
          <div className="card-grid">
            <InfoCard
              cardKey={DASHBOARD_CARD_KEYS.startupCheck}
              label="Startup Check"
              title={startup.available ? "startup_check.json" : "No startup check found"}
              statusLevel={startup.status_level || "MISSING"}
              pairs={[
                { key: "outcome", value: startup.outcome },
                { key: "reason", value: startup.reason },
                { key: "reconcile_reason_code", value: startup.reconcile_reason_code },
                { key: "checked_at", value: startup.checked_at },
              ]}
              flags={startup.attention_flags || []}
            />
            <InfoCard
              label="Reconcile State"
              title={startup.reconcile_reason_code || "Universe and positions"}
              statusLevel={startup.status_level || "MISSING"}
              pairs={[
                {
                  key: "reconcile_reason_message",
                  value: startup.reconcile_reason_message,
                },
                {
                  key: "reconcile_changed_rows",
                  value: startup.reconcile_changed_rows,
                },
                {
                  key: "unresolved_order_count",
                  value: startup.unresolved_order_count,
                },
                {
                  key: "live_position_count",
                  value: startup.live_position_count,
                },
                { key: "universe_exists", value: startup.universe_exists },
                {
                  key: "universe_candidate_count",
                  value: startup.universe_candidate_count,
                },
              ]}
              flags={startup.attention_flags || []}
            />
          </div>
        </Section>

        <Section eyebrow="Sources" title="Source Files" sectionId="source-files">
          <div className="card-grid compact-grid">
            <InfoCard
              label="Daily Ops"
              title="daily_ops_report.json"
              statusLevel={sources.daily_report_available ? "READY" : "MISSING"}
              pairs={[
                { key: "available", value: sources.daily_report_available },
                { key: "path", value: sources.daily_report_path },
              ]}
              flags={[]}
            />
            <InfoCard
              label="Daily Check"
              title="daily_ops_check.json"
              statusLevel={sources.daily_ops_check_available ? "READY" : "MISSING"}
              pairs={[
                { key: "available", value: sources.daily_ops_check_available },
                { key: "path", value: sources.daily_ops_check_path },
              ]}
              flags={[]}
            />
            <InfoCard
              label="Rehearsal"
              title="latest rehearsal_summary.json"
              statusLevel={sources.rehearsal_available ? "READY" : "MISSING"}
              pairs={[
                { key: "available", value: sources.rehearsal_available },
                { key: "path", value: sources.rehearsal_summary_path },
              ]}
              flags={[]}
            />
          </div>
        </Section>

        <Section
          eyebrow="Strategy"
          title="Buy Strategy Selection"
          wide
          sectionId="buy-strategy-selection"
        >
          <div className="strategy-layout">
            <InfoCard
              label="Current Selection"
              title={currentBuyStrategy}
              statusLevel={strategy.status_level || "READY"}
              pairs={[
                { key: "buy_strategy", value: strategy.buy_strategy || "both" },
                {
                  key: "effective_buy_strategy",
                  value: strategy.effective_buy_strategy || "both",
                },
                { key: "run_timing1", value: strategy.run_timing1 },
                { key: "run_timing2", value: strategy.run_timing2 },
                { key: "updated_at", value: strategy.updated_at },
                { key: "applies_to_next_run", value: strategy.applies_to_next_run },
              ]}
              flags={strategy.warning ? [strategy.warning] : []}
            />
            <article className="strategy-panel">
              <div className="strategy-panel-header">
                <div>
                  <p className="info-label">Next Run</p>
                  <h3>Strategy Mode</h3>
                </div>
                <StatusBadge
                  level={apiReadyForStrategy ? "READY" : "MISSING"}
                  label={apiReadyForStrategy ? "API READY" : "API NEEDED"}
                />
              </div>

              <label className="field-label" htmlFor="buy-strategy-select">
                Buy Strategy
              </label>
              <select
                id="buy-strategy-select"
                className="select-input"
                value={selectedBuyStrategy}
                onChange={(event) => setSelectedBuyStrategy(event.target.value)}
              >
                <option value="both">both</option>
                <option value="timing1">timing1</option>
                <option value="timing2">timing2</option>
              </select>

              <label className="field-label" htmlFor="buy-strategy-note-input">
                Note
              </label>
              <input
                id="buy-strategy-note-input"
                className="text-input"
                type="text"
                maxLength="200"
                value={buyStrategyNote}
                onChange={(event) => setBuyStrategyNote(event.target.value)}
                placeholder="Optional reason"
              />

              <button
                className="primary-button"
                type="button"
                onClick={() => {
                  void handleBuyStrategyUpdate();
                }}
                disabled={!apiReadyForStrategy || isBuyStrategyUpdating}
              >
                {isBuyStrategyUpdating ? "Saving..." : "Save Buy Strategy"}
              </button>

              {buyStrategyStatusMessage ? (
                <p className="strategy-message">{buyStrategyStatusMessage}</p>
              ) : null}
            </article>
          </div>
        </Section>

        <Section
          eyebrow="Controls"
          title="Emergency Controls"
          wide
          sectionId="emergency-controls"
        >
          <div className="kill-switch-layout">
            <InfoCard
              label="Kill Switch"
              title={
                controls.kill_switch_enabled ? "Trading Halted" : "Trading Allowed"
              }
              statusLevel={controls.kill_switch_status_level || "MISSING"}
              pairs={[
                { key: "enabled", value: controls.kill_switch_enabled },
                { key: "note", value: controls.kill_switch_note },
                { key: "updated_at", value: controls.kill_switch_updated_at },
              ]}
              flags={controls.kill_switch_enabled ? ["KILL_SWITCH_ENABLED"] : []}
            />
            <article className="kill-switch-panel">
              <div className="kill-switch-panel-header">
                <div>
                  <p className="info-label">Manual Control</p>
                  <h3>{killSwitchEnabled ? "Emergency Stop Active" : "Emergency Stop Ready"}</h3>
                </div>
                <StatusBadge
                  level={apiReadyForKillSwitch ? "READY" : "MISSING"}
                  label={apiReadyForKillSwitch ? "API READY" : "API NEEDED"}
                />
              </div>

              <label className="field-label" htmlFor="kill-switch-note-input">
                Note
              </label>
              <input
                id="kill-switch-note-input"
                className="text-input"
                type="text"
                maxLength="200"
                value={killSwitchNote}
                onChange={(event) => setKillSwitchNote(event.target.value)}
                placeholder="Reason or review note"
              />

              <label className="field-label" htmlFor="resume-confirmation-input">
                Resume Confirm
              </label>
              <input
                id="resume-confirmation-input"
                className="text-input"
                type="text"
                value={resumeConfirmation}
                onChange={(event) => setResumeConfirmation(event.target.value)}
                placeholder="Type RESUME to disable"
              />

              <div className="kill-switch-buttons">
                <button
                  className="danger-button"
                  type="button"
                  onClick={() => {
                    void handleKillSwitchUpdate(true);
                  }}
                  disabled={
                    !apiReadyForKillSwitch ||
                    killSwitchEnabled ||
                    isKillSwitchUpdating
                  }
                >
                  {isKillSwitchUpdating ? "Updating..." : "Enable Kill Switch"}
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => {
                    void handleKillSwitchUpdate(false);
                  }}
                  disabled={disableKillSwitchBlocked}
                >
                  Disable Kill Switch
                </button>
              </div>

              {killSwitchStatusMessage ? (
                <p className="kill-switch-message">{killSwitchStatusMessage}</p>
              ) : null}
            </article>
          </div>
        </Section>

        <Section
          eyebrow="Scan"
          title="Scan And Session State"
          wide
          sectionId="scan-and-session-state"
        >
          <div className="card-grid">
            {scanCards.map((card) => (
              <InfoCard
                key={card.title}
                cardKey={card.cardKey}
                label={card.label}
                title={card.title}
                statusLevel={card.row.status_level || "MISSING"}
                pairs={card.pairs}
                flags={card.row.attention_flags || []}
              />
            ))}
          </div>
        </Section>

        <Section
          eyebrow="Executions"
          title="Direct Execution State"
          wide
          sectionId="direct-execution-state"
        >
          <div className="card-grid">
            {executionCards.map((card) => (
              <InfoCard
                key={card.title}
                cardKey={card.cardKey}
                label={card.label}
                title={card.title}
                statusLevel={card.row?.status_level || "MISSING"}
                pairs={[
                  { key: "stop_reason", value: card.row?.stop_reason },
                  { key: "preview_ready_count", value: card.row?.preview_ready_count },
                  { key: "blocked_count", value: card.row?.blocked_count },
                  { key: "submitted_count", value: card.row?.submitted_count },
                  { key: "acted_count", value: card.row?.acted_count },
                ]}
                flags={card.row?.attention_flags || []}
              />
            ))}
          </div>
        </Section>

        <Section
          eyebrow="Recovery"
          title="Recovery And Maintenance"
          wide
          sectionId="recovery-and-maintenance"
        >
          <div className="card-grid">
            {recoveryCards.map((card) => (
              <InfoCard
                key={card.title}
                cardKey={card.cardKey}
                label={card.label}
                title={card.title}
                statusLevel={card.row?.status_level || "MISSING"}
                pairs={[
                  {
                    key: "manual_recovery_required_count",
                    value: card.row?.manual_recovery_required_count,
                  },
                  { key: "highest_severity", value: card.row?.highest_severity },
                ]}
                flags={card.row?.attention_flags || []}
              />
            ))}
          </div>
        </Section>

        <Section
          eyebrow="Rehearsal"
          title="Mock Validation State"
          wide
          sectionId="mock-validation-state"
        >
          <div className="card-grid">
            <InfoCard
              label="Latest Rehearsal"
              title={rehearsal.available ? "Latest rehearsal result" : "No rehearsal found"}
              statusLevel={rehearsal.status_level || "MISSING"}
              pairs={[
                { key: "overall_outcome", value: rehearsal.overall_outcome },
                { key: "overall_reason", value: rehearsal.overall_reason },
                {
                  key: "step_status_counts",
                  value: rehearsal.step_status_counts
                    ? JSON.stringify(rehearsal.step_status_counts)
                    : "-",
                },
              ]}
              flags={[]}
            />
            <InfoCard
              label="Trading Validation"
              title="Trading Session Preview"
              statusLevel={rehearsal.trading_session?.status_level || "MISSING"}
              pairs={[
                {
                  key: "session_outcome",
                  value: rehearsal.trading_session?.session_outcome,
                },
                {
                  key: "polling_stop_reason",
                  value: rehearsal.trading_session?.polling_stop_reason,
                },
                {
                  key: "timing2_setup_ready",
                  value: rehearsal.trading_session?.timing2_setup_ready,
                },
                {
                  key: "timing2_30s_verified",
                  value: rehearsal.trading_session?.timing2_30s_verified,
                },
              ]}
              flags={rehearsal.trading_session?.attention_flags || []}
            />
          </div>
        </Section>

        <Section
          eyebrow="Actions"
          title="Immediate Action Items"
          wide
          sectionId="immediate-action-items"
        >
          <ActionsSection actions={actions} />
        </Section>
      </main>
    </div>
  );
}

export default App;
