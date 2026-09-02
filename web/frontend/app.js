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

// Compact absolute stamp for the Detected column's second line ("Aug 31 ·
// 4:12p") -- `toLocaleString()`'s full date+time ("9/2/2026, 3:15:12 PM")
// wraps onto two lines in the 96px column (screenshot 14-header-scanning).
function absTimeCompact(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  const month = d.toLocaleString("en-US", { month: "short" });
  let hours = d.getHours();
  const minutes = String(d.getMinutes()).padStart(2, "0");
  const ampm = hours >= 12 ? "p" : "a";
  hours = hours % 12 || 12;
  return `${month} ${d.getDate()} · ${hours}:${minutes}${ampm}`;
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
  // discount_pct comes from the backend rounded to one decimal (queries.py's
  // list_deals), not a whole percent -- round only for display here so the
  // underlying value used for sorting/filtering stays precise. round() is
  // monotonic, so rounding after min/max === min/max of the rounded values.
  const lo = Math.round(Math.min(...pcts)), hi = Math.round(Math.max(...pcts));
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

// resetWindow: true for anything that changes *what* the feed contains
// (scope, status tag, triage-new-only, search/sort/filters) so the render
// window starts back at the top of the new result set; left false (the
// default) for in-place mutations (want/dismiss/defer/undo) and the
// periodic background refresh, which should leave the user's scroll
// position alone -- see renderDealListFooter/extendDealWindow.
async function loadDeals({ resetWindow = false } = {}) {
  if (resetWindow) dealWindowSize = DEAL_PAGE_SIZE;
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

  const rawDeals = await api(`/api/deals?${params.toString()}`);
  // deal_kind isn't a /api/deals query param (queries.list_deals has no
  // such filter) -- "watching" and "active" both fetch status=['new',
  // 'active'] and split on deal_kind here, mirroring status_bar_counts'
  // own active-excludes-watching split so the feed and the status tag
  // counts never disagree.
  const allDeals = filterByDealKind(rawDeals, scope.statusFilter);
  const lastVisit = localStorage.getItem(LAST_VISIT_KEY);
  const newCount = lastVisit ? allDeals.filter((d) => d.created_at > lastVisit).length : 0;
  const displayDeals = (window._triageNewOnly && lastVisit)
    ? allDeals.filter((d) => d.created_at > lastVisit)
    : allDeals;

  window._dealGroups = groupByProduct(displayDeals);
  renderDealsTable(window._dealGroups);
  renderNewBar({ newCount, total: allDeals.length, triageActive: window._triageNewOnly && !!lastVisit });
}

// "Chapel Hill #3612" -- screen 2a's store name plus the retailer's own
// store number, everywhere a store is named in the deal row. Falls back to
// the bare retailer_store_id (no "#") when store_name hasn't backfilled
// yet, matching the old fallback -- so a missing name never renders as the
// id twice ("#3612 #3612").
function storeLabel(row) {
  if (!row.store_name) return row.retailer_store_id || "";
  return row.retailer_store_id ? `${row.store_name} #${row.retailer_store_id}` : row.store_name;
}

function renderStoreLineHtml(productId, rows) {
  if (rows.length === 1) {
    const r = rows[0];
    const stock = stockText(r);
    return `${escapeHtml(storeLabel(r))}${r.aisle ? ` · Aisle ${escapeHtml(r.aisle)}${r.bay ? "/" + escapeHtml(r.bay) : ""}` : ""}${stock ? ` · ${stock}` : ""}`;
  }
  return `<span class="dv-expand-toggle" data-toggle-for="${productId}">▾ ${rows.length} of ${rows.length} stores</span>`;
}

// Leaf-only department label for the row's subline ("Pressure Treated ·
// SKU ..."), not the full space-joined breadcrumb department_name stores
// (see build_department_hierarchy's docstring -- "Electrical Batteries AA
// Batteries" is one flattened string, not three). Reuses the same
// tree-walk as openProductDetail's breadcrumb rather than re-parsing.
function leafDepartmentLabel(departmentName) {
  const labels = departmentBreadcrumbLabels(departmentName);
  return labels.length ? labels[labels.length - 1] : departmentName;
}

// Windowed rendering (Task 1, HANDOFF_DEALS_PAGE.md screen 2a's list
// footer). /api/deals has no pagination -- queries.list_deals's LIMIT 500
// is a ceiling, everything reachable is fetched every load -- so instead
// of asking the API for a page, this only *renders* a window of the
// already-fetched groups and lets the footer/scroll/J-past-the-end grow
// it. dealWindowSize is a module-level cursor (not per-call state) so
// wiring code elsewhere (extendDealWindow, the keyboard handler) can grow
// it without threading it through every call site.
const DEAL_PAGE_SIZE = 50;
let dealWindowSize = DEAL_PAGE_SIZE;

function renderDealsTable(groups) {
  const list = $("#deal-list");
  const empty = $("#deal-table-empty");
  list.innerHTML = "";
  empty.hidden = groups.size > 0;

  const entries = Array.from(groups.entries()).slice(0, dealWindowSize);
  for (const [productId, rows] of entries) {
    const first = rows[0];
    const cheapest = cheapestRow(rows);
    const addedAt = rows.reduce((min, r) => (r.created_at < min ? r.created_at : min), first.created_at);
    const isDeferred = rows.every((r) => r.status === "deferred");
    // deal_kind='upcoming_clearance' -- flagged as a future markdown, price
    // still full. Nothing in the scanner writes this kind yet (see
    // queries.status_bar_counts' docstring), so this is always false in
    // practice today; built to the spec anyway so the row renders
    // correctly the moment that write path lands.
    const isWatching = rows.every((r) => r.deal_kind === "upcoming_clearance");
    const thumbLink = productLink(cheapest.canonical_url, cheapest.retailer_store_id);
    const thumbHtml = first.image_url
      ? `<img class="dv-thumb${isWatching ? " dv-thumb-watching" : ""}" src="${first.image_url}" alt="">`
      : `<div class="dv-thumb-placeholder${isWatching ? " dv-thumb-watching" : ""}"></div>`;

    const row = document.createElement("div");
    row.className = "deal-row";
    row.dataset.product = productId;
    row.innerHTML = `
      <a class="dv-thumb-link" href="${thumbLink}" target="_blank" rel="noopener">${thumbHtml}</a>
      <div class="dv-body">
        <div class="dv-name">
          <a href="${thumbLink}" target="_blank" rel="noopener">${escapeHtml(first.product_name)}</a>
          ${rows.length > 1 ? `<span class="dv-store-badge">↗ ${escapeHtml(cheapest.retailer_store_id || "")}</span>` : ""}
        </div>
        <div class="dv-subline">${first.department_name ? escapeHtml(leafDepartmentLabel(first.department_name)) + " · " : ""}SKU ${escapeHtml(first.retailer_product_id)}</div>
        <div class="dv-store-line">${isWatching ? escapeHtml(watchingNoteText(first)) : renderStoreLineHtml(productId, rows)}</div>
        ${isDeferred ? `<div class="dv-deferred-note">${deferredNoteText(cheapest)}</div>` : ""}
      </div>
      <div class="dv-price">
        <div class="now">${priceRangeText(rows)}</div>
        ${isWatching ? `<div class="dv-no-drop">no drop yet</div>` : (first.list_price_cents ? `<div class="was">${money(first.list_price_cents)}</div>` : "")}
      </div>
      <div class="dv-detected">
        <div class="rel">${relTime(addedAt)}</div>
        <div class="abs">${absTimeCompact(addedAt)}</div>
      </div>
      <div class="dv-discount">${isWatching ? `<span class="dv-tag muted">Watching</span>` : dvDiscountTag(rows)}</div>
      <div class="dv-actions">
        <button class="dv-refresh-btn" data-product="${productId}" type="button" title="Check this item across every store, right now">⟳</button>
        ${isDeferred ? `
          <div class="dv-plain-actions">
            <button class="undefer-btn" data-deal="${cheapest.deal_id}">Change</button>
            <button class="not-interested-btn" data-product="${productId}" data-name="${escapeHtml(first.product_name)}">Never</button>
          </div>
        ` : `
          <div class="dv-split">
            <button class="${isWatching ? "dv-close-eye-btn close-eye-btn" : "dv-want-btn"}" data-deal="${cheapest.deal_id}">${isWatching ? "Close eye" : "Want"}</button>
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
            <a href="${productLink(r.canonical_url, r.retailer_store_id)}" target="_blank" rel="noopener">${escapeHtml(storeLabel(r))} ↗</a>
            <span class="dv-expand-meta">${r.aisle ? `Aisle ${escapeHtml(r.aisle)}${r.bay ? "/" + escapeHtml(r.bay) : ""} · ` : ""}${stockText(r) ? stockText(r) + " · " : ""}detected ${relTime(r.created_at)}</span>
            <span class="dv-expand-price">${money(r.price_cents)}</span>
            <button class="add-store-btn" data-deal="${r.deal_id}">Add to this store's list</button>
          </div>`
        )
        .join("");
      list.appendChild(expandRow);
    }
  }

  // groups is Map-ordered by the API's own sort (insertion order from
  // groupByProduct), so slicing the first dealWindowSize entries and
  // always emitting a multi-store group's expand row in the same pass as
  // its parent row keeps an expanded group from ever being split across
  // the window boundary -- there is no row-level cut, only a group-level
  // one.
  renderDealListFooter(groups.size, entries.length);
  wireDealRowActions(list);
}

// "28 more · J/K move · W want · D never show again" (screen 2a's list
// footer). /api/deals has no pagination (queries.list_deals's own LIMIT
// 500 is a ceiling, not a page) -- `totalCount` is every fetched-but-
// maybe-unrendered group, `renderedCount` is what's actually on screen
// (dealWindowSize, clamped by renderDealsTable). "N more" is always the
// true difference, honest even mid-scroll, and doubles as a click target.
function renderDealListFooter(totalCount, renderedCount) {
  let footer = $("#deal-list-footer");
  if (!footer) {
    footer = document.createElement("div");
    footer.id = "deal-list-footer";
    footer.className = "dv-list-footer";
    $("#deal-list").insertAdjacentElement("afterend", footer);
  }
  const remaining = totalCount - renderedCount;
  const parts = [];
  if (remaining > 0) parts.push(`<button type="button" id="deal-load-more-btn" class="dv-load-more-btn">${remaining} more</button>`);
  parts.push("J/K move", "W want", "D never show again");
  footer.innerHTML = parts.join(" · ");
  footer.hidden = totalCount === 0;
  $("#deal-load-more-btn")?.addEventListener("click", extendDealWindow);
  setupDealListScrollLoader(footer);
}

// Grows the render window by one page (Task 1) -- called from the footer's
// "N more" button, the scroll-triggered observer below, and J past the
// last rendered row. Re-renders the whole table rather than appending so
// the footer count and expand-row wiring stay correct in one place; the
// dataset (window._dealGroups) is already fully fetched, so this is a
// cheap re-render, not a network round trip.
function extendDealWindow() {
  if (!window._dealGroups || dealWindowSize >= window._dealGroups.size) return;
  dealWindowSize = Math.min(dealWindowSize + DEAL_PAGE_SIZE, window._dealGroups.size);
  renderDealsTable(window._dealGroups);
}

// Auto-loads more once the footer scrolls into view, per the brief's
// "load more on scroll or via a control in the footer". Re-observes the
// (stable, id-based) footer element on every render rather than trying to
// track observer lifecycle across renderDealsTable's list.innerHTML reset.
let dealFooterObserver = null;
function setupDealListScrollLoader(footer) {
  if (!("IntersectionObserver" in window)) return;
  dealFooterObserver?.disconnect();
  dealFooterObserver = new IntersectionObserver((entries) => {
    if (entries[0]?.isIntersecting) extendDealWindow();
  }, { rootMargin: "200px" });
  dealFooterObserver.observe(footer);
}

function deferredNoteText(row) {
  const rule = row.defer_rule;
  if (!rule) return "";
  if (rule.type === "penny") return "Waiting for penny status.";
  if (rule.type === "price") return `Waiting for price to drop below ${money(Math.round(rule.value * 100))}.`;
  return `Waiting for ≥${rule.value}% off.`;
}

// Screen 2a's Watching-row store line: "Flagged as upcoming clearance —
// price still full. Checked every 2h, last 14m ago." -- built from the
// same check_interval_seconds/last_checked_at list_deals already returns
// for defer-threshold rows, not a new backend field.
function watchingNoteText(row) {
  const interval = formatDuration(row.check_interval_seconds) || "?";
  const checkedAgo = row.last_checked_at ? relTime(row.last_checked_at) : "unknown";
  return `Flagged as upcoming clearance — price still full. Checked every ${interval}, last ${checkedAgo}.`;
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
  // Watching row's "Close eye" -- shortens this deal's price-check
  // interval (see queries.py's close_eye route docstring). Stays in the
  // Watching feed afterward; it's a cadence change, not a disposition.
  list.querySelectorAll(".close-eye-btn").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await api(`/api/deals/${btn.dataset.deal}/close-eye`, { method: "POST" });
      loadDeals();
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
  const parts = [];
  if (newCount > 0) {
    parts.push(`<span><strong>${newCount} new</strong> · ${total} total</span>`);
    parts.push(`<button type="button" id="triage-new-btn" class="dv-btn dv-btn-outline">${triageActive ? "Show all" : "Triage new only"}</button>`);
  }
  if (window._lastAction) {
    parts.push(`<span>Last ${escapeHtml(window._lastAction.label)} <button type="button" id="undo-btn" class="dv-btn dv-btn-outline">undo</button></span>`);
  }
  // Single source of truth for visibility -- bar.hidden previously had its
  // own separate condition (newCount === 0 && !lastAction) that duplicated
  // this logic instead of deriving from it, and .new-bar's unconditional
  // `display: flex` beats the `[hidden]` UA rule at equal specificity (see
  // .new-bar[hidden] below), so a drifted condition rendered as a visible
  // empty accent strip rather than nothing -- confirmed live 2026-09-02.
  bar.hidden = parts.length === 0;
  if (bar.hidden) return;
  bar.innerHTML = parts.join("");
  $("#triage-new-btn")?.addEventListener("click", () => {
    window._triageNewOnly = !window._triageNewOnly;
    loadDeals({ resetWindow: true });
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

  $("#deal-modal").hidden = false;

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

// --- Screens 3a (shopping lists, desktop) / 3b (walking view, mobile) ------
// Full rewrite per docs/HANDOFF_DEALS_PAGE.md -- the pre-redesign version
// above grouped by department and read /api/deals?status=saved, which
// can't distinguish "still wanted" from "marked no longer needed" (both
// keep deal.status='saved' -- see list_item's schema docstring). /api/lists
// (web/backend/routes/lists.py) is the only correct source of list
// membership now: it excludes state='no_longer_needed' server-side.
//
// `listsData` is the last GET /api/lists response, shared by both screens
// so an action taken in the walking view (3b) is reflected immediately if
// the user backs out to the grid (3a) without an extra round trip beyond
// the one refetch each mutating action already does.
let listsData = null;
// Per-store desktop expand/collapse (session-only, matches 3a's "overflow
// stores can render collapsed" -- the wireframe doesn't give a threshold,
// so this defaults to "first row of the 2-column grid open, the rest
// collapsed" and remembers manual Expand clicks across re-renders.
const slCollapsed = new Map();

async function loadShoppingList() {
  listsData = await api("/api/lists");
  renderShoppingLists();
}

function renderShoppingLists() {
  const container = $("#shopping-list-content");
  const stores = listsData.stores;

  if (stores.length === 0) {
    container.innerHTML = `<p class="meta">Nothing on a list yet — use "Want" on a deal (Deals tab) to add it to that store's list.</p>`;
    return;
  }

  const cards = stores
    .map((store, idx) => {
      if (!slCollapsed.has(store.store_id)) slCollapsed.set(store.store_id, idx >= 2);
      return slCollapsed.get(store.store_id) ? renderCollapsedStoreRow(store) : renderStoreCard(store, idx === 0);
    })
    .join("");

  container.innerHTML = `
    ${renderListsSummaryBar(stores, listsData.total_items)}
    <div class="sl-grid">${cards}</div>
    <div id="print-area"></div>
  `;
}

function renderListsSummaryBar(stores, totalItems) {
  const tags = stores
    .map((s) => `<span class="sl-store-tag">${escapeHtml(s.retailer_name)} #${escapeHtml(s.retailer_store_id)} · ${s.counts.total}</span>`)
    .join("");
  return `
    <div class="sl-summary-bar">
      <div class="sl-summary-left">
        <span class="sl-summary-count">${stores.length} STORE LIST${stores.length === 1 ? "" : "S"} · ${totalItems} ITEM${totalItems === 1 ? "" : "S"}</span>
        <div class="sl-store-tags">${tags}</div>
      </div>
      <div class="sl-summary-right">
        <button type="button" id="sl-email-all-btn" class="dv-btn dv-btn-outline">Email all ${stores.length} list${stores.length === 1 ? "" : "s"}</button>
        <span class="sl-merge-note">Lists never merge — each is a separate trip.</span>
      </div>
    </div>
  `;
}

// Store hours were dropped entirely (see AGENT_BRIEF/task report) -- nothing
// populates store.hours (Home Depot's storeSearch query has no hours field),
// and it was about to become dead schema on the live database. Distance is
// the one location signal /api/lists actually returns, so that's all the
// store card / walking-view headers show; the wireframe's "24 min away" is a
// drive-time estimate we have no data for.
function distanceSegment(store) {
  return store.distance_miles != null ? `${store.distance_miles.toFixed(1)} mi away` : null;
}

function clockTime(dateStr) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  let h = d.getHours();
  const m = d.getMinutes();
  const suffix = h >= 12 ? "p" : "a";
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")}${suffix}`;
}

