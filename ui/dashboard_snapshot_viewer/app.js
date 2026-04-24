const SAMPLE_SNAPSHOT = {
  trade_date: "2026-04-20",
  generated_at: "2026-04-20T11:14:12+09:00",
  sources: {
    ops_dir: "C:\\python\\auto_trader_v2\\data\\ops\\2026-04-20",
    daily_report_path:
      "C:\\python\\auto_trader_v2\\data\\ops\\2026-04-20\\daily_ops_report.json",
    daily_report_available: true,
    rehearsal_summary_path:
      "C:\\python\\auto_trader_v2\\data\\ops\\2026-04-20\\rehearsal_latest\\rehearsal_summary.json",
    rehearsal_available: true,
  },
  overview: {
    daily_report_available: true,
    status_level: "CRITICAL",
    health_outcome: "CRITICAL",
    highest_severity: "CRITICAL",
    report_outcome: "ATTENTION",
    artifact_count: 7,
    attention_flag_count: 4,
    critical_flag_count: 2,
    warning_flag_count: 2,
    action_required: true,
    top_action_codes: [
      "REVIEW_KILL_SWITCH",
      "REVIEW_SELL_EXECUTION_FAILURE",
      "REVIEW_EXECUTION_RECOVERY",
    ],
  },
  controls: {
    kill_switch_enabled: true,
    kill_switch_note: "manual emergency stop",
    kill_switch_updated_at: "2026-04-20T11:10:00+09:00",
    kill_switch_status_level: "CRITICAL",
  },
  scan: {
    live_preview: {
      available: true,
      status_level: "READY",
      highest_severity: "NONE",
      session_outcome: "COMPLETED",
      session_reason: null,
      preopen_readiness_outcome: "READY",
      preopen_readiness_reason: null,
      polling_started: true,
      polling_exit_code: 0,
      polling_stop_reason: "MAX_CYCLES_REACHED",
      timing2_setup_required: true,
      timing2_setup_ready: true,
      timing2_setup_signal_count: 12,
      attention_flags: [],
    },
    live_execute: {
      available: true,
      status_level: "WARNING",
      highest_severity: "WARNING",
      session_outcome: "POLLING_BLOCKED",
      session_reason: "MAX_DAILY_LOSS_REACHED",
      preopen_readiness_outcome: "READY",
      preopen_readiness_reason: null,
      polling_started: true,
      polling_exit_code: 4,
      polling_stop_reason: "MAX_DAILY_LOSS_REACHED",
      timing2_setup_required: true,
      timing2_setup_ready: true,
      timing2_setup_signal_count: 12,
      attention_flags: ["TRADING_SESSION_EXECUTE_BLOCKED"],
    },
    rehearsal_validation: {
      status_level: "READY",
      session_outcome: "COMPLETED",
      polling_stop_reason: "MAX_CYCLES_REACHED",
      timing2_setup_ready: true,
      timing2_30s_verified: true,
      attention_flags: [],
    },
  },
  executions: {
    buy_preview: {
      available: true,
      status_level: "READY",
      highest_severity: "NONE",
      stop_reason: null,
      blocked_count: 0,
      preview_ready_count: 2,
      submitted_count: 0,
      acted_count: 0,
      attention_flags: [],
    },
    buy_execute: {
      available: false,
      status_level: "MISSING",
      highest_severity: "NONE",
      stop_reason: null,
      blocked_count: null,
      preview_ready_count: null,
      submitted_count: null,
      acted_count: null,
      attention_flags: [],
    },
    sell_preview: {
      available: true,
      status_level: "READY",
      highest_severity: "NONE",
      stop_reason: null,
      blocked_count: 0,
      preview_ready_count: 1,
      submitted_count: 0,
      acted_count: 0,
      attention_flags: [],
    },
    sell_execute: {
      available: true,
      status_level: "CRITICAL",
      highest_severity: "CRITICAL",
      stop_reason: "BROKER_SELL_FAILED",
      blocked_count: 0,
      preview_ready_count: 0,
      submitted_count: 0,
      acted_count: 1,
      attention_flags: ["EXECUTE_SELL_SIGNALS_EXECUTE_FAILED"],
    },
  },
  recovery: {
    order_maintenance_preview: {
      available: true,
      status_level: "WARNING",
      highest_severity: "WARNING",
      manual_recovery_required_count: 2,
      attention_flags: ["MANUAL_RECOVERY_REQUIRED"],
    },
    order_maintenance_execute: {
      available: false,
      status_level: "MISSING",
      highest_severity: "NONE",
      manual_recovery_required_count: null,
      attention_flags: [],
    },
    execution_recovery_review: {
      available: true,
      status_level: "WARNING",
      highest_severity: "WARNING",
      manual_recovery_required_count: 2,
      attention_flags: ["EXECUTION_RECOVERY_REVIEW_HAS_MANUAL_ITEMS"],
    },
  },
  rehearsal: {
    available: true,
    path:
      "C:\\python\\auto_trader_v2\\data\\ops\\2026-04-20\\rehearsal_latest\\rehearsal_summary.json",
    status_level: "READY",
    overall_outcome: "COMPLETED",
    overall_reason: null,
    step_status_counts: {
      ok: 2,
      warning: 0,
      failed: 0,
    },
    trading_session: {
      status_level: "READY",
      session_outcome: "COMPLETED",
      polling_stop_reason: "MAX_CYCLES_REACHED",
      timing2_setup_ready: true,
      timing2_30s_verified: true,
      attention_flags: [],
    },
  },
  actions: {
    required: true,
    count: 3,
    top_action_codes: [
      "REVIEW_KILL_SWITCH",
      "REVIEW_SELL_EXECUTION_FAILURE",
      "REVIEW_EXECUTION_RECOVERY",
    ],
    items: [
      {
        action_code: "REVIEW_KILL_SWITCH",
        severity: "CRITICAL",
        summary: "Kill switch 상태와 note를 먼저 확인하고 자동 실행 재개를 보류하세요.",
        detail: "Kill switch is enabled. note=manual emergency stop",
        suggested_command:
          ".\\venv\\Scripts\\python.exe scripts\\set_kill_switch.py --output .\\data\\ops\\2026-04-20\\kill_switch.status.json",
      },
      {
        action_code: "REVIEW_SELL_EXECUTION_FAILURE",
        severity: "CRITICAL",
        summary: "직접 매도 실행 실패 원인을 확인하고 preview부터 다시 점검하세요.",
        detail: "BROKER_SELL_FAILED",
        suggested_command:
          ".\\venv\\Scripts\\python.exe scripts\\execute_sell_signals.py --trade-date 2026-04-20 --output .\\data\\ops\\2026-04-20\\execute_sell_signals.preview.json",
      },
      {
        action_code: "REVIEW_EXECUTION_RECOVERY",
        severity: "WARNING",
        summary: "수동 체결 복구 대상이 남아 있습니다.",
        detail: "Manual recovery required count=2",
        suggested_command:
          ".\\venv\\Scripts\\python.exe scripts\\run_execution_recovery_workflow.py --trade-date 2026-04-20 --output .\\data\\ops\\2026-04-20\\execution_recovery.review.json --draft-output .\\data\\ops\\2026-04-20\\execution_recovery.draft.json",
      },
    ],
  },
};

