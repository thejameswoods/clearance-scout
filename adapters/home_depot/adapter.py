from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from ..base import (
    AuthResult,
    Department,
    PriceCheckResult,
    PriceObservation,
    ProductRef,
    RateLimitPolicy,
    RetailerAdapter,
    StoreInfo,
)
from .api_client import HOME_URL, PRICE_CHECK_MAX_BATCH, HomeDepotApiClient
from .clearance import detect_clearance as _detect_clearance
from .clearance import effective_price as _effective_price
from .clearance import stock_quantity as _stock_quantity
from .departments import discover_departments as _discover_departments
from .penny import detect_penny as _detect_penny

logger = logging.getLogger("clearance_scout.adapters.home_depot")

# HDScanner's own validated pagination limits for category listing (see
# api_client.py's module docstring for how these were confirmed).
CATEGORY_PAGE_SIZE = 48
CATEGORY_MAX_PAGES = 5  # 5*48=240 items/department -- more conservative than
                         # HDScanner's 15-page/720-item cap; WATCHED_DEPARTMENTS
                         # already narrows scope, so exhaustiveness matters less.

# HDScanner's own validated wave shape for phase-3 price checks: 5
# concurrent PRICE_CHECK_MAX_BATCH(16)-item requests = 80 items/wave, not
# ramped further ("proven safe, do not ramp" -- their own comment).
WAVE_PARALLEL_CHUNKS = 5