function storeCaps(store) {
  return `${escapeHtml(store.retailer_name || "").toUpperCase()} · ${escapeHtml(store.store_name || "").toUpperCase()} #${escapeHtml(store.retailer_store_id || "")}`;
}

function itemSubtitle(item, aisleUnknown) {
  const segs = [];
  if (aisleUnknown) {
    segs.push("no aisle from site · ask an associate");
  } else if (item.bay) {
    segs.push(`Bay ${escapeHtml(item.bay)}`);
  }
  if (item.state === "purchased") {
    segs.push(`found &amp; purchased ${clockTime(item.purchased_at)}`);
  } else if (item.state === "cant_find") {
    segs.push(`marked <strong>can't find</strong>`);
    segs.push("kept for next trip");
  } else {
    const st = stockText(item);
    if (st) segs.push(st);
    if (item.sku) segs.push(`SKU ${escapeHtml(item.sku)}`);
  }
  return segs.join(" · ");
}

function slThumb(item) {
  if (item.state === "cant_find") return `<div class="sl-thumb sl-thumb-dashed"></div>`;
  return item.image_url
    ? `<img class="sl-thumb" src="${item.image_url}" alt="">`
    : `<div class="sl-thumb sl-thumb-placeholder"></div>`;
}

function renderListItemRow(item, aisleUnknown) {
  const rowClass = ["sl-item-row"];
  if (item.state === "purchased") rowClass.push("sl-item-purchased");
  if (item.state === "cant_find") rowClass.push("sl-item-cantfind");
  return `
    <div class="${rowClass.join(" ")}">
      <input type="checkbox" class="sl-item-checkbox" data-deal="${item.deal_id}" ${item.state === "purchased" ? "checked" : ""} aria-label="Mark ${escapeHtml(item.product_name)} purchased">
      ${slThumb(item)}
      <div class="sl-item-body">
        <div class="sl-item-name">${escapeHtml(item.product_name)}</div>
        <div class="sl-item-sub">${itemSubtitle(item, aisleUnknown)}</div>
      </div>
      <div class="sl-item-price">
        ${money(item.price_cents)}
        ${item.quantity ? `<div class="sl-item-qty">×${item.quantity}</div>` : ""}
      </div>
    </div>
  `;
}

