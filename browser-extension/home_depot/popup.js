// The full dashboard (deals, history, settings, Browser tab) already lives
// in the existing web backend -- this popup is deliberately thin, matching
// HDScanner's popup only in spirit (a quick-glance status view), not
// reimplementing it. Its only real job is pointing at the real dashboard.

const BACKEND_URL = "http://web:8000";

async function refresh() {
  const badge = document.getElementById("backend-status");
  const stateEl = document.getElementById("scanner-state");
  const lastScanEl = document.getElementById("last-scan");
  const link = document.getElementById("dashboard-link");
  link.href = BACKEND_URL;

  try {
    const health = await fetch(`${BACKEND_URL}/api/health`).then((r) => r.json());
    badge.textContent = "connected";
    badge.className = "badge ok";
    if (health.build_time) lastScanEl.title = `backend build: ${health.build_time}`;
  } catch (e) {
    badge.textContent = "unreachable";
    badge.className = "badge bad";
    return;
  }

  try {
    const status = await fetch(`${BACKEND_URL}/api/scan/status`).then((r) => r.json());
    stateEl.textContent = status?.scanner?.state || "unknown";
    const lastRun = status?.recent_runs?.[0];
    lastScanEl.textContent = lastRun
      ? new Date(lastRun.started_at).toLocaleTimeString()
      : "no scans yet";
  } catch (e) {
    stateEl.textContent = "error";
  }
}

refresh();