class HomeDepotAdapter(RetailerAdapter):
    retailer_slug = "home_depot"

    def authenticate(self, browser_ctx: Any) -> AuthResult:
        # Home Depot doesn't require a logged-in session to browse
        # products/prices/clearance status by store -- login only matters
        # for account-specific things (order history, Pro pricing, saved
        # lists) that this scanner doesn't touch. So: never gate scanning
        # on login here. This also means never navigating to /myaccount/
        # during a normal scan -- that's the account/auth surface, which
        # is reasonably the most heavily monitored part of the site for
        # fraud/bot signals, and there's no reason to poke it when nothing
        # downstream needs it.
        #
        # Login is tabled for now (2026-08-30): the real login flow itself
        # 403's against Home Depot's actual auth API even with Patchright's
        # CDP-level fingerprint patches, which is a harder problem than
        # this project needs to solve to find clearance deals. If a future
        # need reintroduces a login requirement (e.g. a retailer where
        # pricing genuinely requires an account), reintroduce a real check
        # here rather than assuming this pattern generalizes.
        return AuthResult(valid=True)

    def find_stores(
        self, browser_ctx: Any, zip_code: str, radius_miles: float
    ) -> Iterator[StoreInfo]:
        client = HomeDepotApiClient(browser_ctx)
        response = client.store_search(zip_code, radius_miles)
        stores = response.get("data", {}).get("storeSearch", {}).get("stores") or []
        for raw in stores:
            raw_distance = raw.get("distance")
            # storeSearch returns distance as a numeric string, not a
            # number (confirmed live 2026-08-31, contra the earlier
            # assumption baked into StoreInfo's type).
            distance = float(raw_distance) if raw_distance is not None else None
            # storeSearch has no radius param of its own (confirmed --
            # HDScanner doesn't use one either); apply it client-side.
            if distance is not None and distance > radius_miles:
                continue
            address_parts = raw.get("address") or {}
            address = ", ".join(
                p for p in (
                    address_parts.get("street"),
                    address_parts.get("city"),
                    address_parts.get("state"),
                    address_parts.get("postalCode"),
                ) if p
            ) or None
            yield StoreInfo(
                retailer_store_id=str(raw["storeId"]),
                zip_code=zip_code,
                name=raw.get("name"),
                address=address,
                distance_miles=distance,
            )

    def select_store(self, browser_ctx: Any, store: StoreInfo) -> None:
        # Confirmed: storeId is just a per-request GraphQL variable, not
        # site-side state (no cookie/page interaction needed to "select" a
        # store) -- see api_client.py. Recording it on the context is all
        # _require_store_id() needs.
        browser_ctx.clearance_scout_store_id = store.retailer_store_id

    def discover_departments(self, browser_ctx: Any) -> Iterator[Department]:
        yield from _discover_departments(browser_ctx)

    def list_products(
        self, browser_ctx: Any, department: Department
    ) -> Iterator[ProductRef]:
        client = HomeDepotApiClient(browser_ctx)
        store_id = self._require_store_id(browser_ctx)

        seen_item_ids: set[str] = set()
        for page in range(CATEGORY_MAX_PAGES):
            response = client.category_products(
                store_id, department.retailer_department_id,
                page_size=CATEGORY_PAGE_SIZE, start_index=page * CATEGORY_PAGE_SIZE,
            )
            search_model = response.get("data", {}).get("searchModel") or {}
            products = search_model.get("products") or []
            if not products:
                break
            for raw in products:
                item_id = raw.get("itemId")
                if not item_id or item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)
                # See CATEGORY_QUERY's docstring in api_client.py: the name
                # field here is an unverified addition to HDScanner's
                # original query. Falling back to the item_id keeps this
                # from crashing if it's missing -- but WATCH_KEYWORDS can't
                # actually filter anything meaningful without a real name,
                # so treat an all-item_id product list as a signal this
                # needs the fallback lookup mentioned in that docstring.
                name = (raw.get("identifiers") or {}).get("productLabel") or str(item_id)
                yield ProductRef(
                    retailer_product_id=str(item_id),
                    name=name,
                    department=department,
                )
            if len(products) < CATEGORY_PAGE_SIZE:
                break

    def check_price(
        self, browser_ctx: Any, product_ref: ProductRef, store: StoreInfo
    ) -> PriceObservation:
        # The base ABC still requires this single-item method, but the
        # orchestrator calls check_prices() (overridden below) as its
        # primary path -- this stays as a working fallback (e.g. for a
        # manual single-SKU lookup) sharing the same parsing logic via
        # _build_observation, not a second copy of it.
        client = HomeDepotApiClient(browser_ctx)
        response = client.media_price_inventory(store.retailer_store_id, [product_ref.retailer_product_id])
        products = response.get("data", {}).get("products") or []
        if not products:
            raise RuntimeError(
                f"Home Depot returned no data for item {product_ref.retailer_product_id} "
                "(delisted, invalid ID, or not carried at this store)"
            )
        observation = self._build_observation(products[0], product_ref, store)

        # Aisle/bay + canonical URL + image each cost an extra API call
        # (product_detail, then aislebay) -- only worth paying for a
        # confirmed hit, not every product checked. Matches HDScanner's own
        # behavior (its background.js only calls its equivalent enrichment
        # on items that already passed the clearance/penny filter, not the
        # full checked set).
        if observation.is_clearance or observation.is_penny:
            aisle, bay, canonical_url, image_url = self._enrich_confirmed_hit(client, product_ref, store)
            observation = PriceObservation(
                **{**observation.__dict__, "aisle": aisle, "bay": bay,
                   "canonical_url": canonical_url, "image_url": image_url}
            )

        return observation

    def _build_observation(
        self, raw: dict[str, Any], product_ref: ProductRef, store: StoreInfo
    ) -> PriceObservation:
        """The pricing/clearance/penny parsing shared by check_price() and
        check_prices() -- everything except enrichment (aisle/bay/
        canonical_url/image_url), which only applies to confirmed hits and
        is handled differently by each caller (one API call per hit vs.
        batched across a whole wave's hits -- see _enrich_confirmed_hit /
        _enrich_wave_hits)."""
        clearance_signal = self.detect_clearance(raw)
        pricing = raw.get("pricing") or {}
        fulfillment_state = self._pickup_fulfillment_state(raw)

        # Confirmed live 2026-08-31: pricing.value comes back null for some
        # real items at a given store (not delisted -- media_price_inventory
        # still returns the item, just with no price at this location, e.g.
        # special-order-only or a genuinely unpriced SKU). Same posture as
        # the "no data" case above: a clear, single-line error the
        # orchestrator already catches per-item, not a bare TypeError.
        if pricing.get("value") is None:
            raise RuntimeError(
                f"Home Depot returned no price for item {product_ref.retailer_product_id} "
                f"at store {store.retailer_store_id}"
            )

        is_clearance = bool(clearance_signal and clearance_signal.is_clearance)
        charged_price, reference_price = _effective_price(pricing, is_clearance)

        observation = PriceObservation(
            product_ref=product_ref,
            store=store,
            observed_at=datetime.now(timezone.utc),
            price_cents=int(round(float(charged_price) * 100)),
            list_price_cents=(
                int(round(float(reference_price) * 100)) if reference_price else None
            ),
            is_clearance=is_clearance,
            fulfillment_state=fulfillment_state,
            stock_quantity=_stock_quantity(raw),
            raw_signal=raw,
        )
        # is_penny depends on the fully-built observation (price + fulfillment
        # state together), so it's computed after construction and the
        # dataclass is frozen — rebuild with the flag set.
        return PriceObservation(
            **{**observation.__dict__, "is_penny": self.detect_penny(observation)}
        )

    def check_prices(
        self, browser_ctx: Any, product_refs: list[ProductRef], store: StoreInfo,
        rate_limiter: Any,
    ) -> Iterator[PriceCheckResult]:
        """Real batching, overriding RetailerAdapter's per-item default --
        modeled directly on HDScanner's own validated wave approach
        (confirmed live from their source, not guessed): chunks of
        PRICE_CHECK_MAX_BATCH(16) items, WAVE_PARALLEL_CHUNKS(5) chunks
        fired concurrently per wave (80 items/wave), paced between waves
        by _wave_pace_level's escalating/breather schedule -- their own
        numbers, not a generic formula. In practice this is roughly the
        same total work in a small fraction of the round trips the default
        one-item-at-a-time path needs.

        `rate_limiter` (a scanner.ratelimit.RateLimiter) isn't used for
        its own wait_before_next_request() pacing here -- that's this
        method's own concern now -- but record_403()/record_success() are
        still called so the same rate_limit_event log the dashboard reads
        keeps reflecting reality regardless of which check_prices()
        implementation ran.
        """
        if not product_refs:
            return

        client = HomeDepotApiClient(browser_ctx)
        store_id = store.retailer_store_id

        chunks = [
            product_refs[i:i + PRICE_CHECK_MAX_BATCH]
            for i in range(0, len(product_refs), PRICE_CHECK_MAX_BATCH)
        ]
        waves = [chunks[i:i + WAVE_PARALLEL_CHUNKS] for i in range(0, len(chunks), WAVE_PARALLEL_CHUNKS)]

        consecutive_wave_failures = 0
        waves_since_breather = 0

        for wave_index, wave_chunks in enumerate(waves):
            is_last_wave = wave_index == len(waves) - 1
            item_id_chunks = [[ref.retailer_product_id for ref in chunk] for chunk in wave_chunks]

            try:
                responses = client.media_price_inventory_wave(store_id, item_id_chunks)
            except Exception as exc:
                logger.exception("Wave-level fetch failed (wave %d/%d)", wave_index + 1, len(waves))
                for chunk in wave_chunks:
                    for ref in chunk:
                        yield PriceCheckResult(product_ref=ref, error=str(exc))
                consecutive_wave_failures += 1
                consecutive_wave_failures, waves_since_breather = self._wave_pause(
                    consecutive_wave_failures, waves_since_breather, is_last_wave
                )
                continue

            wave_had_success = False
            pending_hits: list[tuple[ProductRef, PriceObservation]] = []

            for chunk, response in zip(wave_chunks, responses):
                if response.get("error"):
                    for ref in chunk:
                        yield PriceCheckResult(product_ref=ref, error=response["error"])
                    if response.get("status") in (403, 429):
                        rate_limiter.record_403()
                    continue

                wave_had_success = True
                products_by_id = {p.get("itemId"): p for p in (response.get("data", {}).get("products") or [])}
                for ref in chunk:
                    raw = products_by_id.get(ref.retailer_product_id)
                    if raw is None:
                        yield PriceCheckResult(
                            product_ref=ref,
                            error=f"Home Depot returned no data for item {ref.retailer_product_id}",
                        )
                        continue
                    try:
                        observation = self._build_observation(raw, ref, store)
                    except Exception as exc:
                        yield PriceCheckResult(product_ref=ref, error=str(exc))
                        continue
                    if observation.is_clearance or observation.is_penny:
                        pending_hits.append((ref, observation))
                    else:
                        yield PriceCheckResult(product_ref=ref, observation=observation)

            if wave_had_success:
                rate_limiter.record_success()

            if pending_hits:
                for ref, observation in self._enrich_wave_hits(client, store, pending_hits):
                    yield PriceCheckResult(product_ref=ref, observation=observation)

            consecutive_wave_failures = 0 if wave_had_success else consecutive_wave_failures + 1
            consecutive_wave_failures, waves_since_breather = self._wave_pause(
                consecutive_wave_failures, waves_since_breather, is_last_wave
            )

    @staticmethod
    def _wave_pace_level(consecutive_failures: int) -> tuple[float, float, str, bool]:
        """Pure -- given the current consecutive-total-wave-failure count,
        returns (base_delay_seconds, jitter_seconds, level_name,
        resets_counter). HDScanner's own validated escalation: clean ->
        1-3s; 1-2 failures -> 8-15s ("light", doesn't reset -- a couple of
        blips isn't a real signal yet); 3-4 -> 45-90s ("moderate", resets);
        5+ -> 180-300s ("heavy", resets). Split out from _wave_pause so
        the schedule itself is testable without mocking time.sleep."""
        if consecutive_failures >= 5:
            return 180.0, 120.0, "heavy", True
        if consecutive_failures >= 3:
            return 45.0, 45.0, "moderate", True
        if consecutive_failures > 0:
            return 8.0, 7.0, "light", False
        return 1.0, 2.0, "ok", False

    def _wave_pause(
        self, consecutive_failures: int, waves_since_breather: int, is_last_wave: bool
    ) -> tuple[int, int]:
        """Sleeps between waves per _wave_pace_level, plus a flat breather
        every 10 clean waves (HDScanner's own numbers) regardless of the
        escalation state. Returns the (possibly reset) consecutive_failures
        and waves_since_breather counters. No-ops on the last wave -- no
        reason to pace after there's nothing left to send."""
        if is_last_wave:
            return consecutive_failures, waves_since_breather

        base, jitter, level, resets = self._wave_pace_level(consecutive_failures)
        if resets:
            consecutive_failures = 0

        waves_since_breather += 1
        if waves_since_breather >= 10 and consecutive_failures == 0:
            breather = 8.0 + random.uniform(0, 12)
            logger.info("Wave pacing: breather pause %.0fs", breather)
            time.sleep(breather)
            waves_since_breather = 0

        delay = base + random.uniform(0, jitter)
        if level != "ok":
            logger.warning("Wave pacing: %s rate limiting, backing off %.0fs", level, delay)
        time.sleep(delay)
        return consecutive_failures, waves_since_breather

    def _enrich_wave_hits(
        self, client: HomeDepotApiClient, store: StoreInfo,
        hits: list[tuple[ProductRef, PriceObservation]],
    ) -> list[tuple[ProductRef, PriceObservation]]:
        """The batched counterpart to _enrich_confirmed_hit: one concurrent
        product_detail_wave() call for every hit in a wave (instead of one
        product_detail() call per hit), plus aislebay() batched up to its
        own native 20-storeSkuId cap. A wave rarely has more than a
        handful of real hits, so this is a small, cheap addition on top of
        the price-check wave itself, not per-hit round trips."""
        item_ids = [ref.retailer_product_id for ref, _ in hits]
        try:
            details = client.product_detail_wave(store.retailer_store_id, item_ids)
        except Exception:
            logger.exception("Batch product_detail enrichment failed for a wave of %d hit(s)", len(hits))
            return hits  # unenriched is still a real, usable observation

        canonical_urls: dict[str, str | None] = {}
        image_urls: dict[str, str | None] = {}
        store_sku_ids: dict[str, str | None] = {}

        for (ref, _), detail in zip(hits, details):
            if detail.get("error"):
                logger.info("product_detail enrichment failed for %s: %s", ref.retailer_product_id, detail["error"])
                continue
            canonical_url, image_url, store_sku_id = self._parse_product_detail(detail)
            canonical_urls[ref.retailer_product_id] = canonical_url
            image_urls[ref.retailer_product_id] = image_url
            store_sku_ids[ref.retailer_product_id] = store_sku_id

        sku_to_ref_id = {sku: ref_id for ref_id, sku in store_sku_ids.items() if sku}
        aisle_bay: dict[str, tuple[str | None, str | None]] = {}
        sku_list = list(sku_to_ref_id.keys())
        for i in range(0, len(sku_list), 20):
            sku_chunk = sku_list[i:i + 20]
            try:
                ab = client.aislebay(store.retailer_store_id, sku_chunk)
                store_skus = ((ab.get("data") or {}).get("aislebay") or {}).get("storeSkus") or []
                for entry in store_skus:
                    sku = entry.get("storeSkuId")
                    if sku:
                        info = entry.get("aisleBayInfo") or {}
                        aisle_bay[sku] = (info.get("aisle"), info.get("bay"))
            except Exception:
                logger.exception("Batch aislebay enrichment failed for %d SKU(s)", len(sku_chunk))

        enriched: list[tuple[ProductRef, PriceObservation]] = []
        for ref, observation in hits:
            sku = store_sku_ids.get(ref.retailer_product_id)
            aisle, bay = aisle_bay.get(sku, (None, None)) if sku else (None, None)
            enriched.append((ref, PriceObservation(**{
                **observation.__dict__,
                "aisle": aisle, "bay": bay,
                "canonical_url": canonical_urls.get(ref.retailer_product_id),
                "image_url": image_urls.get(ref.retailer_product_id),
            })))
        return enriched

    @staticmethod
    def _parse_product_detail(detail: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        """Pure -- pulls (canonical_url, image_url, store_sku_id) out of a
        raw productClientOnlyProduct response. Shared by the per-hit
        (_enrich_confirmed_hit) and per-wave (_enrich_wave_hits) enrichment
        paths so the field-parsing logic (including the confirmed-live
        "<SIZE>" template substitution) lives in exactly one place."""
        product = (detail.get("data") or {}).get("product") or {}
        identifiers = product.get("identifiers") or {}
        canonical_url = identifiers.get("canonicalUrl")
        if canonical_url and not canonical_url.startswith("http"):
            canonical_url = f"{HOME_URL.rstrip('/')}{canonical_url}"

        images = (product.get("media") or {}).get("images") or []
        image_url = images[0].get("url", "").replace("<SIZE>", "400") if images else None

        store_sku_id = identifiers.get("storeSkuNumber")
        return canonical_url, image_url, store_sku_id

    def _enrich_confirmed_hit(
        self, client: HomeDepotApiClient, product_ref: ProductRef, store: StoreInfo
    ) -> tuple[str | None, str | None, str | None, str | None]:
        try:
            detail = client.product_detail(store.retailer_store_id, product_ref.retailer_product_id)
        except Exception:
            logger.exception("product_detail enrichment failed for %s", product_ref.retailer_product_id)
            return (None, None, None, None)

        canonical_url, image_url, store_sku_id = self._parse_product_detail(detail)
        aisle = bay = None
        if not store_sku_id:
            # Silent before this fix -- confirmed live 2026-08-31 a real
            # hit (204724933) came through with no aisle/bay and nothing
            # in the logs explaining why, because this branch had no log
            # line at all. Now it's explicit whether it's "HD didn't give
            # us a storeSkuNumber for this item" vs. the except-branch
            # below ("we had one but the aislebay call itself failed").
            logger.info("No storeSkuNumber for %s -- skipping aisle/bay lookup", product_ref.retailer_product_id)
        else:
            try:
                ab = client.aislebay(store.retailer_store_id, [store_sku_id])
                store_skus = ((ab.get("data") or {}).get("aislebay") or {}).get("storeSkus") or []
                if store_skus:
                    info = store_skus[0].get("aisleBayInfo") or {}
                    aisle, bay = info.get("aisle"), info.get("bay")
                else:
                    logger.info("aislebay returned no data for storeSkuId %s", store_sku_id)
            except Exception:
                logger.exception("aislebay enrichment failed for storeSkuId %s", store_sku_id)

        return (aisle, bay, canonical_url, image_url)

    def detect_clearance(self, raw_response: dict[str, Any]):
        return _detect_clearance(raw_response)

    def detect_penny(self, observation: PriceObservation) -> bool:
        return _detect_penny(observation)

    # No location_hint() override: aisle/bay is wired in directly in
    # check_price/check_prices via _enrich_confirmed_hit/_enrich_wave_hits
    # instead of this base-class hook. The real `aislebay` query needs a
    # storeSkuId (from product_detail's identifiers.storeSkuNumber), not
    # the itemId location_hint() is handed -- and it's only worth fetching
    # for a confirmed hit anyway, which this standalone hook wouldn't know.

    def rate_limit_policy(self) -> RateLimitPolicy:
        # Confirmed real values (not a guess from HDScanner's README prose,
        # which was more conservative than what their actual code does) --
        # see api_client.py's module docstring. Their 403/429 backoff is
        # 10s/30s/90s exponential + jitter (Akamai, not PerimeterX -- this
        # API layer isn't the login flow).
        #
        # This policy now only drives RetailerAdapter's default per-item
        # check_prices() fallback (unused in practice -- check_prices() is
        # overridden below with real wave-based batching, which paces
        # itself via _wave_pace_level using HDScanner's own validated
        # between-wave numbers instead of this per-item policy). Kept
        # correct/conservative regardless, since it's still what a future
        # single-item code path would fall back to.
        return RateLimitPolicy(
            min_delay_seconds=1.5,
            max_delay_seconds=3.0,
            backoff_on_403_seconds=10,
            max_backoff_seconds=90,
        )

    def _pickup_fulfillment_state(self, raw_product: dict[str, Any]) -> str | None:
        for option in raw_product.get("fulfillment", {}).get("fulfillmentOptions", []) or []:
            if option.get("type") != "pickup":
                continue
            for service in option.get("services", []) or []:
                for location in service.get("locations", []) or []:
                    inventory = location.get("inventory") or {}
                    if inventory.get("isInStock"):
                        return "in_stock"
        return None

    def _require_store_id(self, browser_ctx: Any) -> str:
        store_id = getattr(browser_ctx, "clearance_scout_store_id", None)
        if not store_id:
            raise RuntimeError(
                "No store selected on this browser context — call select_store() first."
            )
        return store_id
