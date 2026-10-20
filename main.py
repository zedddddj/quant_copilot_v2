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
    STATE_TRADES_FILE
)
from data.mexc_client import MexcPublicClient
from data.universe import UniverseManager
from agent_strategist import StrategistAgent
from agent_risk import RiskControllerAgent
from agent_scout import ScoutAgent
from agent_auditor import RiskAuditorAgent
from agent_monitor import PositionMonitorAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("MultiAgentEngineV2")

# =========================================================================
# ASYNC HTTP HEALTH CHECK SERVER CHO RENDER WEB SERVICE
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
# KHỞI TẠO ĐỒNG BỘ 5 AGENTS
# =========================================================================
client = MexcPublicClient()
universe = UniverseManager(client)
strategist = StrategistAgent(client)
risk_ctrl = RiskControllerAgent()
scout = ScoutAgent(client)
auditor = RiskAuditorAgent()
monitor = PositionMonitorAgent(client)

def send_startup_alert(macro_info: dict):
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    
    btc_p = macro_info.get("btc_price", 0.0)
    regime = macro_info.get("regime", "UNKNOWN")
    bias = macro_info.get("bias", "NEUTRAL")
    chg = macro_info.get("btc_change_15m_pct", 0.0)
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M:%S - %d/%m/%Y")
    
    msg = (
        f"🚀 <b>[QUANT COPILOT V2 ONLINE]</b>\n"
        f"• <b>Thời gian:</b> {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>BTC Macro:</b> <code>{regime}</code> (BTC: <b>${btc_p:,.1f}</b> | Bias: <b>{bias}</b>)\n"
        f"• <b>Cầu chì BTC:</b> <b>HOẠT ĐỘNG</b> (Biến động 15M: <b>{chg}%</b>)\n"
        f"• <b>Dual-Engine:</b> Beta Trend & Decoupled Alpha Breakout\n"
        f"• <b>Chu kỳ:</b> 180s/lần | <b>Độ sâu:</b> 60 nến 4H, 100 nến 1H/15M\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <i>Cơ chế Top-Down 5 Agents sẵn sàng!</i>"
    )
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=8)
    except Exception as e:
        logger.error(f"Lỗi gửi tin nhắn mở màn: {e}")

# =========================================================================
# TIẾN TRÌNH AGENT 1: STRATEGIST & NEWS RADAR (CHẠY MỖI 60s)
# =========================================================================
async def run_agent_strategist_loop():
    logger.info("Agent 1 (Strategist & News Radar) đã bắt đầu chạy.")
    while True:
        try:
            await asyncio.to_thread(strategist.update_btc_macro)
        except Exception as e:
            logger.error(f"Lỗi Strategist loop: {e}")
        await asyncio.sleep(60)

# =========================================================================
# TIẾN TRÌNH AGENT 2, 3, 5: SCOUT, AUDITOR & RISK (CHẠY MỖI 180s)
# =========================================================================
async def run_pipeline_scout_loop():
    logger.info("Agent 2, 3 & 5 (Scout, Auditor & Risk Controller) đã bắt đầu chạy.")
    while True:
        try:
            # 1. Đọc Gatekeeper từ macro_state.json
            macro = {}
            if os.path.exists(STATE_MACRO_FILE):
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

            regime = macro.get("regime", "RANGE_STABLE")
            btc_bias = macro.get("bias", "NEUTRAL")
            logger.info(f"🔍 [SCAN] Trạng thái BTC: {regime} ({btc_bias}). Bắt đầu quét Altcoin...")

            # Kéo nến 15M của BTC để làm mốc tính hệ số tương quan
            btc_raw = await asyncio.to_thread(client.get_kline, "BTC_USDT", "Min15", 35)
            btc_candles_15m = [scout._extract_ohlc(k) for k in (btc_raw or []) if scout._extract_ohlc(k)[3] > 0][:-1]

            # 2. Lọc sàn thanh khoản >= 1.5M USDT
            eligible, _ = await asyncio.to_thread(universe.get_eligible_symbols, 1500000.0)
            target_symbols = [x["symbol"] for x in eligible[:120]]

            # 3. Agent 2 quét ứng viên
            candidates = []
            for sym in target_symbols:
                c = await asyncio.to_thread(scout.scan_candidate, sym, macro, btc_candles_15m)
                if c:
                    candidates.append(c)
                await asyncio.sleep(0.02)

            logger.info(f"🎯 Scout: Tìm thấy {len(candidates)} ứng viên tiềm năng.")

            # 4. Agent 3 Thẩm định & Agent 5 Kiểm soát Rủi ro
            for item in candidates[:3]:
                funding = await asyncio.to_thread(client.get_funding_rate, item["symbol"])
                can_open, risk_reason = risk_ctrl.can_open_position(item["bias"], funding)
                if not can_open:
                    logger.info(f"🛡️ [RISK VETO] {item['symbol']}: {risk_reason}")
                    continue

                is_valid, audit_reason, plan = await asyncio.to_thread(auditor.audit_trade, item)
                if is_valid:
                    plan["engine_mode"] = item.get("engine_mode", "ALPHA")
                    plan["signal_desc"] = item.get("signal_desc", "")
                    logger.info(f"🚀 [DUYỆT LỆNH] Phát tín hiệu {plan['symbol']} ({plan['direction']})")
                    await asyncio.to_thread(auditor.broadcast_signal, plan)
                    await asyncio.sleep(1.0)
                else:
                    logger.info(f"🛡️ [AUDITOR VETO] {item['symbol']}: {audit_reason}")

        except Exception as e:
            logger.error(f"Lỗi pipeline loop: {e}")

        await asyncio.sleep(180)

