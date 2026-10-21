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
    """Phản hồi HTTP 200 OK cho Render Health Check mà không cần thư viện ngoài"""
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

def send_startup_alert(macro_info: dict):
    """Gửi thông báo khởi động kèm trạng thái vĩ mô ban đầu lên Telegram"""
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
# 3. TIẾN TRÌNH AGENT 2, 3, 5: SCOUT, AUDITOR & RISK CONTROLLER (MỖI 180s)
# =========================================================================
async def run_pipeline_scout_loop():
    logger.info("Agent 2, 3 & 5 (Scout, Auditor & Risk Controller) đã bắt đầu chạy.")
    while True:
        try:
            # 1. Đọc Gatekeeper từ macro_state.json hoặc bộ nhớ RAM của Agent 1
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

            regime = macro.get("regime", "RANGE_STABLE")
            btc_bias = macro.get("bias", "NEUTRAL")
            logger.info(f"🔍 [SCAN] Trạng thái BTC: {regime} ({btc_bias}). Bắt đầu quét Altcoin...")

            # Kéo nến 15M của BTC để làm mốc tính hệ số tương quan (Correlation)
            btc_raw = await asyncio.to_thread(client.get_kline, "BTC_USDT", "Min15", 35)
            btc_candles_15m = [scout._extract_ohlc(k) for k in (btc_raw or []) if scout._extract_ohlc(k)[3] > 0][:-1]

            # 2. Lọc sàn thanh khoản >= 1.5M USDT
            eligible, _ = await asyncio.to_thread(universe.get_eligible_symbols, 1500000.0)
            target_symbols = [x["symbol"] for x in eligible[:120]]

            # 3. Agent 2 quét ứng viên theo 2 chế độ (Beta Trend & Decoupled Alpha)
            candidates = []
            for sym in target_symbols:
                c = await asyncio.to_thread(scout.scan_candidate, sym, macro, btc_candles_15m)
                if c:
                    candidates.append(c)
                await asyncio.sleep(0.02)

            logger.info(f"🎯 Scout: Tìm thấy {len(candidates)} ứng viên tiềm năng.")

            # 4. Agent 3 Thẩm định an toàn & Agent 5 Kiểm soát Rủi ro
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
# 4. TIẾN TRÌNH AGENT 4: POSITION MONITOR COPILOT (MỖI 45s)
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
# 5. TIẾN TRÌNH TELEGRAM HANDLER (LẮNG NGHE /status VÀ NÚT BẤM)
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

                    # 1. Xử lý lệnh /status (3 tầng dự phòng chống lỗi $0.0)
                    msg = update.get("message")
                    if msg and msg.get("text") == "/status":
                        # Tầng 1: Đọc từ bộ nhớ RAM của Agent 1
                        macro = getattr(strategist, "last_state", {})
                        
                        # Tầng 2: Nếu RAM rỗng, đọc từ macro_state.json
                        if not macro and os.path.exists(STATE_MACRO_FILE):
                            try:
                                with open(STATE_MACRO_FILE, "r", encoding="utf-8") as f:
                                    macro = json.load(f)
                            except Exception:
                                pass
                        
                        btc_price = float(macro.get("btc_price", 0.0) or 0.0)
                        
                        # Tầng 3: Nếu vẫn rỗng -> Gọi trực tiếp sàn MEXC Realtime
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
                        bias = macro.get("bias", "NEUTRAL")
                        chg_15m = macro.get("btc_change_15m_pct", 0.0)
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
                            f"• <b>Biến động BTC 15M:</b> <b>{chg_15m}%</b>\n"
                            f"• <b>Cầu chì BTC:</b> {cb_status}\n"
                            f"• <b>Vị thế đang giám sát:</b> <b>{len(active_trades)}</b> ({active_syms})\n"
                            f"• <b>Chuỗi Stop Loss gần nhất:</b> <b>{recent_sl_count}/2</b>\n"
                            f"• <b>Hạ tầng:</b> 5 Agents đang hoạt động độc lập."
                        )
                        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": TELEGRAM_CHAT_ID,
                            "text": status_msg,
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

# =========================================================================
# 6. SUPERVISOR PATTERN & ENTRYPOINT
# =========================================================================
async def supervisor_task(coro_func, task_name: str):
    """Tự động phục hồi tác vụ sau 10 giây nếu gặp lỗi ngoại lệ mà không làm crash app"""
    while True:
        try:
            logger.info(f">>> Khởi chạy tác vụ: {task_name}")
            await coro_func()
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error(f"🚨 [LỖI TIẾN TRÌNH] {task_name} bị dừng: {e}. Tự khởi động lại sau 10 giây...")
            await asyncio.sleep(10)

async def main():
    logger.info(">>> KHỞI ĐỘNG HỆ SINH THÁI QUANT MULTI-AGENT COPILOT V2...")
    
    # 1. Khởi tạo trạng thái ban đầu của BTC ngay lập tức
    initial_macro = await asyncio.to_thread(strategist.update_btc_macro)
    send_startup_alert(initial_macro)

    # 2. Chạy đồng thời Health Server cho Render và 4 tiến trình độc lập
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