function renderAisleGroup(group) {
  const aisleUnknown = group.aisle == null;
  const label = aisleUnknown ? "AISLE UNKNOWN" : `AISLE ${escapeHtml(group.aisle)}`;
  return `
    <div class="sl-aisle-header">${label} · ${group.items.length} ITEM${group.items.length === 1 ? "" : "S"}</div>
    ${group.items.map((item) => renderListItemRow(item, aisleUnknown)).join("")}
  `;
}

function footerStatusText(counts) {
  if (counts.purchased === 0 && counts.cant_find === 0) return "Nothing picked up yet";
  let text = `${counts.purchased} of ${counts.total} picked up`;
  if (counts.cant_find > 0) text += ` · ${counts.cant_find} not found`;
  return text;
}

function renderStoreCard(store, isFirst) {
  const distanceLine = distanceSegment(store);
  return `
    <div class="sl-card" data-store="${store.store_id}">
      <div class="sl-card-header">
        <div class="sl-card-title">${storeCaps(store)}</div>
        <div class="sl-card-count">${store.counts.total} item${store.counts.total === 1 ? "" : "s"}</div>
      </div>
      ${store.address ? `<div class="sl-card-address">${escapeHtml(store.address)}</div>` : ""}
      ${distanceLine ? `<div class="sl-card-distance">${distanceLine}</div>` : ""}
      <div class="sl-card-actions">
        ${store.address ? `<a class="dv-btn dv-btn-outline" target="_blank" rel="noopener" href="${mapsLink(store.address)}">Directions ↗</a>` : ""}
        <button type="button" class="dv-btn dv-btn-outline sl-walk-btn" data-store="${store.store_id}">Open walking view</button>
        <button type="button" class="dv-btn sl-btn-primary sl-email-btn" data-store="${store.store_id}">Email this list</button>
        <button type="button" class="dv-btn dv-btn-outline sl-print-btn" data-store="${store.store_id}">Print</button>
      </div>
      ${isFirst ? `<p class="sl-email-note">Email includes aisle/bay, prices, SKUs and the directions link — readable with no signal in the store. (Item photos aren't included in the email.)</p>` : ""}
      <div class="sl-aisle-groups">
        ${store.aisle_groups.map(renderAisleGroup).join("")}
      </div>
      <div class="sl-card-footer">
        <span class="sl-footer-status">${footerStatusText(store.counts)}</span>
        ${store.counts.purchased > 0 ? `<button type="button" class="sl-clear-btn" data-store="${store.store_id}">Clear finished</button>` : ""}
      </div>
    </div>
  `;
}

function renderCollapsedStoreRow(store) {
  const distance = distanceSegment(store);
  return `
    <div class="sl-collapsed-row" data-store="${store.store_id}">
      <div class="sl-collapsed-name">${storeCaps(store)}</div>
      <div class="sl-collapsed-meta">
        ${store.counts.total} item${store.counts.total === 1 ? "" : "s"}
        ${store.address ? ` · ${escapeHtml(store.address)}` : ""}
        ${distance ? ` · ${distance}` : ""}
      </div>
      <div class="sl-collapsed-actions">
        <button type="button" class="dv-btn sl-btn-primary sl-email-btn" data-store="${store.store_id}">Email this list</button>
        <button type="button" class="dv-btn dv-btn-outline sl-expand-btn" data-store="${store.store_id}">Expand</button>
      </div>
    </div>
  `;
}

function findItemByDeal(dealId) {
  for (const store of listsData?.stores || []) {
    for (const group of store.aisle_groups) {
      const found = group.items.find((i) => i.deal_id === dealId);
      if (found) return found;
    }
  }
  return null;
}

// --- Email (mailto:) -- decided by the user: client-built mailto link, no
// backend send (see AGENT_BRIEF.md's "decisions already made"). Plain text,
// one section per aisle in walking order, maps URL as a visible link.
// Photos can't be inlined in a mailto body -- accepted, called out in the
// on-page note (renderStoreCard's isFirst branch) instead of silently
// promising something we don't deliver.
function storeEmailBody(store) {
  const lines = [];
  lines.push(`${store.retailer_name} · ${store.store_name} #${store.retailer_store_id}`);
  if (store.address) lines.push(store.address);
  if (store.address) lines.push(`Directions: ${mapsLink(store.address)}`);
  lines.push("");
  for (const group of store.aisle_groups) {
    const label = group.aisle == null ? "AISLE UNKNOWN" : `AISLE ${group.aisle}`;
    lines.push(`${label} (${group.items.length} item${group.items.length === 1 ? "" : "s"})`);
    for (const item of group.items) {
      const parts = [item.product_name];
      if (item.bay) parts.push(`Bay ${item.bay}`);
      parts.push(money(item.price_cents));
      if (item.sku) parts.push(`SKU ${item.sku}`);
      if (item.stock_quantity != null) parts.push(`${item.stock_quantity} left`);
      if (item.quantity) parts.push(`qty ×${item.quantity}`);
      if (item.state === "purchased") parts.push(`purchased${item.purchased_at ? " " + clockTime(item.purchased_at) : ""}`);
      if (item.state === "cant_find") parts.push(`can't find${item.cant_find_reason ? " — " + item.cant_find_reason : ""}`);
      lines.push(`  - ${parts.join(" · ")}`);
    }
    lines.push("");
  }
  return lines.join("\n");
}

function emailStoreList(storeId) {
  const store = listsData.stores.find((s) => s.store_id === storeId);
  if (!store) return;
  const subject = `${store.retailer_name} #${store.retailer_store_id} shopping list (${store.counts.total} items)`;
  window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(storeEmailBody(store))}`;
}

// "Email all N lists" -- one mail with clearly-sectioned store blocks,
// rather than firing N separate mailto: navigations. Browsers/mail clients
// don't reliably let a page open several mailto: drafts from one click (the
// 2nd+ navigation is commonly blocked as a popup or just clobbers the 1st),
// so N-separate-emails isn't achievably "one clearly-sectioned mail" either
// way -- a single email keeps the one-tap action predictable.
function emailAllLists() {
  const stores = listsData.stores;
  if (stores.length === 0) return;
  const subject = `${stores.length} store shopping lists (${listsData.total_items} items)`;
  const body = stores
    .map((s) => `===== ${s.retailer_name} · ${s.store_name} #${s.retailer_store_id} =====\n\n${storeEmailBody(s)}`)
    .join("\n");
  window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

// --- Print -- "same aisle-ordered content as the email" (handoff). Renders
// into #print-area (inside #tab-shopping, appended by renderShoppingLists)
// and a print-only stylesheet (style.css, bottom) hides everything else via
// a body.printing-list toggle -- the no-build/no-framework equivalent of a
// dedicated print view.
function buildPrintHtml(store) {
  const groups = store.aisle_groups
    .map((group) => {
      const label = group.aisle == null ? "AISLE UNKNOWN" : `AISLE ${escapeHtml(group.aisle)}`;
      const items = group.items
        .map((item) => {
          const parts = [escapeHtml(item.product_name)];
          if (item.bay) parts.push(`Bay ${escapeHtml(item.bay)}`);
          parts.push(money(item.price_cents));
          if (item.sku) parts.push(`SKU ${escapeHtml(item.sku)}`);
          if (item.stock_quantity != null) parts.push(`${item.stock_quantity} left`);
          if (item.quantity) parts.push(`×${item.quantity}`);
          if (item.state === "purchased") parts.push(`purchased${item.purchased_at ? " " + clockTime(item.purchased_at) : ""}`);
          if (item.state === "cant_find") parts.push(`can't find${item.cant_find_reason ? ": " + escapeHtml(item.cant_find_reason) : ""}`);
          return `<li>${parts.join(" · ")}</li>`;
        })
        .join("");
      return `<h3>${label} · ${group.items.length} ITEM${group.items.length === 1 ? "" : "S"}</h3><ul>${items}</ul>`;
    })
    .join("");
  return `
    <h1>${storeCaps(store)}</h1>
    ${store.address ? `<p>${escapeHtml(store.address)}</p>` : ""}
    ${store.address ? `<p>Directions: ${mapsLink(store.address)}</p>` : ""}
    ${groups}
  `;
}

function printStoreList(storeId) {
  const store = listsData.stores.find((s) => s.store_id === storeId);
  if (!store) return;
  const area = $("#print-area");
  if (!area) return;
  area.innerHTML = buildPrintHtml(store);
  document.body.classList.add("printing-list");
  window.print();
}

function setupShoppingTab() {
  // One-time delegated listeners on the tab panel -- renderShoppingLists()
  // replaces #shopping-list-content's innerHTML on every refresh, but the
  // panel element itself (the listener target) never gets swapped out.
  const panel = $("#tab-shopping");

  panel.addEventListener("click", async (ev) => {
    const expandBtn = ev.target.closest(".sl-expand-btn");
    if (expandBtn) {
      slCollapsed.set(Number(expandBtn.dataset.store), false);
      renderShoppingLists();
      return;
    }
    const walkBtn = ev.target.closest(".sl-walk-btn");
    if (walkBtn) {
      openWalkingView(Number(walkBtn.dataset.store));
      return;
    }
    const emailBtn = ev.target.closest(".sl-email-btn");
    if (emailBtn) {
      emailStoreList(Number(emailBtn.dataset.store));
      return;
    }
    const emailAllBtn = ev.target.closest("#sl-email-all-btn");
    if (emailAllBtn) {
      emailAllLists();
      return;
    }
    const printBtn = ev.target.closest(".sl-print-btn");
    if (printBtn) {
      printStoreList(Number(printBtn.dataset.store));
      return;
    }
    const clearBtn = ev.target.closest(".sl-clear-btn");
    if (clearBtn) {
      await api(`/api/lists/store/${clearBtn.dataset.store}/clear-finished`, { method: "POST" });
      await loadShoppingList();
    }
  });

  panel.addEventListener("change", async (ev) => {
    const cb = ev.target.closest(".sl-item-checkbox");
    if (!cb) return;
    const dealId = Number(cb.dataset.deal);
    const item = findItemByDeal(dealId);
    if (!item) return;
    // Toggle: open -> purchased; anything already resolved (purchased or
    // cant_find) -> reopen (undo) rather than re-marking purchased, so the
    // one checkbox works for both directions.
    if (item.state === "open") {
      await api(`/api/lists/items/${dealId}/purchased`, { method: "POST" });
    } else {
      await api(`/api/lists/items/${dealId}/reopen`, { method: "POST" });
    }
    await loadShoppingList();
  });

  window.addEventListener("afterprint", () => document.body.classList.remove("printing-list"));
}

// --- Screen 3b: mobile walking view -----------------------------------
// Reached from a store card's "Open walking view" -- lives in #walking-view,
// a fixed full-viewport overlay outside <main> (see index.html) so it isn't
// subject to the tab-panel show/hide machinery (it isn't a tab). Built at a
// 390px intrinsic width per the wireframe; a later responsive/media-query
// pass integrates it into the rest of the app's breakpoints.
let wvState = null; // { storeId, skipped: Set<dealId>, undoStack: [dealId], aisleUnknownExpanded }

function currentWvStore() {
  if (!wvState) return null;
  return listsData?.stores.find((s) => s.store_id === wvState.storeId) || null;
}

// "The next unresolved item" (handoff) in aisle order. `skipped` is
// client-only state (State management's "in-flight swipe offset"-style
// client bucket, nothing to persist) -- Skip has no backend endpoint, it
// just deprioritizes an item for the rest of this walking-view session so
// the promoted card moves on without resolving anything.
function pickPromotedItem(store) {
  const flat = [];
  for (const g of store.aisle_groups) for (const item of g.items) flat.push(item);
  const unresolved = flat.filter((i) => i.state === "open");
  if (unresolved.length === 0) return null;
  let candidates = unresolved.filter((i) => !wvState.skipped.has(i.deal_id));
  if (candidates.length === 0) {
    wvState.skipped.clear();
    candidates = unresolved;
  }
  return candidates[0];
}

// "Why it was flagged" (required in-aisle per the handoff), backed by the
// real fields now -- store_lists (web/backend/queries.py) selects
// deal.deal_kind and the latest observation's is_clearance/is_penny. Only
// states the data can actually support: no Home-Depot-specific tag
// language ("yellow-tag") the data doesn't carry, and nothing at all when
// none of the flags are set (an empty meta segment beats a guess).
function flagReasonText(item) {
  if (item.deal_kind === "penny" || item.is_penny) return "penny item";
  if (item.deal_kind === "upcoming_clearance") return "flagged as upcoming clearance";
  if (item.is_clearance) return "clearance";
  return "";
}

function wvThumb(item, sizeClass) {
  if (item.image_url) return `<img class="${sizeClass}" src="${item.image_url}" alt="">`;
  return `<div class="${sizeClass} wv-thumb-placeholder"></div>`;
}

function renderPromotedCard(item) {
  const flagReason = flagReasonText(item);
  const meta = [stockText(item), flagReason].filter(Boolean).join(" · ");
  return `
    <div class="wv-current-aisle-header">AISLE ${item.aisle ? escapeHtml(item.aisle) : "UNKNOWN"}${item.bay ? " · BAY " + escapeHtml(item.bay) : ""}</div>
    <div class="wv-current-card">
      <div class="wv-current-top">
        ${wvThumb(item, "wv-current-photo")}
        <div class="wv-current-info">
          <div class="wv-current-name">${escapeHtml(item.product_name)}</div>
          <div class="wv-current-price-row">
            <span class="wv-current-price">${money(item.price_cents)}</span>
            ${item.discount_pct != null ? `<span class="wv-discount-tag">${item.discount_pct}% off</span>` : ""}
          </div>
          ${meta ? `<div class="wv-current-meta">${meta}</div>` : ""}
          ${item.sku ? `<div class="wv-current-sku">SKU ${escapeHtml(item.sku)}</div>` : ""}
        </div>
      </div>
      <div class="wv-action-row">
        <button type="button" class="wv-found-btn" data-deal="${item.deal_id}">Found it</button>
        <button type="button" class="wv-cantfind-btn" data-deal="${item.deal_id}">Can't find</button>
        <button type="button" class="wv-skip-btn" data-deal="${item.deal_id}">Skip</button>
      </div>
    </div>
  `;
}

function renderCompactRow(item) {
  if (item.state === "purchased") {
    return `
      <div class="wv-row wv-row-purchased">
        ${wvThumb(item, "wv-thumb")}
        <div class="wv-row-body">
          <div class="wv-row-name">${escapeHtml(item.product_name)}</div>
          <div class="wv-row-sub">purchased${item.purchased_at ? ` ${clockTime(item.purchased_at)}` : ""} · ${money(item.price_cents)}</div>
        </div>
      </div>
    `;
  }
  if (item.state === "cant_find") {
    const reasonLabel = item.cant_find_reason ? `reason: ${escapeHtml(item.cant_find_reason)}` : "reason";
    return `
      <div class="wv-row wv-row-cantfind">
        <div class="wv-thumb wv-thumb-dashed"></div>
        <div class="wv-row-body">
          <div class="wv-row-name">${escapeHtml(item.product_name)}</div>
          <div class="wv-row-sub">${item.bay ? `Bay ${escapeHtml(item.bay)} · ` : ""}can't find · <a href="#" class="wv-reason-link" data-deal="${item.deal_id}">${reasonLabel}</a></div>
        </div>
      </div>
    `;
  }
  return `
    <div class="wv-row">
      ${wvThumb(item, "wv-thumb")}
      <div class="wv-row-body">
        <div class="wv-row-name">${escapeHtml(item.product_name)}</div>
        <div class="wv-row-sub">${item.bay ? `Bay ${escapeHtml(item.bay)} · ` : ""}${money(item.price_cents)}${item.quantity ? ` · ×${item.quantity}` : ""}</div>
      </div>
    </div>
  `;
}

function renderWvAisleGroup(group, excludeDealId) {
  const items = group.items.filter((i) => i.deal_id !== excludeDealId);
  if (items.length === 0) return "";
  if (group.aisle == null) {
    if (!wvState.aisleUnknownExpanded) {
      return `<div class="wv-aisle-unknown-row" id="wv-aisle-unknown-toggle">Aisle unknown · ${items.length} item${items.length === 1 ? "" : "s"} ▸</div>`;
    }
    return `
      <div class="wv-aisle-unknown-row expanded" id="wv-aisle-unknown-toggle">Aisle unknown · ${items.length} item${items.length === 1 ? "" : "s"} ▾</div>
      ${items.map(renderCompactRow).join("")}
    `;
  }
  return `
    <div class="wv-aisle-header">AISLE ${escapeHtml(group.aisle)} · ${items.length} ITEM${items.length === 1 ? "" : "S"}</div>
    ${items.map(renderCompactRow).join("")}
  `;
}

function renderWalkingView() {
  const store = currentWvStore();
  if (!store) {
    closeWalkingView();
    return;
  }
  const promoted = pickPromotedItem(store);
  const counts = store.counts;
  const progressPct = counts.total ? Math.round(((counts.total - counts.open) / counts.total) * 100) : 0;
  const distance = distanceSegment(store);

  const body = [
    promoted ? renderPromotedCard(promoted) : `<p class="wv-all-done">Everything's resolved — nice work.</p>`,
    store.aisle_groups.map((g) => renderWvAisleGroup(g, promoted?.deal_id)).join(""),
  ].join("");

  $("#walking-view .wv-panel").innerHTML = `
    <div class="wv-header">
      <div class="wv-top-row">
        <a href="#" class="wv-back">‹ Lists</a>
        <span class="wv-left-count">${counts.open} of ${counts.total} left</span>
      </div>
      <div class="wv-store-name">
        <div>${escapeHtml((store.retailer_name || "").toUpperCase())}</div>
        <div>${escapeHtml((store.store_name || "").toUpperCase())} #${escapeHtml(store.retailer_store_id || "")}</div>
      </div>
      <div class="wv-address-line">
        ${escapeHtml(store.address || "")}${distance ? ` · ${distance}` : ""}${store.address ? ` · <a class="wv-directions" target="_blank" rel="noopener" href="${mapsLink(store.address)}">Directions ↗</a>` : ""}
      </div>
      <div class="wv-progress"><div class="wv-progress-fill" style="width:${progressPct}%"></div></div>
    </div>
    <div class="wv-body">${body}</div>
    <div class="wv-bottom-bar">
      <button type="button" class="wv-no-longer-need" ${promoted ? "" : "disabled"}>No longer need</button>
      <button type="button" class="wv-undo" ${wvState.undoStack.length ? "" : "disabled"}>Undo</button>
      <span class="wv-purchased-count">${counts.purchased} purchased</span>
    </div>
  `;
}

function openWalkingView(storeId) {
  wvState = { storeId, skipped: new Set(), undoStack: [], aisleUnknownExpanded: false };
  renderWalkingView();
  $("#walking-view").hidden = false;
}

function closeWalkingView() {
  $("#walking-view").hidden = true;
  wvState = null;
  // The grid may be stale if actions were taken in the walking view --
  // cheap enough to just refetch on the way back rather than track a dirty
  // flag through every mutating handler below.
  if ($(".tab-btn.active")?.dataset.tab === "shopping") loadShoppingList();
}

async function refreshListsData() {
  listsData = await api("/api/lists");
}

// Found it / Can't find (primary tap, no reason prompt -- see the reason
// link below for where a reason gets attached after the fact, per the
// handoff's explicit requirement that the fast in-aisle tap must work with
// no reason at all).
async function resolveWvItem(dealId, kind) {
  if (kind === "purchased") {
    await api(`/api/lists/items/${dealId}/purchased`, { method: "POST" });
  } else {
    await api(`/api/lists/items/${dealId}/cant-find`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: null }),
    });
  }
  wvState.undoStack.push(dealId);
  wvState.skipped.delete(dealId);
  await refreshListsData();
  renderWalkingView();
}

