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

function relTime(dateStr) {
  if (!dateStr) return "";
  const ms = Date.now() - new Date(dateStr).getTime();
  const mins = Math.round(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function mapsLink(address) {
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
}

function priceRangeText(rows) {
  const prices = rows.map((r) => r.price_cents);
  const lo = Math.min(...prices), hi = Math.max(...prices);
  return lo === hi ? money(lo) : `${money(lo)} – ${money(hi)}`;
}

function discountBadge(rows) {
  const pcts = rows.map((r) => r.discount_pct).filter((p) => p != null);
  const anyPenny = rows.some((r) => r.is_penny);
  const cls = anyPenny ? "discount-badge penny" : "discount-badge";
  if (pcts.length === 0) return anyPenny ? `<span class="${cls}">Penny</span>` : "";
  const lo = Math.min(...pcts), hi = Math.max(...pcts);
  const text = lo === hi ? `${lo}%` : `${lo}%–${hi}%`;
  return `<span class="${cls}">${text}</span>`;
}

// Groups per-store deal rows (what /api/deals returns) into one entry per
// product -- price/discount range + location count, matching how the
// dashboard's Deals table displays a product once rather than once per
// store. Detail modal (openProductDetail) drills back into the individual
// per-store rows kept here.
function groupByProduct(rows) {
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.product_id)) groups.set(r.product_id, []);
    groups.get(r.product_id).push(r);
  }
  return groups;
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
  window._dealGroups = groupByProduct(deals);
  renderDealsTable(window._dealGroups);
}

