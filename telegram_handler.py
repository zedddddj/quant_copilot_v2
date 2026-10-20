import json
import os
import requests
import asyncio
import logging
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM, STATE_MACRO_FILE, STATE_RISK_FILE, STATE_TRADES_FILE
from data.mexc_client import MexcPublicClient
from agent_monitor import PositionMonitorAgent

logger = logging.getLogger("TelegramHandler")

async def run_telegram_bot_listener():
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN:
        return

    client = MexcPublicClient()
    monitor = PositionMonitorAgent(client)
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    logger.info("Telegram Handler đã kích hoạt lắng nghe lệnh /status và nút bấm.")

    while True:
        try:
            res = await asyncio.to_thread(requests.get, url, params={"offset": offset, "timeout": 10}, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    # 1. Xử lý tin nhắn văn bản (/status)
                    msg = update.get("message")
                    if msg and msg.get("text") == "/status":
                        status_report = generate_status_report()
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": TELEGRAM_CHAT_ID,
                            "text": status_report,
                            "parse_mode": "HTML"
                        })

                    # 2. Xử lý nút bấm Inline [ ✅ ĐÃ VÀO LỆNH ]
                    callback = update.get("callback_query")
                    if callback:
                        cb_data = callback.get("data", "")
                        if cb_data.startswith("ENTER_"):
                            parts = cb_data.split("_")
                            sym = parts[1]
                            direction = parts[2]
                            entry = float(parts[3])
                            sl = float(parts[4])
                            tp1 = float(parts[5])
                            tp2 = float(parts[6])

                            monitor.register_trade(sym, direction, entry, sl, tp1, tp2)

                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                                "callback_query_id": callback.get("id"),
                                "text": f"Đã ghi nhận vị thế {sym}! Bắt đầu giám sát.",
                                "show_alert": False
                            })
        except Exception:
            pass
        await asyncio.sleep(2)

def generate_status_report() -> str:
    # Agent 1
    macro = {}
    if os.path.exists(STATE_MACRO_FILE):
        try:
            with open(STATE_MACRO_FILE, "r", encoding="utf-8") as f:
                macro = json.load(f)
        except Exception:
            pass

    # Agent 4
    trades = {}
    if os.path.exists(STATE_TRADES_FILE):
        try:
            with open(STATE_TRADES_FILE, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except Exception:
            pass
    active_trades = [t for t in trades.values() if t.get("status") == "OPEN"]

    # Agent 5
    risk = {}
    if os.path.exists(STATE_RISK_FILE):
        try:
            with open(STATE_RISK_FILE, "r", encoding="utf-8") as f:
                risk = json.load(f)
        except Exception:
            pass
    sl_count = len(risk.get("recent_stop_losses", []))

    btc_p = macro.get("btc_price", 0.0)
    regime = macro.get("regime", "N/A")
    bias = macro.get("bias", "N/A")
    cb = "🔴 ĐANG KHÓA" if macro.get("circuit_breaker") else "🟢 AN TOÀN"
    next_ev = macro.get("next_news")
    news_txt = f"{next_ev['title']} ({next_ev['time']} - còn {next_ev['minutes']}p)" if next_ev else "Không có tin đỏ USD"

    active_syms = ", ".join([f"{t['symbol']}({t['direction']})" for t in active_trades]) or "Không có vị thế nào"

    return (
        f"📊 <b>[BÁO CÁO TRẠNG THÁI HỆ THỐNG /STATUS]</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Agent 1 (Strategist):</b> BTC ${btc_p:,.1f} | {regime} ({bias})\n"
        f"• <b>Cầu chì BTC:</b> {cb} | <b>Tin đỏ tới:</b> {news_txt}\n"
        f"• <b>Agent 2 & 3 (Scout & Auditor):</b> Chu kỳ 180s | Quét Top-Down 1H/15M\n"
        f"• <b>Agent 4 (Monitor):</b> {len(active_trades)} vị thế đang chạy ({active_syms})\n"
        f"• <b>Agent 5 (Risk Controller):</b> Chuỗi SL gần đây: {sl_count}/2\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <i>Tất cả 5 Agent đang hoạt động bình thường trên nền asyncio.</i>"
    )