function setupWalkingView() {
  const el = $("#walking-view");

  el.addEventListener("click", async (ev) => {
    if (ev.target.closest(".wv-back")) {
      ev.preventDefault();
      closeWalkingView();
      return;
    }
    if (ev.target.closest(".wv-directions")) return; // real navigation, let it through

    const foundBtn = ev.target.closest(".wv-found-btn");
    if (foundBtn) return resolveWvItem(Number(foundBtn.dataset.deal), "purchased");

    const cantFindBtn = ev.target.closest(".wv-cantfind-btn");
    if (cantFindBtn) return resolveWvItem(Number(cantFindBtn.dataset.deal), "cant_find");

    const skipBtn = ev.target.closest(".wv-skip-btn");
    if (skipBtn) {
      wvState.skipped.add(Number(skipBtn.dataset.deal));
      renderWalkingView();
      return;
    }

    // Not-drawn-in-the-wireframe decision (handoff explicitly flags the
    // can't-find reason sheet as "needs design or a developer decision"):
    // a plain text prompt, matching the existing share-link prompt() at
    // app.js:466 rather than inventing an un-approved polished enum sheet.
    const reasonLink = ev.target.closest(".wv-reason-link");
    if (reasonLink) {
      ev.preventDefault();
      const dealId = Number(reasonLink.dataset.deal);
      const item = findItemByDeal(dealId);
      const reason = window.prompt("Reason it couldn't be found (optional):", item?.cant_find_reason || "");
      if (reason !== null) {
        await api(`/api/lists/items/${dealId}/cant-find`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: reason.trim() || null }),
        });
        await refreshListsData();
        renderWalkingView();
      }
      return;
    }

    if (ev.target.closest("#wv-aisle-unknown-toggle")) {
      wvState.aisleUnknownExpanded = !wvState.aisleUnknownExpanded;
      renderWalkingView();
      return;
    }

    // "No longer need" applies to the item currently promoted into the
    // large card -- the walking view only ever has one item "in focus," so
    // that's the natural target for a bottom-bar action not tied to a
    // specific row (not spelled out by the wireframe -- flagged in report).
    const noLongerBtn = ev.target.closest(".wv-no-longer-need");
    if (noLongerBtn && !noLongerBtn.disabled) {
      const promoted = pickPromotedItem(currentWvStore());
      if (promoted) {
        await api(`/api/lists/items/${promoted.deal_id}/no-longer-needed`, { method: "POST" });
        wvState.undoStack.push(promoted.deal_id);
        await refreshListsData();
        renderWalkingView();
      }
      return;
    }

    const undoBtn = ev.target.closest(".wv-undo");
    if (undoBtn && !undoBtn.disabled) {
      const dealId = wvState.undoStack.pop();
      if (dealId != null) {
        await api(`/api/lists/items/${dealId}/reopen`, { method: "POST" });
        await refreshListsData();
        renderWalkingView();
      }
    }
  });
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
  $("#deal-modal").hidden = false;
}