function renderDealsTable(groups) {
  const body = $("#deal-table-body");
  const empty = $("#deal-table-empty");
  body.innerHTML = "";
  empty.hidden = groups.size > 0;

  for (const [productId, rows] of groups) {
    const first = rows[0];
    const addedAt = rows.reduce((min, r) => (r.created_at < min ? r.created_at : min), first.created_at);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${first.image_url ? `<img class="thumb" src="${first.image_url}" alt="">` : ""}</td>
      <td>
        <div class="product-name">${escapeHtml(first.product_name)}</div>
        ${first.department_name ? `<div class="product-dept">${escapeHtml(first.department_name)}</div>` : ""}
      </td>
      <td>${priceRangeText(rows)}</td>
      <td>${discountBadge(rows)}</td>
      <td>${relTime(addedAt)}</td>
      <td>${rows.length} Store${rows.length === 1 ? "" : "s"}</td>
      <td class="action-col">
        <button class="save-btn" data-product="${productId}">Save</button>
        <button class="secondary share-btn" data-product="${productId}">Share Link</button>
      </td>
    `;
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      openProductDetail(productId);
    });
    body.appendChild(tr);
  }

  body.querySelectorAll(".save-btn").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const rows = window._dealGroups.get(Number(btn.dataset.product));
      await Promise.all(rows.map((r) => api(`/api/deals/${r.deal_id}/save`, { method: "POST" })));
      refreshActiveTab();
    })
  );
  body.querySelectorAll(".share-btn").forEach((btn) =>
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      shareProductLink(Number(btn.dataset.product));
    })
  );
}

function shareProductLink(productId) {
  const url = `${location.origin}${location.pathname}?product=${productId}`;
  navigator.clipboard?.writeText(url).catch(() => {});
  window.prompt("Link to this deal (copied to clipboard if supported):", url);
}

function openProductDetail(productId) {
  const rows = window._dealGroups?.get(productId);
  if (!rows) return;
  const first = rows[0];
  const body = $("#modal-body");

  const detailRows = rows
    .map((r) => {
      const stockText = r.stock_quantity != null ? `${r.stock_quantity} left` : (r.fulfillment_state || "");
      const stockClass = r.stock_quantity != null && r.stock_quantity <= 5 ? "low" : "ok";
      return `
        <tr>
          <td>${r.image_url ? `<img class="thumb" src="${r.image_url}" alt="">` : ""}</td>
          <td>
            <div class="product-name">${escapeHtml(r.product_name)}</div>
            ${r.department_name ? `<div class="product-dept">${escapeHtml(r.department_name)}</div>` : ""}
          </td>
          <td>${money(r.price_cents)}${r.list_price_cents ? `<div class="product-dept">was ${money(r.list_price_cents)}</div>` : ""}</td>
          <td>${discountBadge([r])}</td>
          <td>${r.store_address ? `<a class="store-address-link" target="_blank" rel="noopener" href="${mapsLink(r.store_address)}">${escapeHtml(r.store_address)}</a>` : ""}</td>
          <td>${escapeHtml(r.retailer_store_id || "")}${r.aisle ? `<div class="product-dept">Aisle ${escapeHtml(r.aisle)}${r.bay ? "/" + escapeHtml(r.bay) : ""}</div>` : ""}</td>
          <td>${stockText ? `<span class="stock-dot ${stockClass}"></span>${escapeHtml(stockText)}` : ""}</td>
          <td>${relTime(r.created_at)}</td>
          <td class="action-col">
            <button class="row-save-btn" data-deal="${r.deal_id}">Save</button>
            <button class="secondary row-bought-btn" data-deal="${r.deal_id}">Bought</button>
            <button class="secondary row-dismiss-btn" data-deal="${r.deal_id}">Dismiss</button>
          </td>
        </tr>
      `;
    })
    .join("");

  body.innerHTML = `
    <h2>${escapeHtml(first.product_name)}</h2>
    <p class="meta">SKU ${escapeHtml(first.retailer_product_id)}</p>
    <div class="table-wrap">
      <table class="detail-table">
        <thead>
          <tr>
            <th>Image</th><th>Product</th><th>Price</th><th>Discount</th>
            <th>Address</th><th>Store</th><th>Stock</th><th>Added</th><th>Action</th>
          </tr>
        </thead>
        <tbody>${detailRows}</tbody>
      </table>
    </div>
    <div class="modal-actions">
      <button class="secondary" id="modal-share-btn">Share Link</button>
    </div>
  `;

  $("#modal-share-btn").addEventListener("click", () => shareProductLink(productId));
  body.querySelectorAll(".row-save-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api(`/api/deals/${btn.dataset.deal}/save`, { method: "POST" });
      closeModal();
      refreshActiveTab();
    })
  );
  body.querySelectorAll(".row-bought-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api(`/api/deals/${btn.dataset.deal}/bought`, { method: "POST" });
      closeModal();
      refreshActiveTab();
    })
  );
  body.querySelectorAll(".row-dismiss-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api(`/api/deals/${btn.dataset.deal}/dismiss`, { method: "POST" });
      closeModal();
      refreshActiveTab();
    })
  );
  $("#deal-modal").classList.remove("hidden");
}

async function loadShoppingList() {
  const rows = await api("/api/deals?status=saved&sort=recent");
  const container = $("#shopping-list-content");
  container.innerHTML = "";

  if (rows.length === 0) {
    container.innerHTML = `<p class="meta">Nothing saved yet — use "Save" on a deal to add it here.</p>`;
    return;
  }

  const byStore = new Map();
  for (const r of rows) {
    const key = r.store_id;
    if (!byStore.has(key)) byStore.set(key, { name: r.store_name, address: r.store_address, sections: new Map() });
    const store = byStore.get(key);
    const sectionKey = r.department_name || "Other";
    if (!store.sections.has(sectionKey)) store.sections.set(sectionKey, []);
    store.sections.get(sectionKey).push(r);
  }

  for (const [, store] of byStore) {
    const groupEl = document.createElement("div");
    groupEl.className = "shopping-store-group";
    let html = `<h3>${escapeHtml(store.name || "Store")}</h3>`;
    if (store.address) {
      html += `<a class="store-address-link" target="_blank" rel="noopener" href="${mapsLink(store.address)}">${escapeHtml(store.address)}</a>`;
    }
    for (const [section, items] of store.sections) {
      html += `<div class="shopping-section"><h4>${escapeHtml(section)}${items[0].aisle ? ` · Aisle ${escapeHtml(items[0].aisle)}` : ""}</h4>`;
      for (const item of items) {
        html += `
          <div class="shopping-item">
            ${item.image_url ? `<img class="thumb" src="${item.image_url}" alt="">` : ""}
            <div class="name">${escapeHtml(item.product_name)}</div>
            <div class="price">${money(item.price_cents)}</div>
            <button class="secondary shopping-bought-btn" data-deal="${item.deal_id}">Bought</button>
            <button class="secondary shopping-remove-btn" data-deal="${item.deal_id}">Remove</button>
          </div>
        `;
      }
      html += `</div>`;
    }
    groupEl.innerHTML = html;
    container.appendChild(groupEl);
  }

  container.querySelectorAll(".shopping-bought-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api(`/api/deals/${btn.dataset.deal}/bought`, { method: "POST" });
      loadShoppingList();
    })
  );
  container.querySelectorAll(".shopping-remove-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await api(`/api/deals/${btn.dataset.deal}/dismiss`, { method: "POST" });
      loadShoppingList();
    })
  );
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function loadLogs() {
  if ($("#logs-pause").checked) return;
  const view = $("#logs-view");
  const wasScrolledToBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 20;

  const lines = await api("/api/logs");
  view.innerHTML = lines
    .map((l) => {
      const ts = l.timestamp ? new Date(l.timestamp).toLocaleTimeString() : "";
      return `<div class="log-line ${l.level}">[${ts}] ${l.level} ${escapeHtml(l.logger)}: ${escapeHtml(l.message)}</div>`;
    })
    .join("");

  if ($("#logs-autoscroll").checked && wasScrolledToBottom) {
    view.scrollTop = view.scrollHeight;
  }
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
  if (active === "shopping") loadShoppingList();
  if (active === "history") loadHistory();
  if (active === "settings") loadSettings();
  if (active === "logs") loadLogs();
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

  api("/api/health").then((h) => {
    $("#build-time").textContent = h.build_time || "unknown";
  });

  await loadFilterOptions();
  await loadDeals();
  await refreshScanStatus();

  // A "Share Link" deep link (?product=123) opens straight to that item's
  // detail modal instead of just landing on the unfiltered table.
  const sharedProductId = Number(new URLSearchParams(location.search).get("product"));
  if (sharedProductId && window._dealGroups?.has(sharedProductId)) {
    openProductDetail(sharedProductId);
  }

  setInterval(refreshScanStatus, 15000);
  setInterval(() => {
    if ($(".tab-btn.active").dataset.tab === "deals") loadDeals();
  }, 60000);
  // Logs benefit from a much tighter poll than deals -- this is meant to
  // feel close to a live tail while the tab is open, not a periodic
  // refresh. Only fires while the tab is active (loadLogs itself is cheap;
  // the guard just avoids needless requests while looking at another tab).
  setInterval(() => {
    if ($(".tab-btn.active").dataset.tab === "logs") loadLogs();
  }, 3000);
}

main();
