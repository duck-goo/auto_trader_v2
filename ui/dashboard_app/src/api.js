const DEFAULT_API_BASE = import.meta.env.VITE_DASHBOARD_API_BASE || "";

export async function fetchDashboardSnapshot({ tradeDate, signal } = {}) {
  const params = new URLSearchParams();
  if (tradeDate) {
    params.set("trade_date", tradeDate);
  }

  const query = params.toString();
  const url = `${DEFAULT_API_BASE}/api/dashboard-snapshot${query ? `?${query}` : ""}`;
  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload && typeof payload === "object" && payload.error_message) {
        detail = `${detail}: ${payload.error_message}`;
      }
    } catch {
      // Keep the HTTP status detail when error JSON is unavailable.
    }
    throw new Error(detail);
  }

  return response.json();
}

export async function setKillSwitch({
  enabled,
  note,
  tradeDate,
  signal,
} = {}) {
  const response = await fetch(`${DEFAULT_API_BASE}/api/kill-switch`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      enabled,
      note,
      trade_date: tradeDate,
    }),
    signal,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload && typeof payload === "object" && payload.error_message) {
        detail = `${detail}: ${payload.error_message}`;
      }
    } catch {
      // Keep the HTTP status detail when error JSON is unavailable.
    }
    throw new Error(detail);
  }

  return response.json();
}

export async function setBuyStrategy({
  buyStrategy,
  note,
  tradeDate,
  signal,
} = {}) {
  const response = await fetch(`${DEFAULT_API_BASE}/api/buy-strategy`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      buy_strategy: buyStrategy,
      note,
      trade_date: tradeDate,
    }),
    signal,
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload && typeof payload === "object" && payload.error_message) {
        detail = `${detail}: ${payload.error_message}`;
      }
    } catch {
      // Keep the HTTP status detail when error JSON is unavailable.
    }
    throw new Error(detail);
  }

  return response.json();
}