function closeModal() {
  $("#deal-modal").hidden = true;
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
  watching: ["new", "active"],
  waiting: ["deferred"],
  all: ["new", "active", "deferred"],
};

// See loadDeals's call site -- deal_kind='upcoming_clearance' rows are
// carved out of "active" and into "watching" client-side, since both tabs
// fetch the same status=['new','active'] set from the backend.
function filterByDealKind(rows, statusFilter) {
  if (statusFilter === "active") return rows.filter((d) => d.deal_kind !== "upcoming_clearance");
  if (statusFilter === "watching") return rows.filter((d) => d.deal_kind === "upcoming_clearance");
  return rows;
}

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
  loadDeals({ resetWindow: true });
}

function selectStore(slug, storeId) {
  scope.retailerSlug = slug;
  scope.storeId = storeId;
  saveScopeToUrl();
  loadTree();
  loadDeals({ resetWindow: true });
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
  loadDeals({ resetWindow: true });
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
  if (scope.departmentName) {
    // department_name is the flattened space-joined breadcrumb (see
    // build_department_hierarchy) -- split it back into segments the same
    // way the deal row's subline does, rather than rendering it as one blob.
    const deptLabels = departmentBreadcrumbLabels(scope.departmentName);
    breadcrumb += " / " + deptLabels
      .map((seg, i) => (i === deptLabels.length - 1 ? `<strong>${escapeHtml(seg)}</strong>` : escapeHtml(seg)))
      .join(" / ");
  }
  $("#scope-breadcrumb").innerHTML = breadcrumb;

  const toggle = $("#scope-descendants-toggle");
  toggle.textContent = `incl. sub-departments ${scope.includeDescendants ? "\u2713" : ""}`;
  toggle.classList.toggle("on", scope.includeDescendants);
  toggle.hidden = !scope.departmentId;
}

function renderStatusBar(counts) {
  const tags = [
    { key: "active", label: `Active clearance ${counts.active}` },
    { key: "watching", label: `Watching ${counts.watching}` },
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
      loadDeals({ resetWindow: true });
    })
  );
}

function setupScopeBar() {
  $("#scope-descendants-toggle").addEventListener("click", () => {
    scope.includeDescendants = !scope.includeDescendants;
    saveScopeToUrl();
    renderScopeBar();
    loadTree();
    loadDeals({ resetWindow: true });
  });
  $("#dept-filter").addEventListener("input", () => renderDepartmentTree(window._departmentTree || []));
}

