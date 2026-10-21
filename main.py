import os
import sys
import logging
import asyncio
import time
import requests
import datetime
import json

from config import (
    TELEGRAM_BOT_TOKEN, 
    TELEGRAM_CHAT_ID, 
    ENABLE_TELEGRAM, 
    VN_TZ, 
    STATE_MACRO_FILE,
    STATE_TRADES_FILE,
    STATE_RISK_FILE
)
from data.mexc_client import MexcPublicClient
from data.universe import UniverseManager
from agent_strategist import StrategistAgent, run_strategist_and_news_radar
from agent_risk import RiskControllerAgent
from agent_scout import ScoutAgent
from agent_auditor import RiskAuditorAgent
from agent_monitor import PositionMonitorAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("MultiAgentEngineV2")

# =========================================================================
# 1. ASYNC HTTP HEALTH CHECK SERVER CHO RENDER WEB SERVICE
# =========================================================================
async def handle_http_request(reader, writer):
    try:
        await reader.read(512)
        response = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "Content-Length: 43\r\n"
            "Connection: close\r\n\r\n"
            "Quant Copilot V2 Engine is Live and Healthy!"
        )
        writer.write(response.encode("utf-8"))
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

async def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = await asyncio.start_server(handle_http_request, "0.0.0.0", port)
    logger.info(f"🌐 [PORT BINDING] Server HTTP Health Check đã mở ngay cổng {port} cho Render!")
    async with server:
        await server.serve_forever()

# =========================================================================
# 2. KHỞI TẠO CÁC AGENT ĐỒNG BỘ
# =========================================================================
client = MexcPublicClient()
universe = UniverseManager(client)
strategist = StrategistAgent(client)
risk_ctrl = RiskControllerAgent()
scout = ScoutAgent(client)
auditor = RiskAuditorAgent()
monitor = PositionMonitorAgent(client)

def broadcast_scout_discovery(item: dict) -> Optional[int]:
    """Agent 2 phát hiện mã tiềm năng (Tin gốc msg_2 của Chuỗi 2)"""
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    sym = item["symbol"]
    tier = item.get("tier", "TIER_1_STRONG_TREND")
    tier_tag = "🚀 [TIER 1 - SÓNG MẠNH]" if tier == "TIER_1_STRONG_TREND" else "⚡ [TIER 2 - SCALP BIÊN]"
    msg = (
        f"🤖 <b>[AGENT 2 - SCOUT SĂN TÍN HIỆU]</b>\n"
        f"<b>{tier_tag}</b>: Phát hiện ứng viên <b>{sym}</b> ({item['bias']})\n"
        f"• <b>Đặc điểm:</b> {item.get('signal_desc')}\n"
        f"• <i>Chuyển giao cho Agent 3 thẩm định an toàn và tính toán Margin rủi ro $4.00...</i>"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=8)
        if res.status_code == 200:
            return res.json().get("result", {}).get("message_id")
    except Exception:
        pass
    return None

