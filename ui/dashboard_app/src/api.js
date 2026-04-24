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
