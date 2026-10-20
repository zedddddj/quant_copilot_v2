import os
import sys
import logging
import asyncio
import time
import requests
from config import TELEGRAM_BOT_TOKEN, ENABLE_TELEGRAM
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
# ASYNC HTTP HEALTH CHECK SERVER (DÀNH RIÊNG CHO RENDER WEB SERVICE MIỄN PHÍ)
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
# KHỞI TẠO CÁC AGENT
# =========================================================================
client = MexcPublicClient()
universe = UniverseManager(client)
strategist = StrategistAgent(client)
risk_ctrl = RiskControllerAgent()
scout = ScoutAgent(client)
auditor = RiskAuditorAgent()
monitor = PositionMonitorAgent(client)

async def telegram_button_listener():
    """Lắng nghe nút bấm [ ✅ ĐÃ VÀO LỆNH ] trên Telegram"""
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN:
        return

    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            res = requests.get(url, params={"offset": offset, "timeout": 10}, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
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

                            cb_id = callback.get("id")
                            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={
                                "callback_query_id": cb_id,
                                "text": f"Đã ghi nhận vị thế {sym}! Bắt đầu giám sát.",
                                "show_alert": False
                            })
        except Exception:
            pass
        await asyncio.sleep(2)

async def position_monitor_loop():
    """Vòng lặp giám sát vị thế nền mỗi 60 giây"""
    while True:
        try:
            await asyncio.to_thread(monitor.check_active_positions)
        except Exception as e:
            logger.error(f"Lỗi monitor loop: {e}")
        await asyncio.sleep(60)

async def pipeline_scan_loop():
    """Chu kỳ quét thị trường 180 giây"""
    while True:
        try:
            # 1. THE STRATEGIST: Cập nhật vĩ mô & tin tức
            macro = await asyncio.to_thread(strategist.update_btc_macro)
            if not macro.get("allow_trade", True):
                logger.warning(f"⏸️ [TẠM DỪNG QUÉT]: {macro.get('news_reason') or 'BTC biến động sốc'}")
                await asyncio.sleep(180)
                continue

            btc_bias = macro.get("bias", "NEUTRAL")

            # 2. Lọc danh sách coin đủ thanh khoản >= 1.5M USDT
            eligible, _ = await asyncio.to_thread(universe.get_eligible_symbols, 1500000.0)
            target_symbols = [x["symbol"] for x in eligible[:120]]

            # 3. THE SCOUT: Quét ứng viên
            candidates = []
            for sym in target_symbols:
                c = await asyncio.to_thread(scout.scan_candidate, sym, btc_bias)
                if c:
                    candidates.append(c)
                await asyncio.sleep(0.02)

            # 4. THE AUDITOR & RISK CONTROLLER: Thẩm định và phát tín hiệu
            for item in candidates[:3]:
                funding = await asyncio.to_thread(client.get_funding_rate, item["symbol"])
                can_open, risk_reason = risk_ctrl.can_open_position(item["bias"], funding)
                if not can_open:
                    logger.info(f"Từ chối {item['symbol']} do Risk Controller: {risk_reason}")
                    continue

                is_valid, audit_reason, plan = await asyncio.to_thread(auditor.audit_trade, item)
                if is_valid:
                    await asyncio.to_thread(auditor.broadcast_signal_with_button, plan)
                    await asyncio.sleep(1.0)
                else:
                    logger.info(f"Từ chối {item['symbol']} do Auditor: {audit_reason}")

        except Exception as e:
            logger.error(f"Lỗi pipeline: {e}")

        await asyncio.sleep(180)

async def main():
    logger.info("🚀 KHỞI ĐỘNG HỆ THỐNG QUANT MULTI-AGENT COPILOT V2")
    # Khởi chạy đồng thời: Cổng HTTP cho Render + Vòng quét thị trường + Giám sát lệnh + Lắng nghe Telegram
    await asyncio.gather(
        start_health_server(),
        pipeline_scan_loop(),
        position_monitor_loop(),
        telegram_button_listener()
    )

if __name__ == "__main__":
    asyncio.run(main())