# =========================================================================
# 3. TIẾN TRÌNH AGENT 2, 3, 5: SCOUT, AUDITOR & RISK (CHUỖI 2)
# =========================================================================
async def run_pipeline_scout_loop():
    logger.info("Agent 2, 3 & 5 (Scout, Auditor & Risk Controller) bắt đầu chu kỳ 180s...")
    while True:
        try:
            macro = getattr(strategist, "last_state", {})
            if not macro and os.path.exists(STATE_MACRO_FILE):
                try:
                    with open(STATE_MACRO_FILE, "r", encoding="utf-8") as f:
                        macro = json.load(f)
                except Exception:
                    pass

            if not macro.get("allow_trade", True):
                freeze_reason = macro.get("freeze_reason", "Cầu chì kích hoạt")
                logger.warning(f"⏸️ [GATEKEEPER KHÓA]: {freeze_reason}")
                await asyncio.sleep(180)
                continue

            # Tầng 1: Lọc thô siêu tốc < 1s lấy Top 25-30 coin sôi động
            top_symbols = await asyncio.to_thread(universe.get_top_active_symbols, 1500000.0, 30)

            # Tầng 2: Lọc sâu song song < 5s đa khung thời gian
            candidates = await scout.scan_market_parallel(top_symbols, macro)
            logger.info(f"🎯 Scout: Lọc được {len(candidates)} ứng viên thỏa mãn tiêu chuẩn Tier 1 / Tier 2.")

            # THỰC THI CHUỖI 2: SCOUT (msg_2) -> AUDITOR (Reply msg_2 & tạo msg_3) -> MONITOR (Reply msg_3)
            for item in candidates[:3]:
                funding = await asyncio.to_thread(client.get_funding_rate, item["symbol"])
                can_open, risk_reason = risk_ctrl.can_open_position(item["bias"], funding)
                if not can_open:
                    logger.info(f"🛡️ [RISK VETO] {item['symbol']}: {risk_reason}")
                    continue

                # 1. Scout phát tin phát hiện (Tin gốc msg_2)
                scout_msg_id = await asyncio.to_thread(broadcast_scout_discovery, item)
                item["scout_msg_id"] = scout_msg_id

                # 2. Auditor thẩm định và Reply vào tin nhắn của Scout
                is_valid, audit_reason, plan = await asyncio.to_thread(auditor.audit_trade, item)
                if is_valid:
                    logger.info(f"🚀 [DUYỆT LỆNH] Phát tín hiệu {plan['symbol']} ({plan['tier']})")
                    await asyncio.to_thread(auditor.broadcast_signal, plan)
                    await asyncio.sleep(1.0)
                else:
                    logger.info(f"🛡️ [AUDITOR VETO] {item['symbol']}: {audit_reason}")

        except Exception as e:
            logger.error(f"Lỗi pipeline loop: {e}")

        await asyncio.sleep(180)

# =========================================================================
# 4. TIẾN TRÌNH AGENT 4: POSITION MONITOR COPILOT (MỖI 60s)
# =========================================================================
async def run_agent_monitor_loop():
    logger.info("Agent 4 (Position Monitor Copilot) bắt đầu chu kỳ 60s...")
    while True:
        try:
            await asyncio.to_thread(monitor.check_active_positions)
        except Exception as e:
            logger.error(f"Lỗi Position Monitor loop: {e}")
        await asyncio.sleep(60)

