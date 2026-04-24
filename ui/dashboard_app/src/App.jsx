import {
  startTransition,
  useDeferredValue,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { fetchDashboardSnapshot } from "./api";
import { sampleSnapshot } from "./sampleSnapshot";
import { asArray, asText, normalizeSnapshot, statusClassName } from "./snapshot";

const DEFAULT_REFRESH_INTERVAL_SECONDS = 30;
const MIN_REFRESH_INTERVAL_SECONDS = 10;
const MAX_REFRESH_INTERVAL_SECONDS = 300;

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

function InfoCard({ label, title, statusLevel, pairs, flags }) {
  return (
    <article className="info-card">
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

function Section({ eyebrow, title, children, wide = false }) {
  return (
    <section className={`panel section-card ${wide ? "section-wide" : ""}`}>
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
      {items.map((item) => (
        <article className="action-card" key={item.action_code}>
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

function App() {
  const inputId = useId();
  const [snapshot, setSnapshot] = useState(() => normalizeSnapshot(sampleSnapshot));
  const [sourceLabel, setSourceLabel] = useState("Sample snapshot");
  const [errorMessage, setErrorMessage] = useState("");
  const [tradeDate, setTradeDate] = useState(sampleSnapshot.trade_date);
  const [isLoading, setIsLoading] = useState(false);
  const [apiStatus, setApiStatus] = useState("unknown");
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(false);
  const [refreshIntervalSeconds, setRefreshIntervalSeconds] = useState(
    DEFAULT_REFRESH_INTERVAL_SECONDS,
  );
  const [lastSuccessfulRefreshAt, setLastSuccessfulRefreshAt] = useState("");
  const [lastRefreshErrorAt, setLastRefreshErrorAt] = useState("");
  const requestInFlightRef = useRef(false);
  const abortControllerRef = useRef(null);

  const deferredSnapshot = useDeferredValue(snapshot);
  const overview = deferredSnapshot.overview || {};
  const controls = deferredSnapshot.controls || {};
  const scan = deferredSnapshot.scan || {};
  const executions = deferredSnapshot.executions || {};
  const recovery = deferredSnapshot.recovery || {};
  const rehearsal = deferredSnapshot.rehearsal || {};
  const sources = deferredSnapshot.sources || {};
  const actions = deferredSnapshot.actions || {};

  function cancelInFlightRequest() {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    requestInFlightRef.current = false;
    setIsLoading(false);
  }

  async function loadFromServer(nextTradeDate, options = {}) {
    const safeTradeDate = nextTradeDate || tradeDate;
    if (requestInFlightRef.current) {
      return false;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    requestInFlightRef.current = true;
    setIsLoading(true);
    if (options.background) {
      setApiStatus("refreshing");
    }
    try {
      const payload = await fetchDashboardSnapshot({
        tradeDate: safeTradeDate,
        signal: controller.signal,
      });
      setErrorMessage("");
      setApiStatus("connected");
      setLastRefreshErrorAt("");
      setLastSuccessfulRefreshAt(new Date().toISOString());
      startTransition(() => {
        setSnapshot(normalizeSnapshot(payload));
        setSourceLabel(`API snapshot (${safeTradeDate})`);
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

  function loadSample() {
    cancelInFlightRequest();
    setErrorMessage("");
    setApiStatus("unknown");
    setAutoRefreshEnabled(false);
    startTransition(() => {
      setSnapshot(normalizeSnapshot(sampleSnapshot));
      setSourceLabel("Sample snapshot");
    });
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
        setErrorMessage("");
        setApiStatus("unknown");
        setAutoRefreshEnabled(false);
        startTransition(() => {
          setSnapshot(normalizeSnapshot(parsed));
          setSourceLabel(file.name);
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
    return () => {
      abortControllerRef.current?.abort();
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
    ["Buy Preview", "execute_buy_signals.preview", executions.buy_preview],
    ["Buy Execute", "execute_buy_signals.execute", executions.buy_execute],
    ["Sell Preview", "execute_sell_signals.preview", executions.sell_preview],
    ["Sell Execute", "execute_sell_signals.execute", executions.sell_execute],
  ];

  const recoveryCards = [
    [
      "Maintenance Preview",
      "order_maintenance.preview",
      recovery.order_maintenance_preview,
    ],
    [
      "Maintenance Execute",
      "order_maintenance.execute",
      recovery.order_maintenance_execute,
    ],
    [
      "Recovery Review",
      "execution_recovery.review",
      recovery.execution_recovery_review,
    ],
  ];

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

      <section className="panel toolbar-card">
        <div>
          <p className="eyebrow">Input</p>
          <h2>Snapshot Source</h2>
          <p className="toolbar-copy">
            The app keeps file upload support, but it can now query the local
            dashboard API as well.
          </p>
        </div>

        <div className="toolbar-actions">
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
          <button
            className="primary-button"
            type="button"
            onClick={() => {
              void loadFromServer(tradeDate);
            }}
            disabled={isLoading}
          >
            {isLoading ? "Loading..." : "Load From Server"}
          </button>
          <label className="secondary-button file-button" htmlFor={inputId}>
            Load JSON File
          </label>
          <input
            id={inputId}
            className="hidden-input"
            type="file"
            accept=".json,application/json"
            onChange={handleFileChange}
          />
          <button className="secondary-button" type="button" onClick={loadSample}>
            Load Sample
          </button>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              setErrorMessage("");
              setAutoRefreshEnabled((current) => !current);
            }}
            disabled={apiStatus !== "connected" && !autoRefreshEnabled}
          >
            {autoRefreshEnabled ? "Stop Auto Refresh" : "Start Auto Refresh"}
          </button>
        </div>

        <div className="toolbar-meta">
          <div>
            <span className="meta-label">Current Source</span>
            <strong>{sourceLabel}</strong>
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

      <main className="dashboard-grid">
        <Section eyebrow="Overview" title="Overall Trading State">
          <div className="metric-grid">
            {overviewCards.map((card) => (
              <MetricCard key={card.label} {...card} />
            ))}
          </div>
        </Section>

        <Section eyebrow="Sources" title="Source Files">
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

        <Section eyebrow="Controls" title="Emergency Controls" wide>
          <div className="card-grid">
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
          </div>
        </Section>

        <Section eyebrow="Scan" title="Scan And Session State" wide>
          <div className="card-grid">
            {scanCards.map((card) => (
              <InfoCard
                key={card.title}
                label={card.label}
                title={card.title}
                statusLevel={card.row.status_level || "MISSING"}
                pairs={card.pairs}
                flags={card.row.attention_flags || []}
              />
            ))}
          </div>
        </Section>

        <Section eyebrow="Executions" title="Direct Execution State" wide>
          <div className="card-grid">
            {executionCards.map(([label, title, row]) => (
              <InfoCard
                key={title}
                label={label}
                title={title}
                statusLevel={row?.status_level || "MISSING"}
                pairs={[
                  { key: "stop_reason", value: row?.stop_reason },
                  { key: "preview_ready_count", value: row?.preview_ready_count },
                  { key: "blocked_count", value: row?.blocked_count },
                  { key: "submitted_count", value: row?.submitted_count },
                  { key: "acted_count", value: row?.acted_count },
                ]}
                flags={row?.attention_flags || []}
              />
            ))}
          </div>
        </Section>

        <Section eyebrow="Recovery" title="Recovery And Maintenance" wide>
          <div className="card-grid">
            {recoveryCards.map(([label, title, row]) => (
              <InfoCard
                key={title}
                label={label}
                title={title}
                statusLevel={row?.status_level || "MISSING"}
                pairs={[
                  {
                    key: "manual_recovery_required_count",
                    value: row?.manual_recovery_required_count,
                  },
                  { key: "highest_severity", value: row?.highest_severity },
                ]}
                flags={row?.attention_flags || []}
              />
            ))}
          </div>
        </Section>

        <Section eyebrow="Rehearsal" title="Mock Validation State" wide>
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

        <Section eyebrow="Actions" title="Immediate Action Items" wide>
          <ActionsSection actions={actions} />
        </Section>
      </main>
    </div>
  );
}

export default App;
