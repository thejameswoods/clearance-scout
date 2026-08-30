const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function money(cents) {
  if (cents == null) return "";
  return `$${(cents / 100).toFixed(2)}`;
}

function dealCard(d) {
  const el = document.createElement("div");
  el.className = "deal-card";
  el.innerHTML = `
    ${d.image_url ? `<img src="${d.image_url}" alt="">` : ""}
    <div class="name">${d.product_name}</div>
    <div class="price-row">
      <span class="price">${money(d.price_cents)}</span>
      ${d.list_price_cents ? `<span class="list-price">${money(d.list_price_cents)}</span>` : ""}
      ${d.discount_pct ? `<span class="discount">${d.discount_pct}% off</span>` : ""}
    </div>
    <div class="tags">
      ${d.is_clearance ? `<span class="tag clearance">Clearance</span>` : ""}
      ${d.is_penny ? `<span class="tag penny">Penny</span>` : ""}
    </div>
    <div class="meta">${d.store_name || ""} ${d.aisle ? `· Aisle ${d.aisle}${d.bay ? "/" + d.bay : ""}` : ""}</div>
  `;
  el.addEventListener("click", () => openDeal(d.deal_id));
  return el;
}

async function loadDeals() {
  const params = new URLSearchParams();
  const search = $("#f-search").value.trim();
  const dept = $("#f-department").value;
  const store = $("#f-store").value;
  const clearanceOnly = $("#f-clearance").checked;
  const pennyOnly = $("#f-penny").checked;
  const minDiscount = $("#f-min-discount").value;
  const sort = $("#f-sort").value;

  if (search) params.set("search", search);
  if (dept) params.set("department_id", dept);
  if (store) params.set("store_id", store);
  if (clearanceOnly) params.set("clearance_only", "true");
  if (pennyOnly) params.set("penny_only", "true");
  if (minDiscount) params.set("min_discount_pct", minDiscount);
  params.set("sort", sort);

  const deals = await api(`/api/deals?${params.toString()}`);
  const grid = $("#deal-grid");
  grid.innerHTML = "";
  if (deals.length === 0) {
    grid.innerHTML = `<p style="color:var(--text-dim)">No deals matching these filters yet — trigger a scan or widen your filters.</p>`;
  }
  deals.forEach((d) => grid.appendChild(dealCard(d)));
}

async function loadHistory() {
  const deals = await api(`/api/deals?status=bought&status=dismissed&status=stale&sort=recent`);
  const grid = $("#history-grid");
  grid.innerHTML = "";
  deals.forEach((d) => grid.appendChild(dealCard(d)));
}

async function openDeal(dealId) {
  const d = await api(`/api/deals/${dealId}`);
  const body = $("#modal-body");
  const historyRows = d.price_history
    .map(
      (h) =>
        `<tr><td>${new Date(h.observed_at).toLocaleString()}</td><td>${money(h.price_cents)}</td>` +
        `<td>${h.is_clearance ? "clearance" : ""}${h.is_penny ? " penny" : ""}</td></tr>`
    )
    .join("");
  body.innerHTML = `
    <h2>${d.product_name}</h2>
    ${d.image_url ? `<img src="${d.image_url}" style="max-width:100%;max-height:200px;object-fit:contain">` : ""}
    <p style="color:var(--text-dim)">SKU ${d.retailer_product_id}</p>
    <table class="history-table">
      <thead><tr><th>Observed</th><th>Price</th><th>Signal</th></tr></thead>
      <tbody>${historyRows}</tbody>
    </table>
    <div class="modal-actions">
      <button id="mark-bought">Mark bought</button>
      <button id="dismiss" class="secondary">Dismiss</button>
    </div>
  `;
  $("#mark-bought").addEventListener("click", async () => {
    await api(`/api/deals/${dealId}/bought`, { method: "POST" });
    closeModal();
    refreshActiveTab();
  });
  $("#dismiss").addEventListener("click", async () => {
    await api(`/api/deals/${dealId}/dismiss`, { method: "POST" });
    closeModal();
    refreshActiveTab();
  });
  $("#deal-modal").classList.remove("hidden");
}

