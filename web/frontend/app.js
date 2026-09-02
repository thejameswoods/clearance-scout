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

// A product link with no store param opens the retailer's default/nearest
// store's price, not the one the deal was actually found at -- confirmed
// live 2026-09-01. `retailer_store_id` is the retailer's own store number
// (e.g. Home Depot's storeId), matching this app's existing ?store=NNNN
// convention for that GraphQL param (see adapters/home_depot/api_client.py).
function productLink(url, retailerStoreId) {
  if (!url) return "#";
  if (!retailerStoreId) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}store=${encodeURIComponent(retailerStoreId)}`;
}

function stockText(row) {
  return row.stock_quantity != null ? `${row.stock_quantity} left` : "";
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

// Deals-tab-specific tag styling (.dv-tag) -- kept separate from
// discountBadge (used by History's dealCard, untouched by this rewrite)
// rather than parameterizing it, so the two tabs' visual languages can't
// bleed into each other by accident.
function dvDiscountTag(rows) {
  const pcts = rows.map((r) => r.discount_pct).filter((p) => p != null);
  const anyPenny = rows.some((r) => r.is_penny);
  if (pcts.length === 0) return anyPenny ? `<span class="dv-tag penny">Penny</span>` : "";
  const lo = Math.min(...pcts), hi = Math.max(...pcts);
  const text = lo === hi ? `${lo}%` : `${lo}%–${hi}%`;
  return `<span class="dv-tag">${text}</span>`;
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

function cheapestRow(rows) {
  return rows.reduce((best, r) => (best == null || r.price_cents < best.price_cents ? r : best), null);
}

const LAST_VISIT_KEY = "deals_last_visit_at";

async function loadDeals() {
  const params = new URLSearchParams();
  const search = $("#f-search").value.trim();
  if (search) params.set("search", search);

  if (scope.retailerSlug) params.set("retailer", scope.retailerSlug);
  if (scope.storeId) params.set("store_id", scope.storeId);
  if (scope.departmentId) {
    if (scope.includeDescendants && scope.departmentName) params.set("department_prefix", scope.departmentName);
    else params.set("department_id", scope.departmentId);
  }
  (STATUS_PARAMS[scope.statusFilter] || STATUS_PARAMS.active).forEach((s) => params.append("status", s));

  if ($("#f-clearance").checked) params.set("clearance_only", "true");
  if ($("#f-penny").checked) params.set("penny_only", "true");
  const minDiscount = $("#f-min-discount").value;
  if (minDiscount) params.set("min_discount_pct", minDiscount);
  const priceMin = $("#f-price-min").value;
  if (priceMin) params.set("price_min_cents", String(Math.round(Number(priceMin) * 100)));
  const priceMax = $("#f-price-max").value;
  if (priceMax) params.set("price_max_cents", String(Math.round(Number(priceMax) * 100)));
  if ($("#f-in-stock").checked) params.set("in_stock_only", "true");
  params.set("sort", $("#f-sort").value);

  const allDeals = await api(`/api/deals?${params.toString()}`);
  const lastVisit = localStorage.getItem(LAST_VISIT_KEY);
  const newCount = lastVisit ? allDeals.filter((d) => d.created_at > lastVisit).length : 0;
  const displayDeals = (window._triageNewOnly && lastVisit)
    ? allDeals.filter((d) => d.created_at > lastVisit)
    : allDeals;

  window._dealGroups = groupByProduct(displayDeals);
  renderDealsTable(window._dealGroups);
  renderNewBar({ newCount, total: allDeals.length, triageActive: window._triageNewOnly && !!lastVisit });
}

function renderStoreLineHtml(productId, rows) {
  if (rows.length === 1) {
    const r = rows[0];
    const stock = stockText(r);
    return `${escapeHtml(r.store_name || r.retailer_store_id)}${r.aisle ? ` · Aisle ${escapeHtml(r.aisle)}${r.bay ? "/" + escapeHtml(r.bay) : ""}` : ""}${stock ? ` · ${stock}` : ""}`;
  }
  return `<span class="dv-expand-toggle" data-toggle-for="${productId}">▾ ${rows.length} of ${rows.length} stores</span>`;
}

function renderDealsTable(groups) {
  const list = $("#deal-list");
  const empty = $("#deal-table-empty");
  list.innerHTML = "";
  empty.hidden = groups.size > 0;

  for (const [productId, rows] of groups) {
    const first = rows[0];
    const cheapest = cheapestRow(rows);
    const addedAt = rows.reduce((min, r) => (r.created_at < min ? r.created_at : min), first.created_at);
    const isDeferred = rows.every((r) => r.status === "deferred");

    const row = document.createElement("div");
    row.className = "deal-row";
    row.dataset.product = productId;
    row.innerHTML = `
      ${first.image_url ? `<img class="dv-thumb" src="${first.image_url}" alt="">` : `<div class="dv-thumb-placeholder"></div>`}
      <div class="dv-body">
        <div class="dv-name">
          <a href="${productLink(cheapest.canonical_url, cheapest.retailer_store_id)}" target="_blank" rel="noopener">${escapeHtml(first.product_name)}</a>
          ${rows.length > 1 ? `<span class="dv-store-badge">↗ ${escapeHtml(cheapest.retailer_store_id || "")}</span>` : ""}
        </div>
        <div class="dv-subline">${first.department_name ? escapeHtml(first.department_name) + " · " : ""}SKU ${escapeHtml(first.retailer_product_id)}</div>
        <div class="dv-store-line">${renderStoreLineHtml(productId, rows)}</div>
        ${isDeferred ? `<div class="dv-deferred-note">${deferredNoteText(cheapest)}</div>` : ""}
      </div>
      <div class="dv-price">
        <div class="now">${priceRangeText(rows)}</div>
        ${first.list_price_cents ? `<div class="was">${money(first.list_price_cents)}</div>` : ""}
      </div>
      <div class="dv-detected">
        <div class="rel">${relTime(addedAt)}</div>
        <div class="abs">${new Date(addedAt).toLocaleString()}</div>
      </div>
      <div class="dv-discount">${dvDiscountTag(rows)}</div>
      <div class="dv-actions">
        <button class="dv-refresh-btn" data-product="${productId}" type="button" title="Check this item across every store, right now">⟳</button>
        ${isDeferred ? `
          <div class="dv-plain-actions">
            <button class="undefer-btn" data-deal="${cheapest.deal_id}">Change</button>
            <button class="not-interested-btn" data-product="${productId}" data-name="${escapeHtml(first.product_name)}">Never</button>
          </div>
        ` : `
          <div class="dv-split">
            <button class="dv-want-btn" data-deal="${cheapest.deal_id}">Want</button>
            <button class="dv-not-interested-btn not-interested-btn" data-product="${productId}" data-name="${escapeHtml(first.product_name)}">Not interested</button>
            <button class="dv-not-yet-caret not-yet-caret" data-deal="${cheapest.deal_id}" data-product-name="${escapeHtml(first.product_name)}" type="button">▾</button>
          </div>
        `}
      </div>
    `;
    row.addEventListener("click", (ev) => {
      if (ev.target.closest("button, a, .dv-expand-toggle")) return;
      openProductDetail(productId);
    });
    list.appendChild(row);

    if (rows.length > 1) {
      const expandRow = document.createElement("div");
      expandRow.className = "dv-expand-row";
      expandRow.hidden = true;
      expandRow.dataset.expandFor = productId;
      expandRow.innerHTML = rows
        .slice()
        .sort((a, b) => a.price_cents - b.price_cents)
        .map(
          (r) => `
          <div class="dv-expand-item">
            <a href="${productLink(r.canonical_url, r.retailer_store_id)}" target="_blank" rel="noopener">${escapeHtml(r.store_name || r.retailer_store_id)} ↗</a>
            <span class="dv-expand-meta">${r.aisle ? `Aisle ${escapeHtml(r.aisle)}${r.bay ? "/" + escapeHtml(r.bay) : ""} · ` : ""}${stockText(r) ? stockText(r) + " · " : ""}detected ${relTime(r.created_at)}</span>
            <span class="dv-expand-price">${money(r.price_cents)}</span>
            <button class="add-store-btn" data-deal="${r.deal_id}">Add to this store's list</button>
          </div>`
        )
        .join("");
      list.appendChild(expandRow);
    }
  }

  wireDealRowActions(list);
}

function deferredNoteText(row) {
  const rule = row.defer_rule;
  if (!rule) return "";
  if (rule.type === "penny") return "Waiting for penny status.";
  if (rule.type === "price") return `Waiting for price to drop below ${money(Math.round(rule.value * 100))}.`;
  return `Waiting for ≥${rule.value}% off.`;
}

function wireDealRowActions(list) {
  list.querySelectorAll(".dv-expand-toggle").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const row = list.querySelector(`.dv-expand-row[data-expand-for="${el.dataset.toggleFor}"]`);
      if (row) row.hidden = !row.hidden;
    })
  );
  list.querySelectorAll(".dv-want-btn, .add-store-btn").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await api(`/api/deals/${btn.dataset.deal}/save`, { method: "POST" });
      loadDeals();
    })
  );
  list.querySelectorAll(".not-interested-btn").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const productId = btn.dataset.product;
      await api(`/api/products/${productId}/dismiss`, { method: "POST" });
      recordLastAction({ type: "dismiss", productId, label: `dismissed: ${btn.dataset.name}` });
      loadDeals();
      loadTree();
    })
  );
  list.querySelectorAll(".undefer-btn").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await api(`/api/deals/${btn.dataset.deal}/undefer`, { method: "POST" });
      loadDeals();
      loadTree();
    })
  );
  list.querySelectorAll(".not-yet-caret").forEach((btn) =>
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      toggleNotYetPanel(btn);
    })
  );
  list.querySelectorAll(".dv-refresh-btn").forEach((btn) =>
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      startProductRefresh(btn);
    })
  );
}

// Queued on the scanner side (scanner/main.py's _refresh_queue), so
// clicking this on several rows in a row is safe -- each just joins the
// queue instead of racing the scanner's one shared browser_ctx.
async function startProductRefresh(btn) {
  if (btn.disabled) return;
  const productId = btn.dataset.product;
  btn.disabled = true;
  btn.classList.add("spinning");
  let res;
  try {
    res = await api(`/api/products/${productId}/refresh`, { method: "POST" });
  } catch (e) {
    btn.disabled = false;
    btn.classList.remove("spinning");
    showRefreshResult(btn, `Failed to queue: ${e.message}`);
    return;
  }
  if (!res.queued && res.error) {
    btn.disabled = false;
    btn.classList.remove("spinning");
    showRefreshResult(btn, `Failed: ${res.error}`);
    return;
  }
  // res.queued === false with no error means it was already queued/running
  // (e.g. a second click) -- just keep polling the existing one.
  pollProductRefresh(btn, productId);
}

async function pollProductRefresh(btn, productId) {
  for (let i = 0; i < 200; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    let status;
    try {
      status = await api(`/api/products/${productId}/refresh-status`);
    } catch (e) {
      break;
    }
    if (status.state === "queued" || status.state === "running") continue;

    btn.disabled = false;
    btn.classList.remove("spinning");
    if (status.state === "done" && status.result) {
      const r = status.result;
      showRefreshResult(btn, `Checked ${r.checked}/${r.stores_total} store(s) -- ${r.hits} hit(s)${r.errors ? `, ${r.errors} error(s)` : ""}.`);
      loadDeals();
      loadTree();
    } else {
      showRefreshResult(btn, "Refresh failed -- see Logs.");
    }
    return;
  }
  btn.disabled = false;
  btn.classList.remove("spinning");
  showRefreshResult(btn, "Still running after several minutes -- check the Logs tab.");
}

function showRefreshResult(btn, text) {
  document.querySelectorAll(".dv-refresh-tooltip").forEach((el) => el.remove());
  const tip = document.createElement("div");
  tip.className = "dv-refresh-tooltip";
  tip.textContent = text;
  btn.closest(".dv-actions").appendChild(tip);
  setTimeout(() => tip.remove(), 6000);
}

function toggleNotYetPanel(caretBtn) {
  const existing = document.querySelector(".not-yet-panel");
  const wasOpenForThis = existing && existing.dataset.deal === caretBtn.dataset.deal;
  closeNotYetPanel();
  if (wasOpenForThis) return;

  const dealId = caretBtn.dataset.deal;
  const panel = document.createElement("div");
  panel.className = "not-yet-panel";
  panel.dataset.deal = dealId;
  panel.innerHTML = `
    <div class="panel-label">Not yet — tell me again when…</div>
    <label class="ny-slider-row"><input type="radio" name="ny-type" value="discount_pct" checked>
      Discount reaches
      <input type="range" id="ny-discount-slider" min="10" max="95" step="5" value="50">
      <span id="ny-discount-value">50%</span> or better
    </label>
    <label><input type="radio" name="ny-type" value="price"> Price drops below
      $<input type="number" id="ny-price" min="0" step="0.01" value="3.00" style="width:70px"></label>
    <label><input type="radio" name="ny-type" value="penny"> It hits penny status</label>
    <label><input type="radio" name="ny-type" value="never"> Never — hide this product for good</label>
    <div class="modal-actions">
      <button id="ny-set" type="button">Set threshold</button>
      <button id="ny-cancel" type="button">Cancel</button>
    </div>
    <div class="footnote">Row leaves the feed and returns as new only if the threshold is met — at any store where it's met.</div>
  `;
  caretBtn.classList.add("open");
  caretBtn.closest(".dv-actions").appendChild(panel);
  panel.addEventListener("click", (ev) => ev.stopPropagation());

  panel.querySelector("#ny-discount-slider").addEventListener("input", (ev) => {
    panel.querySelector("#ny-discount-value").textContent = `${ev.target.value}%`;
  });
  panel.querySelector("#ny-cancel").addEventListener("click", closeNotYetPanel);
  panel.querySelector("#ny-set").addEventListener("click", async () => {
    const type = panel.querySelector('input[name="ny-type"]:checked').value;
    const productName = caretBtn.dataset.productName;
    if (type === "never") {
      const productId = caretBtn.closest(".deal-row").dataset.product;
      await api(`/api/products/${productId}/dismiss`, { method: "POST" });
      recordLastAction({ type: "dismiss", productId, label: `dismissed: ${productName}` });
    } else {
      const value = type === "discount_pct"
        ? Number(panel.querySelector("#ny-discount-slider").value)
        : type === "price" ? Number(panel.querySelector("#ny-price").value) : null;
      await api(`/api/deals/${dealId}/defer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type, value }),
      });
      recordLastAction({ type: "defer", dealId, label: `deferred: ${productName}` });
    }
    closeNotYetPanel();
    loadDeals();
    loadTree();
  });
}