function dataToolsHtml(missingCount) {
  const missingLabel = missingCount == null ? "unknown" : missingCount;
  return `
    <div class="data-tools">
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

// --- Settings tab (wireframe screen 7 / 4c): per-retailer scan config,
// store enable/disable, and a departments-to-watch checkbox tree, plus a
// still-global Notifications/Data-tools section. `nav` is one of
// {type:"retailer", id} / {type:"notifications"} / {type:"data-tools"} --
// rebuilt fresh (not persisted) on every tab visit.
const settingsState = { retailers: [], nav: null };

async function loadSettings() {
  settingsState.retailers = await api("/api/settings/retailers");
  if (!settingsState.nav) {
    settingsState.nav = settingsState.retailers.length
      ? { type: "retailer", id: settingsState.retailers[0].id }
      : { type: "notifications" };
  }
  renderSettingsSidebar();
  await renderSettingsMain();
}

function renderSettingsSidebar() {
  const retailerTree = $("#settings-retailer-tree");
  retailerTree.innerHTML = settingsState.retailers.length
    ? settingsState.retailers.map((r) => {
        const subtitle = r.store_count
          ? `${r.store_count} store${r.store_count === 1 ? "" : "s"} · ${r.enabled ? "enabled" : "disabled"}`
          : "not connected";
        return `
          <div class="settings-tree-row ${settingsState.nav.type === "retailer" && settingsState.nav.id === r.id ? "selected" : ""}" data-retailer-id="${r.id}">
            <span class="settings-tree-row-name"><span class="settings-enabled-dot ${r.enabled ? "" : "off"}"></span>${escapeHtml(r.display_name)}</span>
            <span class="settings-tree-meta">${subtitle}</span>
          </div>
        `;
      }).join("")
    : `<p class="meta">None configured yet</p>`;
  retailerTree.querySelectorAll(".settings-tree-row").forEach((row) =>
    row.addEventListener("click", () => {
      settingsState.nav = { type: "retailer", id: Number(row.dataset.retailerId) };
      renderSettingsSidebar();
      renderSettingsMain();
    })
  );

  const globalTree = $("#settings-global-tree");
  const globalItems = [
    { type: "notifications", label: "Notifications" },
    { type: "data-tools", label: "Data tools" },
  ];
  globalTree.innerHTML = globalItems.map((item) => `
    <div class="settings-tree-row ${settingsState.nav.type === item.type ? "selected" : ""}" data-nav="${item.type}">
      <span class="settings-tree-name">${item.label}</span>
    </div>
  `).join("");
  globalTree.querySelectorAll(".settings-tree-row").forEach((row) =>
    row.addEventListener("click", () => {
      settingsState.nav = { type: row.dataset.nav };
      renderSettingsSidebar();
      renderSettingsMain();
    })
  );
}

async function renderSettingsMain() {
  const main = $("#settings-main");
  if (settingsState.nav.type === "retailer") {
    main.innerHTML = `<p class="meta">Loading…</p>`;
    const detail = await api(`/api/settings/retailers/${settingsState.nav.id}`);
    renderSettingsRetailerPanel(detail);
  } else if (settingsState.nav.type === "notifications") {
    await renderSettingsNotifications();
  } else if (settingsState.nav.type === "data-tools") {
    await renderSettingsDataTools();
  }
}

const CREDENTIAL_STATUS_LABELS = { valid: "Connected", expired: "Session expired", needs_login: "Needs login" };

function settingsStoreRowHtml(store) {
  const distance = store.distance_miles != null ? `${store.distance_miles.toFixed(1)} mi` : "distance unknown";
  const scanned = store.last_scanned_at ? `scanned ${relTime(store.last_scanned_at)}` : "never scanned";
  return `
    <label class="settings-store-row">
      <input type="checkbox" class="settings-store-check" data-store-id="${store.store_id}" ${store.enabled ? "checked" : ""} />
      <span class="settings-store-name">${escapeHtml(store.name || store.retailer_store_id)}</span>
      <span class="settings-store-meta">${distance} · ${scanned}</span>
    </label>
  `;
}

function renderSettingsRetailerPanel(detail) {
  const sc = detail.scan_config || {};
  const statusLabel = CREDENTIAL_STATUS_LABELS[detail.credential_status] || "Not yet scanned";

  $("#settings-main").innerHTML = `
    <div class="settings-panel-header">
      <h3>${escapeHtml(detail.display_name)}</h3>
      <span class="settings-badge ${detail.credential_status || ""}">${statusLabel} · adapter v${escapeHtml(detail.adapter_version || "0")}</span>
      <label class="settings-enabled-toggle"><input type="checkbox" id="settings-retailer-enabled" ${detail.enabled ? "checked" : ""} /> Enabled</label>
    </div>
    ${sc.error ? `<p class="settings-panel-note">Scanner unreachable -- showing saved values only.</p>` : ""}

    <div class="settings-section">
      <h4>Location</h4>
      <form id="settings-config-form" class="settings-form">
        <label>ZIP code <input type="text" id="cfg-zip" value="${escapeHtml(sc.zip_code || "")}" required></label>
        <label>Radius (miles) <input type="number" id="cfg-radius" min="1" step="0.5" value="${sc.radius_miles ?? ""}" required></label>
        <label>Watch keywords <input type="text" id="cfg-keywords" placeholder="blank = all products" value="${escapeHtml((sc.watch_keywords || []).join(", "))}"></label>
        <label>Product list cache (hours) <input type="number" id="cfg-cache-hours" min="0" step="1" value="${sc.product_list_cache_hours ?? ""}"></label>
        <label>Minimum discount % <input type="number" id="cfg-min-discount" min="0" max="100" step="1" value="${detail.min_discount_pct ?? ""}" placeholder="no floor"></label>
        <div class="modal-actions">
          <button type="submit">Save changes</button>
          <span id="settings-config-save-status" class="meta"></span>
        </div>
      </form>
    </div>

    <div class="settings-section">
      <h4>Stores</h4>
      <div class="settings-store-list">
        ${detail.stores.length ? detail.stores.map(settingsStoreRowHtml).join("") : `<p class="meta">No stores discovered yet.</p>`}
      </div>
      <div class="settings-rescan-row">
        <a href="#" id="settings-rescan-stores-btn">Rescan store list</a>
        <span id="settings-rescan-stores-status" class="meta"></span>
      </div>
    </div>

    <div class="settings-section">
      <h4>Departments to watch</h4>
      <div id="settings-dept-summary" class="settings-section-summary"></div>
      <input type="text" id="settings-dept-filter" class="settings-dept-filter" placeholder="Filter departments" />
      <div id="settings-dept-tree" class="settings-dept-tree"></div>
      <p class="meta">"–" marks a parent with some but not all descendants selected.</p>
      <div class="modal-actions" style="margin-top:8px">
        <button id="settings-dept-save" type="button">Save departments</button>
        <span id="settings-dept-save-status" class="meta"></span>
      </div>
    </div>

    <div class="settings-danger-zone">
      <h4>Danger zone</h4>
      <div class="data-tool">
        <div>
          <strong>Force product re-list</strong>
          <p class="meta">Clears this retailer's product-list cache so the next scan re-lists from the retailer instead of serving cached SKUs.</p>
        </div>
        <button id="settings-relist-btn" class="secondary" type="button">Reset cache</button>
      </div>
      <p id="settings-relist-status" class="meta"></p>
    </div>
  `;

  setupSettingsConfigForm(detail);
  setupSettingsStoreChecks();
  setupSettingsRescanStores(detail);
  setupSettingsDeptTree(detail);
  setupSettingsDangerZone(detail);

  $("#settings-retailer-enabled").addEventListener("change", async (ev) => {
    await api(`/api/settings/retailers/${detail.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: ev.target.checked }),
    });
    const row = settingsState.retailers.find((r) => r.id === detail.id);
    if (row) row.enabled = ev.target.checked;
    renderSettingsSidebar();
  });
}

function setupSettingsConfigForm(detail) {
  $("#settings-config-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const statusEl = $("#settings-config-save-status");
    statusEl.textContent = "Saving…";
    try {
      await api(`/api/settings/retailers/${detail.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          zip_code: $("#cfg-zip").value.trim(),
          radius_miles: Number($("#cfg-radius").value),
          watch_keywords: $("#cfg-keywords").value.trim(),
          product_list_cache_hours: Number($("#cfg-cache-hours").value),
        }),
      });
      const rawDiscount = $("#cfg-min-discount").value.trim();
      await api(`/api/settings/retailers/${detail.id}/min-discount`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_discount_pct: rawDiscount === "" ? null : Number(rawDiscount) }),
      });
      statusEl.textContent = "Saved -- takes effect on the next scan.";
    } catch (e) {
      statusEl.textContent = `Save failed: ${e.message}`;
    }
  });
}

function setupSettingsStoreChecks() {
  $$(".settings-store-check").forEach((el) =>
    el.addEventListener("change", () => {
      api(`/api/settings/stores/${el.dataset.storeId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: el.checked }),
      });
    })
  );
}