# =========================================================================
# TIẾN TRÌNH AGENT 4: POSITION MONITOR (CHẠY MỖI 45s)
# =========================================================================
async def run_agent_monitor_loop():
    logger.info("Agent 4 (Position Monitor Copilot) đã bắt đầu chạy.")
    while True:
        try:
            await asyncio.to_thread(monitor.check_active_positions)
        except Exception as e:
            logger.error(f"Lỗi Position Monitor loop: {e}")
        await asyncio.sleep(45)

# =========================================================================
# TIẾN TRÌNH TELEGRAM HANDLER (LẮNG NGHE /status VÀ NÚT BẤM)
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

                    # Xử lý lệnh /status
                    msg = update.get("message")
                    if msg and msg.get("text") == "/status":
                        macro = {}
                        if os.path.exists(STATE_MACRO_FILE):
                            try:
                                with open(STATE_MACRO_FILE, "r", encoding="utf-8") as f:
                                    macro = json.load(f)
                            except Exception:
                                pass
                        
                        trades = monitor.load_trades()
                        active_trades = [t for t in trades.values() if t.get("status") == "OPEN"]
                        active_syms = ", ".join([f"{t['symbol']}({t['direction']})" for t in active_trades]) or "Không có"
                        
                        status_msg = (
                            f"📊 <b>[BÁO CÁO HỆ THỐNG /STATUS]</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"• <b>BTC:</b> ${macro.get('btc_price', 0):,.1f} | {macro.get('regime')} ({macro.get('bias')})\n"
                            f"• <b>Cầu chì:</b> {'🔴 KHÓA' if macro.get('circuit_breaker') else '🟢 BÌNH THƯỜNG'}\n"
                            f"• <b>Vị thế đang giám sát:</b> {len(active_trades)} ({active_syms})\n"
                            f"• <b>Hạ tầng:</b> 5 Agents đang hoạt động đồng bộ."
                        )
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": TELEGRAM_CHAT_ID,
                            "text": status_msg,
                            "parse_mode": "HTML"
                        })

                    # Xử lý nút bấm Inline [ ✅ ĐÃ VÀO LỆNH ]
                    callback = update.get("callback_query")
                    if callback:
                        cb_data = callback.get("data", "")
                        if cb_data.startswith("ENTER_"):
                            parts = cb_data.split("_")
                            sym, direction = parts[1], parts[2]
                            entry, sl, tp1, tp2 = float(parts[3]), float(parts[4]), float(parts[5]), float(parts[6])

                            monitor.register_trade(sym, direction, entry, sl, tp1, tp2)

                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                                "callback_query_id": callback.get("id"),
                                "text": f"Đã ghi nhận vị thế {sym}! Bắt đầu giám sát.",
                                "show_alert": False
                            })
        except Exception:
            pass
        await asyncio.sleep(2)

# =========================================================================
# HÀM CHÍNH KHỞI CHẠY HỆ THỐNG (SUPERVISOR PATTERN)
# =========================================================================
async def supervisor_task(coro_func, task_name: str):
    while True:
        try:
            await coro_func()
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error(f"🚨 [LỖI TIẾN TRÌNH] {task_name} bị dừng: {e}. Tự khởi động lại sau 10 giây...")
            await asyncio.sleep(10)

async def main():
    logger.info(">>> KHỞI ĐỘNG HỆ SINH THÁI QUANT MULTI-AGENT COPILOT V2...")
    
    # 1. Khởi tạo trạng thái ban đầu của BTC
    initial_macro = await asyncio.to_thread(strategist.update_btc_macro)
    send_startup_alert(initial_macro)

    # 2. Chạy đồng thời Health Server và 4 tiến trình của 5 Agents
    await asyncio.gather(
        start_health_server(),
        supervisor_task(run_agent_strategist_loop, "Agent 1: Strategist"),
        supervisor_task(run_pipeline_scout_loop, "Agent 2, 3 & 5: Scout & Auditor & Risk"),
        supervisor_task(run_agent_monitor_loop, "Agent 4: Position Monitor"),
        supervisor_task(run_telegram_listener, "Telegram Handler")
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Hệ thống đã dừng an toàn.")