function closeNotYetPanel() {
  document.querySelectorAll(".not-yet-caret.open").forEach((b) => b.classList.remove("open"));
  document.querySelectorAll(".not-yet-panel").forEach((p) => p.remove());
}
document.addEventListener("click", closeNotYetPanel);

function recordLastAction(action) { window._lastAction = action; }

async function undoLastAction() {
  const a = window._lastAction;
  if (!a) return;
  window._lastAction = null;
  if (a.type === "dismiss") await api(`/api/products/${a.productId}/undismiss`, { method: "POST" });
  else if (a.type === "defer") await api(`/api/deals/${a.dealId}/undefer`, { method: "POST" });
  loadDeals();
  loadTree();
}

function renderNewBar({ newCount, total, triageActive }) {
  const bar = $("#new-bar");
  if (newCount === 0 && !window._lastAction) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  const parts = [];
  if (newCount > 0) {
    parts.push(`<span><strong>${newCount} new</strong> · ${total} total</span>`);
    parts.push(`<button type="button" id="triage-new-btn" class="dv-btn dv-btn-outline">${triageActive ? "Show all" : "Triage new only"}</button>`);
  }
  if (window._lastAction) {
    parts.push(`<span>Last ${escapeHtml(window._lastAction.label)} <button type="button" id="undo-btn" class="dv-btn dv-btn-outline">undo</button></span>`);
  }
  bar.innerHTML = parts.join("");
  $("#triage-new-btn")?.addEventListener("click", () => {
    window._triageNewOnly = !window._triageNewOnly;
    loadDeals();
  });
  $("#undo-btn")?.addEventListener("click", undoLastAction);
}