function setupSettingsRescanStores(detail) {
  $("#settings-rescan-stores-btn").addEventListener("click", async (ev) => {
    ev.preventDefault();
    const statusEl = $("#settings-rescan-stores-status");
    statusEl.textContent = "Rescanning…";
    try {
      const res = await api(`/api/settings/retailers/${detail.id}/rescan-stores`, { method: "POST" });
      if (!res.triggered) {
        statusEl.textContent = `Failed to start: ${res.error || "unknown error"}`;
        return;
      }
    } catch (e) {
      statusEl.textContent = `Failed to start: ${e.message}`;
      return;
    }
    await pollRescanStoresStatus(detail.id, statusEl);
  });
}

async function pollRescanStoresStatus(retailerId, statusEl) {
  // Re-lists just the store list -- much quicker than a real scan, but
  // still a real request against the retailer's store locator, so this
  // polls the same way pollRepairStatus does rather than assuming it's
  // done in one round trip.
  for (let i = 0; i < 60; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    let status;
    try {
      status = await api(`/api/settings/retailers/${retailerId}/rescan-stores/status`);
    } catch (e) {
      statusEl.textContent = `Lost track of progress: ${e.message}`;
      return;
    }
    if (status.state === "running") continue;
    const result = status.last_run_result;
    const summary = result && typeof result === "object" ? Object.values(result)[0] : result;
    if (summary && typeof summary === "object") {
      statusEl.textContent = `Done -- ${summary.stores_found} store(s) found.`;
    } else if (summary === "needs_login") {
      statusEl.textContent = "Needs login -- open the Browser tab and log in.";
    } else {
      statusEl.textContent = `Finished (${summary || "no result"}).`;
    }
    // Distance/name/address may have changed -- refresh just the store
    // list (only if still looking at this same retailer), not the whole
    // panel: a full re-render would instantly wipe the status message
    // above and any unsaved edits elsewhere in the form.
    if (settingsState.nav.type === "retailer" && settingsState.nav.id === retailerId) {
      const detail = await api(`/api/settings/retailers/${retailerId}`);
      const listEl = $(".settings-store-list");
      if (listEl) {
        listEl.innerHTML = detail.stores.length
          ? detail.stores.map(settingsStoreRowHtml).join("")
          : `<p class="meta">No stores discovered yet.</p>`;
        setupSettingsStoreChecks();
      }
    }
    return;
  }
  statusEl.textContent = "Still running after 2 minutes -- check the Logs tab.";
}

function setupSettingsDangerZone(detail) {
  $("#settings-relist-btn").addEventListener("click", async () => {
    if (!confirm(`Clear ${detail.display_name}'s product-list cache? The next scan re-lists products from scratch instead of using the cache (slower, more requests to the retailer).`)) return;
    const statusEl = $("#settings-relist-status");
    statusEl.textContent = "Resetting…";
    try {
      const res = await api(`/api/admin/reset-department-cache?retailer=${encodeURIComponent(detail.slug)}`, { method: "POST" });
      statusEl.textContent = `Done -- ${res.reset} department(s) will re-list on the next scan.`;
    } catch (e) {
      statusEl.textContent = `Failed: ${e.message}`;
    }
  });
}

// Descendant id sets (self excluded) for every department name, built the
// same deepest-first-fold way common/db.py's get_watched_department_names
// expands a watched selection -- used here for the tree's indeterminate
// state and the "products in scope" summary, not for what gets saved
// (only the raw explicit `selected` set is ever sent to the server).
function settingsDeptDescendantIdSets(departments) {
  const byName = new Map(departments.map((d) => [d.name, d]));
  const childrenByName = new Map();
  for (const d of departments) {
    if (d.parent) {
      if (!childrenByName.has(d.parent)) childrenByName.set(d.parent, []);
      childrenByName.get(d.parent).push(d.name);
    }
  }
  const result = new Map();
  [...departments].sort((a, b) => b.depth - a.depth).forEach((d) => {
    const set = new Set();
    for (const childName of childrenByName.get(d.name) || []) {
      const childNode = byName.get(childName);
      set.add(childNode.id);
      for (const id of result.get(childName) || []) set.add(id);
    }
    result.set(d.name, set);
  });
  return result;
}

function updateSettingsDeptSummary(departments, selected, descendantSets) {
  const summaryEl = $("#settings-dept-summary");
  if (!selected.size) {
    const total = departments.filter((d) => d.depth === 0).reduce((sum, d) => sum + d.count, 0);
    summaryEl.textContent = `Watching everything · ${total} product${total === 1 ? "" : "s"} in scope`;
    return;
  }
  const byId = new Map(departments.map((d) => [d.id, d]));
  // Only sum a selected id's count if it isn't already covered by some
  // OTHER selected id being its ancestor -- each node's own count is
  // already a rolled-up total including its descendants (see
  // queries.retailer_department_tree), so counting both would double-count.
  const topLevel = [...selected].filter((id) =>
    ![...selected].some((otherId) => otherId !== id && (descendantSets.get(byId.get(otherId)?.name) || new Set()).has(id))
  );
  const total = topLevel.reduce((sum, id) => sum + (byId.get(id)?.count || 0), 0);
  summaryEl.textContent = `${selected.size} selected explicitly · ${total} product${total === 1 ? "" : "s"} in scope incl. sub-departments`;
}

function setupSettingsDeptTree(detail) {
  const departments = detail.departments;
  const retailerId = detail.id;
  const selected = new Set(departments.filter((d) => d.watched).map((d) => d.id));
  const descendantSets = settingsDeptDescendantIdSets(departments);
  const expandKey = (name) => `settings_dept_expanded_${retailerId}_${name}`;

  function render() {
    const filterText = ($("#settings-dept-filter").value || "").toLowerCase().trim();
    const byName = new Map(departments.map((d) => [d.name, d]));
    let visibleNames = null;
    if (filterText) {
      visibleNames = new Set(departments.filter((d) => d.label.toLowerCase().includes(filterText)).map((d) => d.name));
      for (const name of [...visibleNames]) {
        let p = byName.get(name)?.parent;
        while (p) { visibleNames.add(p); p = byName.get(p)?.parent; }
      }
    }
    const isCollapsed = (d) => {
      if (filterText) return false;
      let p = d.parent;
      while (p) {
        if (localStorage.getItem(expandKey(p)) !== "1") return true;
        p = byName.get(p)?.parent;
      }
      return false;
    };

    const el = $("#settings-dept-tree");
    el.innerHTML = "";
    for (const d of departments) {
      if (visibleNames && !visibleNames.has(d.name)) continue;
      if (isCollapsed(d)) continue;
      const hasChildren = departments.some((x) => x.parent === d.name);
      const expanded = !!filterText || localStorage.getItem(expandKey(d.name)) === "1";
      const checked = selected.has(d.id);
      const hasCheckedDescendant = [...(descendantSets.get(d.name) || [])].some((id) => selected.has(id));

      const row = document.createElement("label");
      row.className = "settings-dept-row";
      row.style.paddingLeft = `${6 + d.depth * 16}px`;
      row.innerHTML = `
        <span class="settings-dept-toggle">${hasChildren ? (expanded ? "▾" : "▸") : ""}</span>
        <input type="checkbox" class="settings-dept-check" ${checked ? "checked" : ""} />
        <span class="settings-dept-name">${escapeHtml(d.label)}</span>
        <span class="settings-dept-count">${d.count}</span>
      `;
      if (hasChildren) {
        row.querySelector(".settings-dept-toggle").addEventListener("click", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          localStorage.setItem(expandKey(d.name), expanded ? "0" : "1");
          render();
        });
      }
      const checkbox = row.querySelector(".settings-dept-check");
      checkbox.indeterminate = !checked && hasCheckedDescendant;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(d.id);
        else selected.delete(d.id);
        render();
      });
      el.appendChild(row);
    }
    updateSettingsDeptSummary(departments, selected, descendantSets);
  }

  render();
  $("#settings-dept-filter").addEventListener("input", render);
  $("#settings-dept-save").addEventListener("click", async () => {
    const statusEl = $("#settings-dept-save-status");
    statusEl.textContent = "Saving…";
    try {
      await api(`/api/settings/retailers/${retailerId}/departments`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ department_ids: [...selected] }),
      });
      statusEl.textContent = "Saved -- takes effect on the next scan.";
    } catch (e) {
      statusEl.textContent = `Save failed: ${e.message}`;
    }
  });
}

async function renderSettingsNotifications() {
  const telegram = await api("/api/settings/telegram");
  $("#settings-main").innerHTML = `
    <h3>Notifications</h3>
    <dl>
      <dt>Alerts sent</dt><dd>${telegram.alerts_sent}</dd>
      <dt>Last alert</dt><dd>${telegram.last_alert_at ? new Date(telegram.last_alert_at).toLocaleString() : "never"}</dd>
    </dl>
  `;
}

async function renderSettingsDataTools() {
  const missing = await api("/api/admin/repair-missing-data/count").catch(() => null);
  $("#settings-main").innerHTML = `<h3>Data tools</h3>${dataToolsHtml(missing ? missing.missing : null)}`;
  setupDataTools();
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
  $("#deal-modal").hidden = false;

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
  needs_login: "Needs login",
};