function closeModal() {
  $("#deal-modal").classList.add("hidden");
}

async function loadFilterOptions() {
  const [departments, stores] = await Promise.all([
    api("/api/settings/departments"),
    api("/api/settings/stores"),
  ]);
  const deptSel = $("#f-department");
  departments.forEach((dep) => {
    const opt = document.createElement("option");
    opt.value = dep.id;
    opt.textContent = dep.name;
    deptSel.appendChild(opt);
  });
  const storeSel = $("#f-store");
  stores.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.name || s.retailer_slug} (${s.zip_code})`;
    storeSel.appendChild(opt);
  });
}

async function loadSettings() {
  const [retailers, telegram] = await Promise.all([
    api("/api/settings/retailers"),
    api("/api/settings/telegram"),
  ]);
  $("#settings-content").innerHTML = `
    <h3>Retailers</h3>
    <ul>${retailers.map((r) => `<li>${r.display_name} (${r.slug})</li>`).join("") || "<li>None configured yet</li>"}</ul>
    <h3>Telegram</h3>
    <dl>
      <dt>Alerts sent</dt><dd>${telegram.alerts_sent}</dd>
      <dt>Last alert</dt><dd>${telegram.last_alert_at ? new Date(telegram.last_alert_at).toLocaleString() : "never"}</dd>
    </dl>
  `;
}

async function refreshScanStatus() {
  const status = await api("/api/scan/status");
  const badge = $("#scan-state-badge");
  const state = status.scanner.state || "unknown";
  badge.textContent = state;
  badge.className = `badge ${state}`;
}

// noVNC's own client page, proxied same-origin through /vnc/* (see
// web/backend/routes/vnc.py) so there's no separate host/port/login
// prompt — it connects straight back to the dashboard's own websocket
// proxy. `path` here must stay in sync with that proxy's route prefix.
const NOVNC_SRC = "/vnc/vnc.html?autoconnect=true&resize=remote&reconnect=true&path=vnc/websockify";

function openBrowserFrame() {
  const frame = $("#browser-frame");
  if (frame.src === "" || frame.src === "about:blank" || !frame.src) {
    frame.src = NOVNC_SRC;
  }
}

function closeBrowserFrame() {
  // Drops the VNC connection when you're not looking at it, rather than
  // leaving a live remote-control session open in the background.
  const frame = $("#browser-frame");
  if (frame.src && !frame.src.endsWith("about:blank")) {
    frame.src = "about:blank";
  }
}

function refreshActiveTab() {
  const active = $(".tab-btn.active").dataset.tab;
  if (active === "deals") loadDeals();
  if (active === "history") loadHistory();
  if (active === "settings") loadSettings();
  if (active === "browser") {
    openBrowserFrame();
  } else {
    closeBrowserFrame();
  }
}

function setupTabs() {
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".tab-btn").forEach((b) => b.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      refreshActiveTab();
    });
  });
}

function setupFilters() {
  ["#f-search", "#f-department", "#f-store", "#f-clearance", "#f-penny", "#f-min-discount", "#f-sort"].forEach(
    (sel) => $(sel).addEventListener("change", loadDeals)
  );
  $("#f-search").addEventListener("input", () => {
    clearTimeout(window._searchDebounce);
    window._searchDebounce = setTimeout(loadDeals, 300);
  });
}

async function main() {
  setupTabs();
  setupFilters();
  $("#modal-close").addEventListener("click", closeModal);
  $("#scan-now-btn").addEventListener("click", async () => {
    await api("/api/scan/trigger", { method: "POST" });
    refreshScanStatus();
  });

  await loadFilterOptions();
  await loadDeals();
  await refreshScanStatus();
  setInterval(refreshScanStatus, 15000);
  setInterval(() => {
    if ($(".tab-btn.active").dataset.tab === "deals") loadDeals();
  }, 60000);
}

main();