const STATUS_LABELS = {
  READY: "READY",
  WARNING: "WARNING",
  CRITICAL: "CRITICAL",
  FAILED: "FAILED",
  MISSING: "MISSING",
  NO_DATA: "NO DATA",
};

const dom = {
  heroStatusCard: document.getElementById("hero-status-card"),
  overviewGrid: document.getElementById("overview-grid"),
  sourcesGrid: document.getElementById("sources-grid"),
  controlsGrid: document.getElementById("controls-grid"),
  scanGrid: document.getElementById("scan-grid"),
  executionGrid: document.getElementById("execution-grid"),
  recoveryGrid: document.getElementById("recovery-grid"),
  rehearsalGrid: document.getElementById("rehearsal-grid"),
  actionsContent: document.getElementById("actions-content"),
  fileInput: document.getElementById("snapshot-file-input"),
  loadSampleButton: document.getElementById("load-sample-button"),
  currentSourceLabel: document.getElementById("current-source-label"),
  currentGeneratedAt: document.getElementById("current-generated-at"),
  errorBanner: document.getElementById("error-banner"),
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function asText(value, fallback = "-") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return String(value);
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function statusClass(level) {
  switch (level) {
    case "READY":
      return "status-ready";
    case "WARNING":
      return "status-warning";
    case "CRITICAL":
      return "status-critical";
    case "FAILED":
      return "status-failed";
    case "NO_DATA":
      return "status-no-data";
    default:
      return "status-missing";
  }
}

function renderStatusPill(level, label) {
  const safeLevel = level || "MISSING";
  const safeLabel = label || STATUS_LABELS[safeLevel] || safeLevel;
  return `<span class="status-pill ${statusClass(safeLevel)}">${escapeHtml(
    safeLabel
  )}</span>`;
}

function renderFlagPills(flags) {
  const rows = asArray(flags);
  if (!rows.length) {
    return `<p class="empty-copy">주의 플래그 없음</p>`;
  }
  return `
    <div class="flag-list">
      ${rows
        .map((flag) => {
          const flagText = asText(flag);
          const flagClass = flagText.includes("CRITICAL")
            ? "flag-critical"
            : "flag-warning";
          return `<span class="flag-pill ${flagClass}">${escapeHtml(flagText)}</span>`;
        })
        .join("")}
    </div>
  `;
}

function renderPairList(pairs) {
  return `
    <div class="pair-list">
      ${pairs
        .map(
          (pair) => `
            <div class="pair-row">
              <span class="pair-key">${escapeHtml(pair.key)}</span>
              <strong class="pair-value">${escapeHtml(asText(pair.value))}</strong>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderMetricCard({ label, value, subtext, statusLevel }) {
  return `
    <article class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(asText(value))}</div>
      <div class="metric-subtext">
        ${statusLevel ? renderStatusPill(statusLevel) : escapeHtml(asText(subtext))}
      </div>
      ${
        subtext && statusLevel
          ? `<div class="metric-subtext">${escapeHtml(asText(subtext))}</div>`
          : ""
      }
    </article>
  `;
}

function renderInfoCard({ label, title, statusLevel, pairs, flags }) {
  return `
    <article class="info-card">
      <div class="info-card-top">
        <div>
          <div class="info-card-label">${escapeHtml(label)}</div>
          <h3 class="info-card-title">${escapeHtml(title)}</h3>
        </div>
        ${renderStatusPill(statusLevel)}
      </div>
      <div class="info-card-body">
        ${renderPairList(pairs)}
        ${renderFlagPills(flags)}
      </div>
    </article>
  `;
}

function normalizeSnapshot(snapshot) {
  const safeSnapshot =
    snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
      ? snapshot
      : {};
  return {
    trade_date: safeSnapshot.trade_date || "-",
    generated_at: safeSnapshot.generated_at || null,
    sources: safeSnapshot.sources && typeof safeSnapshot.sources === "object"
      ? safeSnapshot.sources
      : {},
    overview: safeSnapshot.overview && typeof safeSnapshot.overview === "object"
      ? safeSnapshot.overview
      : {},
    controls: safeSnapshot.controls && typeof safeSnapshot.controls === "object"
      ? safeSnapshot.controls
      : {},
    scan: safeSnapshot.scan && typeof safeSnapshot.scan === "object"
      ? safeSnapshot.scan
      : {},
    executions:
      safeSnapshot.executions && typeof safeSnapshot.executions === "object"
        ? safeSnapshot.executions
        : {},
    recovery:
      safeSnapshot.recovery && typeof safeSnapshot.recovery === "object"
        ? safeSnapshot.recovery
        : {},
    rehearsal:
      safeSnapshot.rehearsal && typeof safeSnapshot.rehearsal === "object"
        ? safeSnapshot.rehearsal
        : {},
    actions: safeSnapshot.actions && typeof safeSnapshot.actions === "object"
      ? safeSnapshot.actions
      : {},
  };
}

function renderHero(snapshot) {
  const overview = snapshot.overview;
  dom.heroStatusCard.innerHTML = `
    <article class="hero-card">
      <div>
        <div class="hero-card-title">Trade Date</div>
        <div class="hero-card-value">${escapeHtml(asText(snapshot.trade_date))}</div>
      </div>
      <div>
        ${renderStatusPill(overview.status_level, overview.health_outcome || overview.status_level)}
        <div class="metric-subtext">
          highest_severity=${escapeHtml(asText(overview.highest_severity))}
        </div>
      </div>
    </article>
  `;
}

function renderOverview(snapshot) {
  const overview = snapshot.overview;
  const metrics = [
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
      statusLevel: null,
    },
    {
      label: "Critical Flags",
      value: overview.critical_flag_count,
      subtext: `warning=${asText(overview.warning_flag_count, "0")}`,
      statusLevel: null,
    },
    {
      label: "Action Required",
      value: overview.action_required ? "YES" : "NO",
      subtext: overview.top_action_codes && overview.top_action_codes.length
        ? overview.top_action_codes.join(", ")
        : "즉시 조치 없음",
      statusLevel: overview.action_required ? "WARNING" : "READY",
    },
  ];

  dom.overviewGrid.innerHTML = metrics.map(renderMetricCard).join("");
}

function renderSources(snapshot) {
  const sources = snapshot.sources;
  dom.sourcesGrid.innerHTML = [
    renderInfoCard({
      label: "Daily Ops",
      title: "일일 운영 리포트",
      statusLevel: sources.daily_report_available ? "READY" : "MISSING",
      pairs: [
        { key: "available", value: sources.daily_report_available },
        { key: "path", value: sources.daily_report_path || "-" },
      ],
      flags: [],
    }),
    renderInfoCard({
      label: "Rehearsal",
      title: "최신 mock rehearsal",
      statusLevel: sources.rehearsal_available ? "READY" : "MISSING",
      pairs: [
        { key: "available", value: sources.rehearsal_available },
        { key: "path", value: sources.rehearsal_summary_path || "-" },
      ],
      flags: [],
    }),
  ].join("");
}

function renderControls(snapshot) {
  const controls = snapshot.controls;
  dom.controlsGrid.innerHTML = renderInfoCard({
    label: "Kill Switch",
    title: controls.kill_switch_enabled ? "자동매매 중단 상태" : "자동매매 허용 상태",
    statusLevel: controls.kill_switch_status_level || "MISSING",
    pairs: [
      { key: "enabled", value: controls.kill_switch_enabled },
      { key: "note", value: controls.kill_switch_note || "-" },
      { key: "updated_at", value: controls.kill_switch_updated_at || "-" },
    ],
    flags: controls.kill_switch_enabled ? ["KILL_SWITCH_ENABLED"] : [],
  });
}

function renderScan(snapshot) {
  const scan = snapshot.scan;
  const cards = [
    {
      label: "Live Preview",
      title: "run_trading_session.preview",
      row: scan.live_preview || {},
    },
    {
      label: "Live Execute",
      title: "run_trading_session.execute",
      row: scan.live_execute || {},
    },
    {
      label: "Rehearsal Validation",
      title: "mock session verification",
      row: scan.rehearsal_validation || {},
    },
  ];

  dom.scanGrid.innerHTML = cards
    .map(({ label, title, row }) =>
      renderInfoCard({
        label,
        title,
        statusLevel: row.status_level || "MISSING",
        pairs: [
          { key: "session_outcome", value: row.session_outcome || "-" },
          { key: "polling_stop_reason", value: row.polling_stop_reason || "-" },
          { key: "timing2_setup_ready", value: row.timing2_setup_ready ?? "-" },
          {
            key: "timing2_30s_verified",
            value:
              row.timing2_30s_verified === undefined
                ? "-"
                : row.timing2_30s_verified,
          },
        ],
        flags: row.attention_flags || [],
      })
    )
    .join("");
}

function renderExecutions(snapshot) {
  const executions = snapshot.executions;
  const rows = [
    ["Buy Preview", "execute_buy_signals.preview", executions.buy_preview],
    ["Buy Execute", "execute_buy_signals.execute", executions.buy_execute],
    ["Sell Preview", "execute_sell_signals.preview", executions.sell_preview],
    ["Sell Execute", "execute_sell_signals.execute", executions.sell_execute],
  ];

  dom.executionGrid.innerHTML = rows
    .map(([label, title, row]) =>
      renderInfoCard({
        label,
        title,
        statusLevel: row?.status_level || "MISSING",
        pairs: [
          { key: "stop_reason", value: row?.stop_reason || "-" },
          { key: "preview_ready_count", value: row?.preview_ready_count ?? "-" },
          { key: "blocked_count", value: row?.blocked_count ?? "-" },
          { key: "submitted_count", value: row?.submitted_count ?? "-" },
          { key: "acted_count", value: row?.acted_count ?? "-" },
        ],
        flags: row?.attention_flags || [],
      })
    )
    .join("");
}

function renderRecovery(snapshot) {
  const recovery = snapshot.recovery;
  const rows = [
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

  dom.recoveryGrid.innerHTML = rows
    .map(([label, title, row]) =>
      renderInfoCard({
        label,
        title,
        statusLevel: row?.status_level || "MISSING",
        pairs: [
          {
            key: "manual_recovery_required_count",
            value: row?.manual_recovery_required_count ?? "-",
          },
          { key: "highest_severity", value: row?.highest_severity ?? "-" },
        ],
        flags: row?.attention_flags || [],
      })
    )
    .join("");
}

function renderRehearsal(snapshot) {
  const rehearsal = snapshot.rehearsal;
  const tradingSession = rehearsal.trading_session || {};

  dom.rehearsalGrid.innerHTML = [
    renderInfoCard({
      label: "Latest Rehearsal",
      title: rehearsal.available ? "가장 최근 mock rehearsal" : "rehearsal 없음",
      statusLevel: rehearsal.status_level || "MISSING",
      pairs: [
        { key: "overall_outcome", value: rehearsal.overall_outcome || "-" },
        { key: "overall_reason", value: rehearsal.overall_reason || "-" },
        {
          key: "step_status_counts",
          value: rehearsal.step_status_counts
            ? JSON.stringify(rehearsal.step_status_counts)
            : "-",
        },
      ],
      flags: [],
    }),
    renderInfoCard({
      label: "Trading Validation",
      title: "Trading Session Preview",
      statusLevel: tradingSession.status_level || "MISSING",
      pairs: [
        { key: "session_outcome", value: tradingSession.session_outcome || "-" },
        {
          key: "polling_stop_reason",
          value: tradingSession.polling_stop_reason || "-",
        },
        {
          key: "timing2_setup_ready",
          value: tradingSession.timing2_setup_ready ?? "-",
        },
        {
          key: "timing2_30s_verified",
          value: tradingSession.timing2_30s_verified ?? "-",
        },
      ],
      flags: tradingSession.attention_flags || [],
    }),
  ].join("");
}

function renderActions(snapshot) {
  const actions = snapshot.actions;
  const items = asArray(actions.items);
  if (!items.length) {
    dom.actionsContent.innerHTML = `<p class="empty-copy">즉시 조치가 필요한 항목이 없습니다.</p>`;
    return;
  }

  dom.actionsContent.innerHTML = `
    <div class="action-stack">
      ${items
        .map(
          (item) => `
            <article class="action-card">
              <div class="action-card-header">
                <h3 class="action-card-title">${escapeHtml(
                  asText(item.action_code)
                )}</h3>
                ${renderStatusPill(item.severity || "WARNING")}
              </div>
              <p class="action-card-summary">${escapeHtml(
                asText(item.summary)
              )}</p>
              ${
                item.detail
                  ? `<p class="action-card-detail">${escapeHtml(asText(item.detail))}</p>`
                  : ""
              }
              ${
                item.suggested_command
                  ? `<div class="action-card-command"><code>${escapeHtml(
                      asText(item.suggested_command)
                    )}</code></div>`
                  : ""
              }
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function renderSnapshot(rawSnapshot, sourceLabel) {
  const snapshot = normalizeSnapshot(rawSnapshot);
  hideError();
  dom.currentSourceLabel.textContent = sourceLabel;
  dom.currentGeneratedAt.textContent = asText(snapshot.generated_at);
  renderHero(snapshot);
  renderOverview(snapshot);
  renderSources(snapshot);
  renderControls(snapshot);
  renderScan(snapshot);
  renderExecutions(snapshot);
  renderRecovery(snapshot);
  renderRehearsal(snapshot);
  renderActions(snapshot);
}

function showError(message) {
  dom.errorBanner.textContent = message;
  dom.errorBanner.classList.remove("hidden");
}

function hideError() {
  dom.errorBanner.textContent = "";
  dom.errorBanner.classList.add("hidden");
}

function loadSampleSnapshot() {
  renderSnapshot(SAMPLE_SNAPSHOT, "샘플 snapshot");
}

function handleFileSelection(event) {
  const [file] = event.target.files || [];
  if (!file) {
    return;
  }

  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(String(reader.result));
      renderSnapshot(parsed, file.name);
    } catch (error) {
      showError(
        `JSON 파싱 실패: ${error instanceof Error ? error.message : String(error)}`
      );
    }
  };
  reader.onerror = () => {
    showError("파일을 읽는 중 오류가 발생했습니다.");
  };
  reader.readAsText(file, "utf-8");
}

dom.fileInput.addEventListener("change", handleFileSelection);
dom.loadSampleButton.addEventListener("click", loadSampleSnapshot);

loadSampleSnapshot();