// Header wireframe 5b gives "idle" and "scanning" their own dedicated UI
// (the quiet running total, and the row-2 status bar) -- the old
// always-visible badge would be redundant noise in both. It only needs to
// resurface for the states neither of those covers: still starting up, or
// something's actually wrong.
const SCAN_BADGE_STATES = new Set(["starting", "unreachable", "unknown", "needs_login"]);

function formatDuration(seconds) {
  if (seconds == null) return null;
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const mins = Math.round(s / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function formatEta(etaSeconds) {
  if (etaSeconds == null) return null;
  const mins = Math.round(etaSeconds / 60);
  return mins < 1 ? "<1 min left" : `~${mins} min left`;
}

// Keyed by a caller-chosen id (not the element) because the status bar
// that holds the big odometer gets its innerHTML fully rebuilt on every
// poll (see renderScanStatusBar) -- a DOM-attribute-based "previous
// value" would be wiped out the instant its element is recreated, right
// when the rollover highlight matters most (mid-scan, every 2-3s).
const _odometerPrevValues = {};

// Mechanical digit-counter odometer (header wireframe 5b): one bordered
// cell per digit, tabular numerals via CSS (see style.css's .odometer
// rules). Digits that changed since the last render get the accent
// "just rolled over" highlight, diffed right-aligned (least-significant
// digit first) so a value that grows a new leading digit doesn't
// misalign the comparison.
function renderOdometer(el, value, key) {
  if (!el || value == null) return;
  const digits = String(value).split("");
  const prevStr = _odometerPrevValues[key];
  const prevDigits = prevStr ? prevStr.split("") : [];
  const offset = digits.length - prevDigits.length;
  el.innerHTML = digits
    .map((d, i) => {
      const prevIdx = i - offset;
      const rolled = prevDigits.length > 0 && prevIdx >= 0 && prevDigits[prevIdx] !== d;
      // A gap every 3 digits from the right -- mechanical odometers don't
      // print commas, but a 7-digit total is unreadable with none at all.
      const fromRight = digits.length - i;
      const grouped = fromRight > 1 && fromRight % 3 === 1;
      return `<span class="odometer-digit${rolled ? " rolled" : ""}${grouped ? " odometer-group-gap" : ""}">${d}</span>`;
    })
    .join("");
  _odometerPrevValues[key] = String(value);
}

async function refreshScanStatus() {
  const status = await api("/api/scan/status");
  const state = status.scanner.state || "unknown";
  const scanInProgress = state === "scanning" || state === "starting";

  const badge = $("#scan-state-badge");
  badge.hidden = !SCAN_BADGE_STATES.has(state);
  if (!badge.hidden) {
    badge.textContent = SCAN_STATE_LABELS[state] || state;
    badge.className = `badge ${state}`;
  }

  // Disabled while a scan is already running so it's never ambiguous
  // whether clicking does anything -- confirmed source of confusion
  // 2026-08-31 (the button was always clickable regardless of state).
  const btn = $("#scan-now-btn");
  btn.disabled = scanInProgress;
  btn.textContent = scanInProgress ? "Scanning…" : "Scan now";

  renderScanIdleSummary(state, status);
  renderScanStatusBar(state, status);

  // A ticking odometer and a responsive progress bar want a much tighter
  // loop than the old 15s -- 2-3s while a scan is actually running, back
  // off to the idle cadence otherwise so an open-but-idle tab isn't
  // hammering /api/scan/status for nothing. Read by scheduleScanStatusPoll
  // (see main()) for the *next* wait, not this one -- so the very next
  // poll after a scan starts still lands quickly.
  window._scanStatusPollDelay = state === "scanning" ? 2500 : 15000;
}

function renderScanIdleSummary(state, status) {
  const wrap = $("#scan-idle-summary");
  const total = status.price_checks?.total;
  if (state === "scanning" || total == null) {
    wrap.hidden = true;
    return;
  }
  wrap.hidden = false;
  const lastScan = status.scanner.last_scan_started_at;
  $("#scan-idle-text").textContent = `${total.toLocaleString()} checks · last scan ${lastScan ? relTime(lastScan) : "never"}`;
}

function renderScanStatusBar(state, status) {
  const bar = $("#scan-status-bar");
  if (state !== "scanning") {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;

  // _progress.clear() runs at the top of every scan (scanner/main.py's
  // _scan_all) -- the first poll or two after "Scan now" can land before
  // the first checkpoint fires, so every field below is treated as
  // possibly absent rather than assuming phase/retailer are always set.
  const p = status.scanner.progress || {};
  const breadcrumb = [p.retailer, p.store, p.department].filter(Boolean).join(" › ");
  const cancelling = !!status.scanner.cancel_requested;

  const productParts = [];
  if (p.department_products_total) {
    productParts.push(`${p.department_products_checked || 0} of ${p.department_products_total} products`);
  } else if (p.products_checked) {
    productParts.push(`${p.products_checked} products checked`);
  }
  const eta = formatEta(status.scanner.eta_seconds);
  if (eta) productParts.push(eta);
  if (p.errors_count) productParts.push(`${p.errors_count} error(s)`);

  const fraction = status.scanner.progress_fraction;
  const lastMinute = status.price_checks?.last_minute ?? 0;

  bar.innerHTML = `
    <span class="scanbar-dot" aria-hidden="true"></span>
    <span class="scanbar-label">${cancelling ? "Cancelling…" : "Scanning"}</span>
    <span class="scanbar-breadcrumb">${escapeHtml(breadcrumb || "Starting…")}</span>
    <div class="scanbar-progress-wrap">
      <div class="scanbar-progress-text">${escapeHtml(productParts.join(" · ") || "Starting…")}</div>
      <div class="scanbar-progress-track"><div class="scanbar-progress-fill" style="width: ${fraction != null ? Math.round(fraction * 100) : 0}%"></div></div>
    </div>
    <button id="scan-cancel-btn" class="scanbar-cancel-btn" type="button" ${cancelling ? "disabled" : ""}>${cancelling ? "Cancelling…" : "Cancel scan"}</button>
    <div class="scanbar-divider" aria-hidden="true"></div>
    <div class="scanbar-odometer">
      <div class="scanbar-odometer-label">Total price checks performed</div>
      <div id="scan-odometer-big" class="odometer odometer-big"></div>
      <div class="scanbar-odometer-rate">+${lastMinute} in the last minute</div>
    </div>
  `;
  renderOdometer($("#scan-odometer-big"), status.price_checks?.total, "big");

  $("#scan-cancel-btn").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = "Cancelling…";
    // POST /api/scan/cancel is cooperative (scanner/orchestrator.py's
    // ScanCancelled) -- it can take a checkpoint or two to actually wind
    // down, hence the immediate optimistic "Cancelling…" rather than
    // waiting for state to flip to idle.
    await api("/api/scan/cancel", { method: "POST" }).catch(() => {});
    refreshScanStatus();
  });
}

// Recursive setTimeout, not setInterval -- refreshScanStatus adjusts
// window._scanStatusPollDelay every call (fast while scanning, slow
// idle), and a fixed setInterval can't change its own period. Wrapped in
// try/catch so one failed request (backend restart, brief network blip)
// doesn't silently kill polling for the rest of the session.
function scheduleScanStatusPoll() {
  clearTimeout(window._scanStatusPollTimer);
  window._scanStatusPollTimer = setTimeout(async () => {
    try {
      await refreshScanStatus();
    } catch (e) {
      console.error("refreshScanStatus failed", e);
    }
    scheduleScanStatusPoll();
  }, window._scanStatusPollDelay || 15000);
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
    loadDeals({ resetWindow: true });
  });
}

function setupFilters() {
  // Sidebar tree selections and the status-bar tags fire their own reload
  // (selectRetailer/selectStore/selectDepartment, renderStatusBar) -- this
  // only wires the filter row above the deal list.
  setupFiltersPopover();
  ["#f-clearance", "#f-penny", "#f-min-discount", "#f-price-min", "#f-price-max", "#f-in-stock", "#f-sort"].forEach(
    (sel) => $(sel).addEventListener("change", () => { updateFiltersCountBadge(); loadDeals({ resetWindow: true }); })
  );
  $("#f-search").addEventListener("input", () => {
    clearTimeout(window._searchDebounce);
    window._searchDebounce = setTimeout(() => loadDeals({ resetWindow: true }), 300);
  });
}

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (ev) => {
    if ($(".tab-btn.active")?.dataset.tab !== "deals") return;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    let rows = $$("#deal-list .deal-row");
    if (!rows.length) return;
    let idx = rows.findIndex((r) => r.classList.contains("focused"));

    if (ev.key === "j" || ev.key === "k") {
      ev.preventDefault();
      // J past the last *rendered* row shouldn't dead-end at the window
      // boundary -- pull in the next page first (a no-op once everything
      // fetched is already on screen) and re-query before moving focus.
      if (ev.key === "j" && idx === rows.length - 1) {
        extendDealWindow();
        rows = $$("#deal-list .deal-row");
      }
      rows.forEach((r) => r.classList.remove("focused"));
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
  setupShoppingTab();
  setupWalkingView();
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

  scheduleScanStatusPoll();
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
