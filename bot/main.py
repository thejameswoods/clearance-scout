"""Standalone Telegram bot: alerts on new deals, remote scan control.
Independent of the (separate, still-mid-migration) Hermes agent project's
own Telegram gateway — this bot is wired directly into this service.

Bot-internal state (bound chat id, paused flag) lives in a small JSON file
on a volume, not Postgres — it's bot session state, not scan/deal domain
data, so it doesn't belong in the shared schema.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from common import db

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("clearance_scout.bot")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_CHAT_IDS = {
    c.strip() for c in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if c.strip()
}
SCANNER_URL = os.environ.get("SCANNER_INTERNAL_URL", "http://scanner:8090")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://clearance-scout.lan")
POLL_INTERVAL_SECONDS = int(os.environ.get("ALERT_POLL_INTERVAL_SECONDS", "60"))

STATE_PATH = Path(os.environ.get("BOT_STATE_PATH", "/data/bot_state.json"))


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"chat_id": None, "paused": False}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def money(cents: int | None) -> str:
    return f"${cents / 100:.2f}" if cents is not None else ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    state = load_state()

    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text("Not authorized for this bot.")
        logger.warning("Rejected /start from unauthorized chat_id=%s", chat_id)
        return

    if state["chat_id"] and state["chat_id"] != chat_id and not ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            "This bot is already bound to another chat. Set TELEGRAM_ALLOWED_CHAT_IDS "
            "to allow more than one, or unbind by clearing the bot's state file."
        )
        return

    state["chat_id"] = chat_id
    state["paused"] = False
    save_state(state)
    await update.message.reply_text(
        "Bound. You'll get alerts here for new clearance/penny finds. Send /menu for controls."
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Scan now", callback_data="scan_now")],
            [InlineKeyboardButton("Status", callback_data="status")],
            [InlineKeyboardButton("Today's deals", callback_data="today")],
            [InlineKeyboardButton("Pause/resume alerts", callback_data="toggle_pause")],
        ]
    )
    await update.message.reply_text("What do you want to do?", reply_markup=keyboard)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "scan_now":
        try:
            httpx.post(f"{SCANNER_URL}/trigger-scan", timeout=5)
            await query.edit_message_text("Scan triggered.")
        except httpx.HTTPError as exc:
            await query.edit_message_text(f"Couldn't reach the scanner: {exc}")

    elif query.data == "status":
        try:
            status = httpx.get(f"{SCANNER_URL}/status", timeout=5).json()
            await query.edit_message_text(f"Scanner state: {status.get('state')}")
        except httpx.HTTPError as exc:
            await query.edit_message_text(f"Couldn't reach the scanner: {exc}")

    elif query.data == "today":
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT p.name, po.price_cents FROM deal d
                JOIN price_observation po ON po.id = d.latest_observation_id
                JOIN product p ON p.id = d.product_id
                WHERE d.status IN ('new','active') AND d.created_at::date = current_date
                ORDER BY d.created_at DESC LIMIT 10
                """
            ).fetchall()
        if not rows:
            await query.edit_message_text("No new deals found today yet.")
        else:
            lines = [f"• {r['name']} — {money(r['price_cents'])}" for r in rows]
            await query.edit_message_text("Today's deals:\n" + "\n".join(lines))

    elif query.data == "toggle_pause":
        state = load_state()
        state["paused"] = not state.get("paused", False)
        save_state(state)
        await query.edit_message_text(f"Alerts {'paused' if state['paused'] else 'resumed'}.")


async def shoppinglist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.name, po.price_cents, s.name AS store_name,
                   dept.name AS department_name, spl.aisle, spl.bay
            FROM deal d
            JOIN price_observation po ON po.id = d.latest_observation_id
            JOIN product p ON p.id = d.product_id
            JOIN store s ON s.id = d.store_id
            LEFT JOIN department dept ON dept.id = p.department_id
            LEFT JOIN store_product_location spl ON spl.product_id = p.id AND spl.store_id = s.id
            WHERE d.status = 'saved'
            ORDER BY s.name, dept.name NULLS LAST, p.name
            """
        ).fetchall()

    if not rows:
        await update.message.reply_text("Shopping list is empty — save a deal from the dashboard to add it here.")
        return

    lines = []
    current_store = None
    current_section = None
    for row in rows:
        if row["store_name"] != current_store:
            current_store = row["store_name"]
            current_section = None
            lines.append(f"\n📍 <b>{current_store}</b>")
        section = row["department_name"] or "Other"
        if section != current_section:
            current_section = section
            lines.append(f"  <i>{section}</i>")
        aisle = ""
        if row["aisle"]:
            aisle = f" (Aisle {row['aisle']}{'/' + row['bay'] if row['bay'] else ''})"
        lines.append(f"  • {row['name']} — {money(row['price_cents'])}{aisle}")

    await update.message.reply_text("\n".join(lines).strip(), parse_mode="HTML")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    department = context.args[0] if context.args else None
    try:
        httpx.post(
            f"{SCANNER_URL}/trigger-scan",
            params={"department": department} if department else {},
            timeout=5,
        )
        await update.message.reply_text("Scan triggered.")
    except httpx.HTTPError as exc:
        await update.message.reply_text(f"Couldn't reach the scanner: {exc}")


async def poll_and_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if not state.get("chat_id") or state.get("paused"):
        return

    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.id AS deal_id, p.name, po.price_cents, po.list_price_cents,
                   po.is_clearance, po.is_penny, s.name AS store_name,
                   spl.aisle, spl.bay
            FROM deal d
            JOIN price_observation po ON po.id = d.latest_observation_id
            JOIN product p ON p.id = d.product_id
            JOIN store s ON s.id = d.store_id
            LEFT JOIN store_product_location spl ON spl.product_id = p.id AND spl.store_id = s.id
            WHERE d.status = 'new'
              AND NOT EXISTS (
                  SELECT 1 FROM alert_sent a WHERE a.deal_id = d.id AND a.channel = 'telegram'
              )
            ORDER BY d.created_at ASC
            LIMIT 20
            """
        ).fetchall()

        for row in rows:
            tags = []
            if row["is_clearance"]:
                tags.append("CLEARANCE")
            if row["is_penny"]:
                tags.append("PENNY")
            location = f" · Aisle {row['aisle']}/{row['bay']}" if row["aisle"] else ""
            text = (
                f"{'🟡 ' if row['is_clearance'] else ''}{'🪙 ' if row['is_penny'] else ''}"
                f"{row['name']}\n{money(row['price_cents'])}"
                + (f" (was {money(row['list_price_cents'])})" if row["list_price_cents"] else "")
                + f"\n{row['store_name']}{location}\n{DASHBOARD_URL}/#deal-{row['deal_id']}"
            )
            message = await context.bot.send_message(chat_id=state["chat_id"], text=text)
            conn.execute(
                "INSERT INTO alert_sent (deal_id, channel, telegram_message_id) VALUES (%s, 'telegram', %s)",
                (row["deal_id"], message.message_id),
            )


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("shoppinglist", shoppinglist_command))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.job_queue.run_repeating(poll_and_alert, interval=POLL_INTERVAL_SECONDS, first=10)
    logger.info("Bot starting (long-polling, no inbound webhook)")
    app.run_polling()


if __name__ == "__main__":
    main()