function shareProductLink(productId) {
  const url = `${location.origin}${location.pathname}?product=${productId}`;
  navigator.clipboard?.writeText(url).catch(() => {});
  window.prompt("Link to this deal (copied to clipboard if supported):", url);
}

// This retailer's department tree (window._departmentTree, from loadTree())
// stores each node's full flattened name plus its immediate parent's full
// name (see build_department_hierarchy) -- walking `parent` back to a root
// reconstructs the breadcrumb's per-level labels for the item-detail modal.
// Falls back to the flat department name if the tree hasn't loaded (e.g. a
// product opened via ?product= before the sidebar has fetched).
function departmentBreadcrumbLabels(departmentName) {
  if (!departmentName) return [];
  const tree = window._departmentTree || [];
  const byName = new Map(tree.map((n) => [n.name, n]));
  let node = byName.get(departmentName);
  if (!node) return [departmentName];
  const labels = [];
  while (node) {
    labels.unshift(node.label);
    node = node.parent ? byName.get(node.parent) : null;
  }
  return labels;
}

function sparklineSvg(history) {
  const asc = history.slice().reverse();
  if (asc.length < 2) return "";
  const prices = asc.map((h) => h.price_cents);
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const w = 220, h = 44;
  const points = asc
    .map((p, i) => {
      const x = (i / (asc.length - 1)) * w;
      const y = hi === lo ? h / 2 : h - ((p.price_cents - lo) / (hi - lo)) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return `<svg class="pd-sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${points}" fill="none" stroke="var(--dv-accent)" stroke-width="2"/></svg>`;
}

// Product-wide price_history interleaves every store's observations in one
// observed_at-ordered list -- narrating that directly produces nonsense for
// a multi-store product ("dropped to $3.47 ... deepened to $5.12") since
// it's really two unrelated stores' prices, not one trajectory. Anchoring
// the story to a single store (the modal's cheapest row) keeps it coherent
// and matches the wireframe's single "... 2h ago at Chapel Hill #3612"
// narrative, which only ever tells one store's story.
function historyForStore(history, storeId) {
  const filtered = history.filter((h) => h.store_id === storeId);
  return filtered.length ? filtered : history;
}

// One sentence per distinct price level, oldest first ("Full price $X since
// <date>", then "dropped"/"deepened to $Y (Z%) <date>"), matching the
// wireframe's history narrative, ending with the store it happened at.
function buildHistoryNarrative(storeHistory) {
  if (!storeHistory.length) return "No price history recorded yet.";
  const asc = storeHistory.slice().reverse();
  const storeName = asc[asc.length - 1].store_name;
  const segments = [];
  let prevPrice = null;
  asc.forEach((h, i) => {
    if (prevPrice === h.price_cents) return;
    const dateStr = new Date(h.observed_at).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (i === 0) {
      segments.push(`Full price ${money(h.price_cents)} since ${dateStr}`);
    } else {
      const pct = h.list_price_cents > 0
        ? Math.round((100 * (h.list_price_cents - h.price_cents)) / h.list_price_cents)
        : null;
      const verb = segments.length === 1 ? "dropped" : "deepened";
      segments.push(`${verb} to ${money(h.price_cents)}${pct != null ? ` (${pct}%)` : ""} ${dateStr}`);
    }
    prevPrice = h.price_cents;
  });
  if (segments.length > 0 && storeName) {
    const lastObserved = asc[asc.length - 1].observed_at;
    segments[segments.length - 1] += ` ${relTime(lastObserved)} at ${storeName}`;
  }
  return segments.join(" · ") + ".";
}

function productDetailStatusTag(rows) {
  if (rows.some((r) => r.is_penny)) return `<span class="pd-tag solid">Penny item</span>`;
  if (rows.some((r) => r.is_clearance)) return `<span class="pd-tag solid">Active clearance</span>`;
  return `<span class="pd-tag">Tracked</span>`;
}

function productDetailActionsHtml(productId, cheapest, isDeferred, first) {
  return `
    <button class="dv-refresh-btn" data-product="${productId}" type="button" title="Check this item across every store, right now">⟳</button>
    ${isDeferred ? `
      <div class="dv-plain-actions">
        <button class="undefer-btn" data-deal="${cheapest.deal_id}">Change</button>
        <button class="not-interested-btn" data-product="${productId}" data-name="${escapeHtml(first.product_name)}">Never</button>
      </div>
    ` : `
      <div class="dv-split">
        <button class="dv-want-btn" data-deal="${cheapest.deal_id}">Want</button>
        <button class="dv-not-interested-btn not-interested-btn" data-product="${productId}" data-name="${escapeHtml(first.product_name)}">Not interested</button>
        <button class="dv-not-yet-caret not-yet-caret" data-deal="${cheapest.deal_id}" data-product-name="${escapeHtml(first.product_name)}" type="button">▾</button>
      </div>
    `}
    <button class="secondary" id="pd-share-btn" type="button">Share link</button>
  `;
}

function productDetailStoreCardHtml(r, cheapestDealId) {
  const stock = stockText(r);
  const stockClass = r.stock_quantity != null && r.stock_quantity <= 5 ? "low" : "ok";
  return `
    <div class="pd-store-card">
      <div class="pd-store-card-head">
        <a href="${productLink(r.canonical_url, r.retailer_store_id)}" target="_blank" rel="noopener">${escapeHtml(r.store_name || r.retailer_store_id)} ↗</a>
        ${r.deal_id === cheapestDealId ? `<span class="pd-tag muted">cheapest</span>` : ""}
      </div>
      ${r.store_address ? `<a class="store-address-link" target="_blank" rel="noopener" href="${mapsLink(r.store_address)}">${escapeHtml(r.store_address)}</a>` : ""}
      <div class="pd-store-card-row">
        ${r.aisle ? `<span>Aisle ${escapeHtml(r.aisle)}${r.bay ? "/" + escapeHtml(r.bay) : ""}</span>` : `<span class="dv-text-dim">Aisle unknown</span>`}
        ${stock ? `<span><span class="stock-dot ${stockClass}"></span>${escapeHtml(stock)}</span>` : ""}
        <span class="dv-text-dim">checked ${relTime(r.observed_at)}</span>
      </div>
      <div class="pd-store-card-row">
        <span class="pd-store-card-price">${money(r.price_cents)}</span>
        ${r.list_price_cents ? `<span class="was">${money(r.list_price_cents)}</span>` : ""}
        ${r.discount_pct != null ? `<span class="pd-tag">${r.discount_pct}% off</span>` : r.is_penny ? `<span class="pd-tag penny">Penny</span>` : ""}
      </div>
      <button class="add-store-btn" data-deal="${r.deal_id}">Add to this store's list</button>
    </div>
  `;
}

// The shared modal shell (#deal-modal/.modal-content/#modal-body) is reused
// by three unrelated openers (product detail, history-tab deal, Scan Now) --
// each toggles its own sizing/theme modifier class on/off, so every opener
// must clear the others' first or a leftover class leaks its layout into a
// shape it wasn't designed for.
const MODAL_MODIFIER_CLASSES = ["pd-detail", "scan-now-detail"];
function resetModalModifierClasses() {
  document.querySelector("#deal-modal .modal-content")?.classList.remove(...MODAL_MODIFIER_CLASSES);
}

function openProductDetail(productId) {
  const rows = window._dealGroups?.get(productId);
  if (!rows) return;
  const first = rows[0];
  const cheapest = cheapestRow(rows);
  const isDeferred = rows.every((r) => r.status === "deferred");
  const storeRows = rows.slice().sort((a, b) => a.price_cents - b.price_cents);

  const breadcrumb = [first.retailer_name, ...departmentBreadcrumbLabels(first.department_name)];
  const breadcrumbHtml = breadcrumb
    .map((seg, i) => (i === breadcrumb.length - 1 ? `<strong>${escapeHtml(seg)}</strong>` : escapeHtml(seg)))
    .join(" / ");

  resetModalModifierClasses();
  const modalContent = document.querySelector("#deal-modal .modal-content");
  modalContent.classList.add("pd-detail");
  const body = $("#modal-body");

  body.innerHTML = `
    <div class="pd-topbar">
      <a href="#" id="pd-back">‹ Back to Deals</a>
      <span class="dv-text-dim">·</span>
      <span class="pd-breadcrumb">${breadcrumbHtml}</span>
    </div>
    <div class="pd-header">
      ${first.image_url ? `<img class="pd-photo" src="${first.image_url}" alt="">` : `<div class="pd-photo pd-photo-placeholder"></div>`}
      <div class="pd-header-body">
        <div class="pd-status-tags">
          ${productDetailStatusTag(rows)}
          ${isDeferred ? `<span class="pd-tag muted">${escapeHtml(deferredNoteText(cheapest))}</span>` : ""}
        </div>
        <h2 class="pd-name">${escapeHtml(first.product_name)}</h2>
        <div class="pd-meta">SKU ${escapeHtml(first.retailer_product_id)}</div>
        <div class="pd-price-line">
          <span class="pd-price-now">${priceRangeText(rows)}</span>
          ${first.list_price_cents ? `<span class="was">${money(first.list_price_cents)}</span>` : ""}
          ${dvDiscountTag(rows)}
        </div>
        <div class="dv-actions pd-actions">${productDetailActionsHtml(productId, cheapest, isDeferred, first)}</div>
      </div>
    </div>
    <div class="pd-history">
      <div class="pd-section-label">History</div>
      <div id="pd-history-content" class="pd-history-content">Loading price history…</div>
    </div>
    <div class="pd-section-label">Available at ${rows.length} of ${rows.length} ${escapeHtml(first.retailer_name || "")} store${rows.length === 1 ? "" : "s"} near you</div>
    <div class="pd-stores">
      ${storeRows.map((r) => productDetailStoreCardHtml(r, cheapest.deal_id)).join("")}
    </div>
  `;

  $("#pd-back").addEventListener("click", (ev) => {
    ev.preventDefault();
    closeModal();
  });
  $("#pd-share-btn").addEventListener("click", () => shareProductLink(productId));
  wireDealRowActions(body);
  body.querySelectorAll(".dv-want-btn, .add-store-btn, .not-interested-btn, .undefer-btn").forEach((btn) =>
    btn.addEventListener("click", () => closeModal())
  );

  $("#deal-modal").classList.remove("hidden");

  api(`/api/deals/${cheapest.deal_id}`)
    .then((detail) => {
      const el = $("#pd-history-content");
      if (!el) return;
      const storeHistory = historyForStore(detail.price_history, cheapest.store_id);
      el.innerHTML = `
        ${sparklineSvg(storeHistory)}
        <div class="pd-narrative">${escapeHtml(buildHistoryNarrative(storeHistory))}</div>
      `;
    })
    .catch(() => {
      const el = $("#pd-history-content");
      if (el) el.textContent = "Price history unavailable.";
    });
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
      html += `<div class="shopping-section"><h4>${escapeHtml(section)}</h4>`;
      for (const item of items) {
        const aisleText = item.aisle ? `Aisle ${escapeHtml(item.aisle)}${item.bay ? "/" + escapeHtml(item.bay) : ""}` : "";
        html += `
          <div class="shopping-item">
            ${item.image_url ? `<img class="thumb" src="${item.image_url}" alt="">` : ""}
            <div class="name">
              ${escapeHtml(item.product_name)}
              ${aisleText ? `<div class="product-dept">${aisleText}</div>` : ""}
              ${item.canonical_url ? `<a class="store-address-link" target="_blank" rel="noopener" href="${productLink(item.canonical_url, item.retailer_store_id)}">View item &rarr;</a>` : ""}
            </div>
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
  resetModalModifierClasses();
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

// --- Deals page scope: retailer / store / department tree, synced to the
// URL so a scope is linkable and survives reload (design doc's
// "Scope changes ... should be reflected in the URL"). -----------------

function defaultScope() {
  return {
    retailerSlug: null, storeId: null, departmentId: null, departmentName: null,
    includeDescendants: true, statusFilter: "active",
  };
}
let scope = defaultScope();

function loadScopeFromUrl() {
  const p = new URLSearchParams(location.search);
  scope.retailerSlug = p.get("retailer") || null;
  scope.storeId = p.get("store_id") || null;
  scope.departmentId = p.get("department_id") || null;
  scope.departmentName = p.get("department_name") || null;
  scope.includeDescendants = p.get("descendants") !== "0";
  scope.statusFilter = p.get("status") || "active";
}

function saveScopeToUrl() {
  const p = new URLSearchParams();
  if (scope.retailerSlug) p.set("retailer", scope.retailerSlug);
  if (scope.storeId) p.set("store_id", scope.storeId);
  if (scope.departmentId) {
    p.set("department_id", scope.departmentId);
    if (scope.departmentName) p.set("department_name", scope.departmentName);
  }
  if (!scope.includeDescendants) p.set("descendants", "0");
  if (scope.statusFilter !== "active") p.set("status", scope.statusFilter);
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

const STATUS_PARAMS = {
  active: ["new", "active"],
  waiting: ["deferred"],
  all: ["new", "active", "deferred"],
};

function retailerExpandKey(slug) { return `deals_retailer_expanded_${slug}`; }
function deptExpandKey(name) { return `deals_dept_expanded_${name}`; }

async function loadTree() {
  const params = new URLSearchParams();
  if (scope.retailerSlug) params.set("retailer", scope.retailerSlug);
  if (scope.storeId) params.set("store_id", scope.storeId);
  if (scope.departmentId) {
    if (scope.includeDescendants && scope.departmentName) params.set("department_prefix", scope.departmentName);
    else params.set("department_id", scope.departmentId);
  }

  const tree = await api(`/api/deals/tree?${params.toString()}`);
  if (!scope.retailerSlug) scope.retailerSlug = tree.selected_retailer;
  window._retailerTree = tree.retailers;
  renderRetailerTree(tree.retailers);
  renderDepartmentTree(tree.departments);
  renderStatusBar(tree.status_counts);
  renderScopeBar();
}

function renderRetailerTree(retailers) {
  const el = $("#retailer-tree");
  el.innerHTML = "";
  for (const r of retailers) {
    // Expansion is purely the toggle's own state -- independent of
    // selection, and defaults to collapsed (nothing in localStorage yet).
    // Selecting a retailer (clicking its name, not the toggle) scopes to
    // "all stores" for it directly; there's no separate "All N stores" row.
    const expanded = localStorage.getItem(retailerExpandKey(r.slug)) === "1";
    const isSelectedRetailer = r.slug === scope.retailerSlug;

    const retailerRow = document.createElement("div");
    retailerRow.className = `tree-row ${isSelectedRetailer && !scope.storeId ? "selected" : ""} ${r.total === 0 ? "zero" : ""}`;
    retailerRow.innerHTML = `<span class="tree-toggle">${expanded ? "\u25BE" : "\u25B8"}</span><span class="tree-name">${escapeHtml(r.display_name)}</span><span class="tree-count">${r.total}</span>`;
    retailerRow.querySelector(".tree-toggle").addEventListener("click", (ev) => {
      ev.stopPropagation();
      localStorage.setItem(retailerExpandKey(r.slug), expanded ? "0" : "1");
      renderRetailerTree(retailers);
    });
    retailerRow.addEventListener("click", () => selectRetailer(r.slug));
    el.appendChild(retailerRow);
    if (!expanded) continue;

    for (const s of r.stores) {
      const storeSelected = isSelectedRetailer && String(scope.storeId) === String(s.store_id);
      const storeRow = document.createElement("div");
      storeRow.className = `tree-row indent ${storeSelected ? "selected" : ""} ${s.open_count === 0 ? "zero" : ""}`;
      storeRow.innerHTML = `<span class="tree-name">${escapeHtml(s.name || s.retailer_store_id)}</span><span class="tree-count">${s.open_count}</span>`;
      storeRow.addEventListener("click", () => selectStore(r.slug, s.store_id));
      el.appendChild(storeRow);
    }
  }
}

function selectRetailer(slug) {
  scope.retailerSlug = slug;
  scope.storeId = null;
  scope.departmentId = null;
  scope.departmentName = null;
  saveScopeToUrl();
  loadTree();
  loadDeals();
}

function selectStore(slug, storeId) {
  scope.retailerSlug = slug;
  scope.storeId = storeId;
  saveScopeToUrl();
  loadTree();
  loadDeals();
}

function renderDepartmentTree(departments) {
  window._departmentTree = departments;
  const retailerName = window._retailerTree?.find((r) => r.slug === scope.retailerSlug)?.display_name;
  $("#dept-tree-label").textContent = retailerName ? `${retailerName.toUpperCase()} DEPARTMENTS` : "DEPARTMENTS";

  const filterText = ($("#dept-filter").value || "").toLowerCase().trim();
  const byName = new Map(departments.map((d) => [d.name, d]));
  let visibleNames = null; // null means "everything" (no filter active)
  if (filterText) {
    visibleNames = new Set(departments.filter((d) => d.label.toLowerCase().includes(filterText)).map((d) => d.name));
    for (const name of [...visibleNames]) {
      let p = byName.get(name)?.parent;
      while (p) { visibleNames.add(p); p = byName.get(p)?.parent; }
    }
  }

  const isCollapsed = (d) => {
    if (filterText) return false; // filtering force-expands everything that matches
    let p = d.parent;
    while (p) {
      if (localStorage.getItem(deptExpandKey(p)) !== "1") return true;
      p = byName.get(p)?.parent;
    }
    return false;
  };

  const el = $("#department-tree");
  el.innerHTML = "";
  for (const d of departments) {
    if (d.count === 0) continue; // an empty department is never useful to browse into
    if (visibleNames && !visibleNames.has(d.name)) continue;
    if (isCollapsed(d)) continue;
    const hasChildren = departments.some((x) => x.parent === d.name && x.count > 0);
    const expanded = !!filterText || localStorage.getItem(deptExpandKey(d.name)) === "1";
    const selected = String(scope.departmentId) === String(d.id);

    const row = document.createElement("div");
    row.className = `tree-row ${selected ? "selected" : ""} ${d.count === 0 ? "zero" : ""}`;
    row.style.paddingLeft = `${6 + d.depth * 14}px`;
    row.innerHTML = `<span class="tree-toggle">${hasChildren ? (expanded ? "\u25BE" : "\u25B8") : ""}</span><span class="tree-name">${escapeHtml(d.label)}</span><span class="tree-count">${d.count}</span>`;
    if (hasChildren) {
      row.querySelector(".tree-toggle").addEventListener("click", (ev) => {
        ev.stopPropagation();
        localStorage.setItem(deptExpandKey(d.name), expanded ? "0" : "1");
        renderDepartmentTree(departments);
      });
    }
    row.addEventListener("click", () => selectDepartment(d));
    el.appendChild(row);
  }
}

function selectDepartment(d) {
  scope.departmentId = d.id;
  scope.departmentName = d.name;
  saveScopeToUrl();
  loadTree();
  loadDeals();
}

function renderScopeBar() {
  const retailer = window._retailerTree?.find((r) => r.slug === scope.retailerSlug);
  const parts = [retailer ? escapeHtml(retailer.display_name) : "All retailers"];
  if (scope.storeId) {
    const store = retailer?.stores.find((s) => String(s.store_id) === String(scope.storeId));
    parts.push(escapeHtml(store?.name || "store"));
  } else {
    parts.push("all stores");
  }
  let breadcrumb = parts.join(" \u00B7 ");
  if (scope.departmentName) breadcrumb += ` / <strong>${escapeHtml(scope.departmentName)}</strong>`;
  $("#scope-breadcrumb").innerHTML = breadcrumb;

  const toggle = $("#scope-descendants-toggle");
  toggle.textContent = `incl. sub-departments ${scope.includeDescendants ? "\u2713" : ""}`;
  toggle.classList.toggle("on", scope.includeDescendants);
  toggle.hidden = !scope.departmentId;
}

function renderStatusBar(counts) {
  const tags = [
    { key: "active", label: `Active clearance ${counts.active}` },
    { key: "waiting", label: `Waiting for deeper cut ${counts.waiting}` },
    { key: "all", label: `All ${counts.all}` },
  ];
  const el = $("#status-bar");
  el.innerHTML = tags
    .map((t) => `<button type="button" class="status-tag ${scope.statusFilter === t.key ? "selected" : ""}" data-status="${t.key}">${t.label}</button>`)
    .join("");
  el.querySelectorAll(".status-tag").forEach((btn) =>
    btn.addEventListener("click", () => {
      scope.statusFilter = btn.dataset.status;
      saveScopeToUrl();
      renderStatusBar(counts);
      loadDeals();
    })
  );
}

function setupScopeBar() {
  $("#scope-descendants-toggle").addEventListener("click", () => {
    scope.includeDescendants = !scope.includeDescendants;
    saveScopeToUrl();
    renderScopeBar();
    loadTree();
    loadDeals();
  });
  $("#dept-filter").addEventListener("input", () => renderDepartmentTree(window._departmentTree || []));
}

function scanConfigFormHtml(scanConfig) {
  const joined = (arr) => (arr && arr.length ? arr.join(", ") : "");
  return `
    <form id="scan-config-form" class="settings-form">
      <label>Retailers <input type="text" value="${escapeHtml((scanConfig.retailers || []).join(", "))}" disabled title="Not yet editable from here -- set via RETAILERS in .env"></label>
      <label>ZIP code <input type="text" id="cfg-zip" value="${escapeHtml(scanConfig.zip_code || "")}" required></label>
      <label>Radius (miles) <input type="number" id="cfg-radius" min="1" step="0.5" value="${scanConfig.radius_miles ?? ""}" required></label>
      <label>Watched departments <input type="text" id="cfg-departments" placeholder="blank = all departments" value="${escapeHtml(joined(scanConfig.watched_departments))}"></label>
      <label>Watch keywords <input type="text" id="cfg-keywords" placeholder="blank = all products" value="${escapeHtml(joined(scanConfig.watch_keywords))}"></label>
      <label>Scan interval (min, 0 = manual only) <input type="number" id="cfg-interval" min="0" step="1" value="${scanConfig.scan_interval_minutes ?? ""}"></label>
      <label>Product list cache (hours) <input type="number" id="cfg-cache-hours" min="0" step="1" value="${scanConfig.product_list_cache_hours ?? ""}"></label>
      <div class="modal-actions">
        <button type="submit">Save</button>
        <span id="scan-config-save-status" class="meta"></span>
      </div>
    </form>
  `;
}

function setupScanConfigForm() {
  const form = $("#scan-config-form");
  if (!form) return;
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const statusEl = $("#scan-config-save-status");
    statusEl.textContent = "Saving…";
    try {
      await api("/api/settings/scan-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          zip_code: $("#cfg-zip").value.trim(),
          radius_miles: Number($("#cfg-radius").value),
          watched_departments: $("#cfg-departments").value.trim(),
          watch_keywords: $("#cfg-keywords").value.trim(),
          scan_interval_minutes: Number($("#cfg-interval").value),
          product_list_cache_hours: Number($("#cfg-cache-hours").value),
        }),
      });
      statusEl.textContent = "Saved -- takes effect on the next scan.";
    } catch (e) {
      statusEl.textContent = `Save failed: ${e.message}`;
    }
  });
}

function dataToolsHtml(missingCount) {
  const missingLabel = missingCount == null ? "unknown" : missingCount;
  return `
    <h3>Data tools</h3>
    <div class="data-tools">
      <div class="data-tool">
        <div>
          <strong>Force product re-list</strong>
          <p class="meta">Clears the product-list cache for every department so the next scan re-lists from the retailer instead of serving cached SKUs -- use if new products stop showing up (see "Product list cache" above).</p>
        </div>
        <button id="tool-relist" class="secondary" type="button">Reset cache</button>
      </div>
      <div class="data-tool">
        <div>
          <strong>Recompute deal statuses</strong>
          <p class="meta">Re-derives Active/Stale from each deal's latest price check. Leaves Bought/Dismissed/Saved alone unless the box below is checked.</p>
          <label class="checkbox-label"><input type="checkbox" id="tool-recompute-override"> Include Bought/Dismissed/Saved (repair only -- can undo a real action)</label>
        </div>
        <button id="tool-recompute" class="danger" type="button">Recompute</button>
      </div>
      <div class="data-tool">
        <div>
          <strong>Repair missing data</strong>
          <p class="meta">Backfills image/product-link/aisle/bay for deals missing them (${missingLabel} right now) -- re-fetches from the retailer regardless of whether the item is still on clearance, unlike a normal scan (which only enriches a live hit). Runs on the scanner's real browser session, so it takes a little while and is capped per run.</p>
          <label>Max items this run <input type="number" id="tool-repair-limit" min="1" step="1" value="50" style="width:80px"></label>
        </div>
        <button id="tool-repair" class="secondary" type="button">Repair</button>
      </div>
    </div>
    <p id="data-tools-status" class="meta"></p>
  `;
}

function setupDataTools() {
  const statusEl = $("#data-tools-status");

  $("#tool-relist").addEventListener("click", async () => {
    if (!confirm("Clear the product-list cache for every department? The next scan re-lists products from scratch instead of using the cache (slower, more requests to the retailer).")) return;
    statusEl.textContent = "Resetting…";
    try {
      const res = await api("/api/admin/reset-department-cache", { method: "POST" });
      statusEl.textContent = `Done -- ${res.reset} department(s) will re-list on the next scan.`;
    } catch (e) {
      statusEl.textContent = `Failed: ${e.message}`;
    }
  });

  $("#tool-recompute").addEventListener("click", async () => {
    const override = $("#tool-recompute-override").checked;
    const msg = override
      ? "Recompute ALL deal statuses from their latest price check, including Bought/Dismissed/Saved? This can undo a real action someone took on purpose."
      : "Recompute Active/Stale deal statuses from each deal's latest price check?";
    if (!confirm(msg)) return;
    statusEl.textContent = "Recomputing…";
    try {
      const res = await api(`/api/admin/recompute-deal-statuses?override_manual=${override}`, { method: "POST" });
      statusEl.textContent = `Done -- ${res.updated} deal(s) updated.`;
    } catch (e) {
      statusEl.textContent = `Failed: ${e.message}`;
    }
  });

  $("#tool-repair").addEventListener("click", async () => {
    const limit = Number($("#tool-repair-limit").value) || 50;
    if (!confirm(`Fetch fresh product detail for up to ${limit} deal(s) missing image/link/aisle/bay data? This hits the real retailer API through the scanner's browser session -- it won't run alongside a scan, and it'll pause the next scheduled scan until it's done.`)) return;
    try {
      const res = await api(`/api/admin/repair-missing-data?limit=${limit}`, { method: "POST" });
      if (!res.triggered) {
        statusEl.textContent = `Failed to start: ${res.error || "unknown error"}`;
        return;
      }
    } catch (e) {
      statusEl.textContent = `Failed to start: ${e.message}`;
      return;
    }
    statusEl.textContent = "Repairing…";
    pollRepairStatus(statusEl);
  });
}

async function pollRepairStatus(statusEl) {
  for (let i = 0; i < 200; i++) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let status;
    try {
      status = await api("/api/admin/repair-missing-data/status");
    } catch (e) {
      statusEl.textContent = `Lost track of progress: ${e.message}`;
      return;
    }
    if (status.state === "running") continue;
    const result = status.last_run_result;
    const summary = result && typeof result === "object"
      ? Object.values(result)[0]
      : result;
    if (summary && typeof summary === "object") {
      statusEl.textContent = `Done -- attempted ${summary.attempted}, ${summary.images_filled} image(s), ${summary.canonical_filled} link(s), ${summary.aisle_bay_filled} aisle/bay filled, ${summary.errors} still missing.`;
    } else {
      statusEl.textContent = `Finished (${summary || "no result"}).`;
    }
    return;
  }
  statusEl.textContent = "Still running after 10 minutes -- check the Logs tab.";
}

async function loadSettings() {
  const [retailers, telegram, scanConfig, missing] = await Promise.all([
    api("/api/settings/retailers"),
    api("/api/settings/telegram"),
    api("/api/settings/scan-config"),
    api("/api/admin/repair-missing-data/count").catch(() => null),
  ]);
  $("#settings-content").innerHTML = `
    <h3>Scan configuration</h3>
    ${scanConfig.error ? `<p class="meta">${escapeHtml(scanConfig.error)}</p>` : scanConfigFormHtml(scanConfig)}
    <h3>Retailers</h3>
    ${retailers.length ? `
      <div class="settings-form" style="max-width:420px">
        ${retailers.map((r) => `
          <label>${escapeHtml(r.display_name)} (${escapeHtml(r.slug)}) -- minimum discount %
            <span style="display:flex;gap:8px;align-items:center">
              <input type="number" min="0" max="100" step="1" class="retailer-min-discount" data-retailer="${r.id}"
                     value="${r.min_discount_pct ?? ""}" placeholder="no floor" style="max-width:100px">
              <button type="button" class="secondary retailer-min-discount-save" data-retailer="${r.id}">Save</button>
              <span class="meta retailer-min-discount-status" data-retailer="${r.id}"></span>
            </span>
          </label>
        `).join("")}
      </div>
    ` : `<p class="meta">None configured yet</p>`}
    <h3>Telegram</h3>
    <dl>
      <dt>Alerts sent</dt><dd>${telegram.alerts_sent}</dd>
      <dt>Last alert</dt><dd>${telegram.last_alert_at ? new Date(telegram.last_alert_at).toLocaleString() : "never"}</dd>
    </dl>
    ${dataToolsHtml(missing ? missing.missing : null)}
  `;
  setupScanConfigForm();
  setupDataTools();
  setupRetailerMinDiscount();
}

function setupRetailerMinDiscount() {
  $$(".retailer-min-discount-save").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const retailerId = btn.dataset.retailer;
      const input = $(`.retailer-min-discount[data-retailer="${retailerId}"]`);
      const statusEl = $(`.retailer-min-discount-status[data-retailer="${retailerId}"]`);
      const raw = input.value.trim();
      statusEl.textContent = "Saving…";
      try {
        await api(`/api/settings/retailers/${retailerId}/min-discount`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ min_discount_pct: raw === "" ? null : Number(raw) }),
        });
        statusEl.textContent = raw === "" ? "Saved -- no floor." : "Saved.";
      } catch (e) {
        statusEl.textContent = `Failed: ${e.message}`;
      }
    })
  );
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

// --- Scan Now dialog (wireframe screen 4b): pick a subset of stores to
// scan instead of always scanning everything. Reuses the shared modal
// shell (see resetModalModifierClasses above); state (which stores are
// checked) lives in window._scanNowSelected, rebuilt fresh each time the
// dialog opens rather than persisted across opens.

function scanNowStoreRowHtml(store, checked) {
  const distance = store.distance_miles != null ? `${store.distance_miles.toFixed(1)} mi` : "distance unknown";
  const scanned = store.last_scanned_at ? `scanned ${relTime(store.last_scanned_at)}` : "never scanned";
  return `
    <label class="sn-store-row">
      <input type="checkbox" class="sn-store-check" data-store-id="${store.store_id}" ${checked ? "checked" : ""} />
      <span class="sn-store-name">${escapeHtml(store.name || store.retailer_store_id)}</span>
      <span class="sn-store-meta">${distance} · ${scanned}</span>
    </label>
  `;
}

function scanNowRetailerBlockHtml(retailer) {
  if (!retailer.stores.length) {
    return `
      <div class="sn-retailer sn-retailer-disabled">
        <div class="sn-retailer-header"><strong>${escapeHtml(retailer.display_name)}</strong></div>
        <p class="sn-not-connected">Not connected — add in Settings.</p>
      </div>
    `;
  }
  const selectedCount = retailer.stores.filter((s) => window._scanNowSelected.has(s.store_id)).length;
  const allChecked = selectedCount === retailer.stores.length;
  const deptLabel = `${retailer.watched_department_count} department${retailer.watched_department_count === 1 ? "" : "s"} watched`;
  return `
    <div class="sn-retailer" data-retailer-id="${retailer.retailer_id}">
      <label class="sn-retailer-header">
        <input type="checkbox" class="sn-retailer-all" data-retailer-id="${retailer.retailer_id}" ${allChecked ? "checked" : ""} />
        <strong>${escapeHtml(retailer.display_name)}</strong>
        <span class="sn-count">${selectedCount} of ${retailer.stores.length} stores selected</span>
      </label>
      <div class="sn-stores">
        ${retailer.stores.map((s) => scanNowStoreRowHtml(s, window._scanNowSelected.has(s.store_id))).join("")}
      </div>
      <div class="sn-dept-scope">
        ${deptLabel} <a href="#" class="sn-edit-link">Edit</a>
      </div>
    </div>
  `;
}

function computeScanNowEstimate(scope) {
  let totalStores = 0;
  let totalSeconds = 0;
  let maxDeptCount = 0;
  for (const retailer of scope.retailers) {
    const selected = retailer.stores.filter((s) => window._scanNowSelected.has(s.store_id));
    if (!selected.length) continue;
    totalStores += selected.length;
    totalSeconds += selected.length * retailer.watched_department_count * scope.avg_seconds_per_store_department;
    maxDeptCount = Math.max(maxDeptCount, retailer.watched_department_count);
  }
  return { minutes: totalStores ? Math.max(1, Math.round(totalSeconds / 60)) : 0, stores: totalStores, departments: maxDeptCount };
}

function renderScanNowFooter(scope) {
  const est = computeScanNowEstimate(scope);
  const estimateEl = $("#sn-estimate");
  estimateEl.textContent = est.stores
    ? `Estimated ~${est.minutes} min · ${est.stores} store${est.stores === 1 ? "" : "s"} · ${est.departments} department${est.departments === 1 ? "" : "s"}`
    : "Select at least one store to scan.";
  $("#sn-start").disabled = est.stores === 0;
}

function renderScanNowDialog(scope) {
  const body = $("#modal-body");
  body.innerHTML = `
    <h2>Scan now</h2>
    <p class="meta">Choose which stores to check. Department scope follows your Settings watch list.</p>
    <div class="sn-retailers">${scope.retailers.map(scanNowRetailerBlockHtml).join("")}</div>
    <div class="sn-footer">
      <span id="sn-estimate" class="sn-estimate"></span>
      <div class="sn-footer-actions">
        <button id="sn-cancel" class="secondary" type="button">Cancel</button>
        <button id="sn-start" type="button">Start scan</button>
      </div>
    </div>
  `;

  // .checked alone can't distinguish "0 selected" from "some, but not all,
  // selected" -- both render as unchecked -- so a partial retailer would
  // otherwise look identical to an empty one and a click would silently
  // select everything instead of the expected "finish selecting the
  // rest." .indeterminate is a JS-only DOM property (no HTML attribute),
  // so it has to be set here after the checkbox already exists.
  body.querySelectorAll(".sn-retailer-all").forEach((el) => {
    const retailer = scope.retailers.find((r) => String(r.retailer_id) === el.dataset.retailerId);
    const selectedCount = retailer.stores.filter((s) => window._scanNowSelected.has(s.store_id)).length;
    el.indeterminate = selectedCount > 0 && selectedCount < retailer.stores.length;
  });

  body.querySelectorAll(".sn-store-check").forEach((el) =>
    el.addEventListener("change", () => {
      const storeId = Number(el.dataset.storeId);
      if (el.checked) window._scanNowSelected.add(storeId);
      else window._scanNowSelected.delete(storeId);
      renderScanNowDialog(scope);
    })
  );
  body.querySelectorAll(".sn-retailer-all").forEach((el) =>
    el.addEventListener("change", () => {
      const retailer = scope.retailers.find((r) => String(r.retailer_id) === el.dataset.retailerId);
      retailer.stores.forEach((s) => {
        if (el.checked) window._scanNowSelected.add(s.store_id);
        else window._scanNowSelected.delete(s.store_id);
      });
      renderScanNowDialog(scope);
    })
  );
  body.querySelectorAll(".sn-edit-link").forEach((el) =>
    el.addEventListener("click", (ev) => {
      ev.preventDefault();
      closeModal();
      $('.tab-btn[data-tab="settings"]').click();
    })
  );
  $("#sn-cancel").addEventListener("click", closeModal);
  $("#sn-start").addEventListener("click", async () => {
    const btn = $("#sn-start");
    btn.disabled = true;
    btn.textContent = "Starting…";
    // store_ids goes as repeated query params -- routes/scan.py's
    // /trigger endpoint (like the scanner's own /trigger-scan it proxies
    // to) has no request body, only query params.
    const params = new URLSearchParams();
    window._scanNowSelected.forEach((id) => params.append("store_ids", id));
    await api(`/api/scan/trigger?${params.toString()}`, { method: "POST" }).catch(() => {});
    closeModal();
    refreshScanStatus();
  });

  renderScanNowFooter(scope);
}

async function openScanNowDialog() {
  resetModalModifierClasses();
  document.querySelector("#deal-modal .modal-content").classList.add("scan-now-detail");
  const body = $("#modal-body");
  body.innerHTML = `<p class="meta">Loading stores…</p>`;
  $("#deal-modal").classList.remove("hidden");

  const scope = await api("/api/scan/scope");
  window._scanNowSelected = new Set();
  scope.retailers.forEach((r) => r.stores.forEach((s) => window._scanNowSelected.add(s.store_id)));
  renderScanNowDialog(scope);
}

const SCAN_STATE_LABELS = {
  starting: "Starting up…",
  scanning: "Scanning…",
  idle: "Idle",
  unreachable: "Scanner unreachable",
  unknown: "Unknown",
};

async function refreshScanStatus() {
  const status = await api("/api/scan/status");
  const badge = $("#scan-state-badge");
  const state = status.scanner.state || "unknown";
  badge.textContent = SCAN_STATE_LABELS[state] || state;
  badge.title = state === "idle"
    ? "Not currently scanning — \"Scan now\" will start one immediately."
    : "A scan is in progress — \"Scan now\" is disabled until it finishes.";
  badge.className = `badge ${state}`;

  // Disabled while a scan is already running so it's never ambiguous
  // whether clicking does anything -- confirmed source of confusion
  // 2026-08-31 (the button was always clickable regardless of state).
  const btn = $("#scan-now-btn");
  const scanInProgress = state === "scanning" || state === "starting";
  btn.disabled = scanInProgress;
  btn.textContent = scanInProgress ? "Scanning…" : "Scan now";

  // Live checkpoint progress (see orchestrator.py's on_progress) -- "what
  // is it actually doing right now", not just a state word. Confirmed
  // real friction this session: this used to require reading logs or the
  // DB by hand to answer.
  const detail = $("#scan-progress-detail");
  const p = status.scanner.progress;
  if (!scanInProgress || !p || !p.phase) {
    detail.hidden = true;
  } else {
    const parts = [];
    if (p.store) parts.push(`Store ${p.store_index || "?"}/${p.stores_total || "?"} (${p.store})`);
    if (p.department) {
      const deptProgress = p.department_products_total
        ? `${p.department_products_checked || 0}/${p.department_products_total}`
        : "…";
      parts.push(`Dept ${p.department_index || "?"}/${p.departments_total || "?"} "${p.department}" ${deptProgress}`);
    }
    if (p.errors_count) parts.push(`${p.errors_count} error(s)`);
    detail.textContent = parts.join(" · ") || "Starting…";
    detail.hidden = parts.length === 0;
  }
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

const POPOVER_FILTER_IDS = ["#f-clearance", "#f-penny", "#f-min-discount", "#f-price-min", "#f-price-max", "#f-in-stock"];

function updateFiltersCountBadge() {
  const active = POPOVER_FILTER_IDS.filter((sel) => {
    const el = $(sel);
    return el.type === "checkbox" ? el.checked : el.value.trim() !== "";
  }).length;
  const badge = $("#filters-count-badge");
  badge.textContent = String(active);
  badge.hidden = active === 0;
}

function setupFiltersPopover() {
  const toggleBtn = $("#filters-toggle-btn");
  const popover = $("#filters-popover");
  toggleBtn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    popover.hidden = !popover.hidden;
  });
  popover.addEventListener("click", (ev) => ev.stopPropagation());
  document.addEventListener("click", () => { popover.hidden = true; });

  $("#filters-clear-btn").addEventListener("click", () => {
    $("#f-clearance").checked = false;
    $("#f-penny").checked = false;
    $("#f-min-discount").value = "";
    $("#f-price-min").value = "";
    $("#f-price-max").value = "";
    $("#f-in-stock").checked = false;
    updateFiltersCountBadge();
    loadDeals();
  });
}

function setupFilters() {
  // Sidebar tree selections and the status-bar tags fire their own reload
  // (selectRetailer/selectStore/selectDepartment, renderStatusBar) -- this
  // only wires the filter row above the deal list.
  setupFiltersPopover();
  ["#f-clearance", "#f-penny", "#f-min-discount", "#f-price-min", "#f-price-max", "#f-in-stock", "#f-sort"].forEach(
    (sel) => $(sel).addEventListener("change", () => { updateFiltersCountBadge(); loadDeals(); })
  );
  $("#f-search").addEventListener("input", () => {
    clearTimeout(window._searchDebounce);
    window._searchDebounce = setTimeout(loadDeals, 300);
  });
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (ev) => {
    if ($(".tab-btn.active")?.dataset.tab !== "deals") return;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    const rows = $$("#deal-list .deal-row");
    if (!rows.length) return;
    let idx = rows.findIndex((r) => r.classList.contains("focused"));

    if (ev.key === "j" || ev.key === "k") {
      ev.preventDefault();
      if (idx >= 0) rows[idx].classList.remove("focused");
      idx = ev.key === "j" ? Math.min(idx + 1, rows.length - 1) : Math.max(idx - 1, 0);
      rows[idx].classList.add("focused");
      rows[idx].scrollIntoView({ block: "nearest" });
    } else if (ev.key === "w" && idx >= 0) {
      rows[idx].querySelector(".dv-want-btn")?.click();
    } else if (ev.key === "d" && idx >= 0) {
      rows[idx].querySelector(".not-interested-btn")?.click();
    }
  });
}

async function main() {
  setupTabs();
  setupFilters();
  setupScopeBar();
  setupKeyboardShortcuts();
  $("#modal-close").addEventListener("click", closeModal);
  $("#scan-now-btn").addEventListener("click", () => openScanNowDialog());

  api("/api/health").then((h) => {
    $("#build-time").textContent = h.build_time || "unknown";
  });

  loadScopeFromUrl();
  await loadTree();
  await loadDeals();
  localStorage.setItem(LAST_VISIT_KEY, new Date().toISOString());
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