# =========================================================================
# 5. TIẾN TRÌNH TELEGRAM HANDLER (LẮNG NGHE /status VÀ NÚT BẤM INLINE)
# =========================================================================
async def run_telegram_listener():
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN:
        return

    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    logger.info("Telegram Handler đã kích hoạt.")

    while True:
        try:
            res = await asyncio.to_thread(requests.get, url, params={"offset": offset, "timeout": 10}, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    # 1. Xử lý lệnh /status (3 tầng dự phòng chuẩn xác)
                    msg = update.get("message")
                    if msg and msg.get("text") == "/status":
                        macro = getattr(strategist, "last_state", {})
                        if not macro and os.path.exists(STATE_MACRO_FILE):
                            try:
                                with open(STATE_MACRO_FILE, "r", encoding="utf-8") as f:
                                    macro = json.load(f)
                            except Exception:
                                pass
                        
                        btc_price = float(macro.get("btc_price", 0.0) or 0.0)
                        if btc_price <= 0.0:
                            try:
                                tickers = await asyncio.to_thread(client.get_all_tickers)
                                for t in tickers:
                                    if t.get("symbol") == "BTC_USDT":
                                        btc_price = float(t.get("lastPrice", 0.0) or 0.0)
                                        break
                            except Exception:
                                pass

                        regime = macro.get("regime", "RANGE_STABLE")
                        bias = macro.get("bias", "BULLISH_ACCUMULATION")
                        chg_15m = macro.get("btc_change_15m_pct", 0.0)
                        funding_btc = macro.get("funding_btc", 0.0)
                        cb_status = "🔴 KÍCH HOẠT KHÓA" if macro.get("circuit_breaker") else "🟢 BÌNH THƯỜNG"
                        
                        trades = monitor.load_trades()
                        active_trades = [t for t in trades.values() if t.get("status") == "OPEN"]
                        active_syms = ", ".join([f"{t['symbol']}({t['direction']})" for t in active_trades]) or "Không có"
                        
                        risk_state = risk_ctrl.load_risk_state()
                        recent_sl_count = len(risk_state.get("recent_stop_losses", []))

                        status_msg = (
                            f"📊 <b>[BÁO CÁO HỆ THỐNG /STATUS]</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"• <b>BTC:</b> <code>${btc_price:,.1f}</code> | <b>{regime}</b> (<b>{bias}</b>)\n"
                            f"• <b>Nến đóng 15M gần nhất:</b> <b>{chg_15m:+.2f}%</b> | <b>Funding:</b> <code>{funding_btc:+0.4f}%</code>\n"
                            f"• <b>Cầu chì BTC:</b> {cb_status}\n"
                            f"• <b>Vị thế đang giám sát:</b> <b>{len(active_trades)}</b> ({active_syms})\n"
                            f"• <b>Chuỗi Stop Loss gần nhất:</b> <b>{recent_sl_count}/2</b>\n"
                            f"• <b>Hạ tầng:</b> 5 Agents giao tiếp theo chuỗi (Threaded Reply)."
                        )
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": TELEGRAM_CHAT_ID,
                            "text": status_msg,
                            "parse_mode": "HTML"
                        })

                    # 2. Xử lý nút bấm Inline [ ✅ ĐÃ VÀO LỆNH ] (Kích hoạt Agent 4 reply tiếp vào msg_3)
                    callback = update.get("callback_query")
                    if callback:
                        cb_data = callback.get("data", "")
                        if cb_data.startswith("ENTER_"):
                            parts = cb_data.split("_")
                            sym, direction = parts[1], parts[2]
                            entry, sl, tp1, tp2 = float(parts[3]), float(parts[4]), float(parts[5]), float(parts[6])
                            tier = parts[7] if len(parts) > 7 else "TIER_1_STRONG_TREND"
                            auditor_msg_id = int(parts[8]) if len(parts) > 8 else None

                            monitor.register_trade(sym, direction, entry, sl, tp1, tp2, tier, auditor_msg_id)

                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                                "callback_query_id": callback.get("id"),
                                "text": f"Đã ghi nhận vị thế {sym}! Agent 4 bắt đầu giám sát.",
                                "show_alert": False
                            })
        except Exception:
            pass
        await asyncio.sleep(2)

# =========================================================================
# 6. SUPERVISOR PATTERN & ENTRYPOINT
# =========================================================================
async def supervisor_task(coro_func, task_name: str):
    while True:
        try:
            logger.info(f">>> Khởi chạy tác vụ: {task_name}")
            await coro_func()
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error(f"🚨 [LỖI TIẾN TRÌNH] {task_name} bị dừng: {e}. Tự phục hồi sau 10 giây...")
            await asyncio.sleep(10)

async def main():
    logger.info(">>> KHỞI ĐỘNG HỆ SINH THÁI QUANT COPILOT V2 (5-AGENT THREADED)...")
    await asyncio.gather(
        start_health_server(),
        supervisor_task(run_strategist_and_news_radar, "Agent 1: Strategist & News Radar"),
        supervisor_task(run_pipeline_scout_loop, "Agent 2, 3 & 5: Scout & Auditor & Risk"),
        supervisor_task(run_agent_monitor_loop, "Agent 4: Position Monitor"),
        supervisor_task(run_telegram_listener, "Telegram Handler")
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Hệ thống đã dừng an toàn.")
