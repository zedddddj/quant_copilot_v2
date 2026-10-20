import os
import sys
import logging
import asyncio
import time
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM, VN_TZ
import datetime
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
# ASYNC HTTP HEALTH CHECK SERVER CHO RENDER
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
# KHỞI TẠO CÁC AGENT
# =========================================================================
client = MexcPublicClient()
universe = UniverseManager(client)
strategist = StrategistAgent(client)
risk_ctrl = RiskControllerAgent()
scout = ScoutAgent(client)
auditor = RiskAuditorAgent()
monitor = PositionMonitorAgent(client)

def send_startup_alert():
    """Gửi thông báo ngay khi hệ thống online để xác nhận kết nối Telegram"""
    if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M:%S - %d/%m/%Y")
    msg = (
        f"🚀 <b>[QUANT COPILOT V2 ĐÃ ONLINE]</b>\n"
        f"• <b>Thời gian:</b> {now_str}\n"
        f"• <b>Trạng thái:</b> Sẵn sàng quét thị trường & giám sát lệnh.\n"
        f"• <b>Chu kỳ quét:</b> 180s/lần | <b>Độ sâu:</b> 100 nến 15M/1H."
    )
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=8)
        if res.status_code == 200:
            logger.info("✅ Đã gửi thông báo khởi động lên Telegram thành công!")
        else:
            logger.error(f"Lỗi gửi Telegram: {res.text}")
    except Exception as e:
        logger.error(f"Ngoại lệ gửi tin nhắn Telegram: {e}")

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
            logger.info("🔍 [SCAN LOOP] Bắt đầu chu kỳ quét thị trường...")
            # 1. THE STRATEGIST: Cập nhật vĩ mô & tin tức
            macro = await asyncio.to_thread(strategist.update_btc_macro)
            if not macro.get("allow_trade", True):
                reason = macro.get("news_reason") or "BTC giật sốc"
                logger.warning(f"⏸️ [TẠM DỪNG QUÉT]: {reason}")
                await asyncio.sleep(180)
                continue

            btc_bias = macro.get("bias", "NEUTRAL")
            logger.info(f"📊 [BTC MACRO] Xu hướng BTC: {btc_bias} | Biến động 15M: {macro.get('btc_change_15m_pct')}%")

            # 2. Lọc danh sách coin đủ thanh khoản >= 1.5M USDT
            eligible, _ = await asyncio.to_thread(universe.get_eligible_symbols, 1500000.0)
            target_symbols = [x["symbol"] for x in eligible[:120]]
            logger.info(f"📋 Lọc được {len(target_symbols)} cặp coin đạt điều kiện thanh khoản.")

            # 3. THE SCOUT: Quét ứng viên
            candidates = []
            for sym in target_symbols:
                c = await asyncio.to_thread(scout.scan_candidate, sym, btc_bias)
                if c:
                    candidates.append(c)
                await asyncio.sleep(0.02)

            logger.info(f"🎯 Scout tìm ra {len(candidates)} ứng viên tiềm năng.")

            # 4. THE AUDITOR & RISK CONTROLLER: Thẩm định và phát tín hiệu
            for item in candidates[:3]:
                funding = await asyncio.to_thread(client.get_funding_rate, item["symbol"])
                can_open, risk_reason = risk_ctrl.can_open_position(item["bias"], funding)
                if not can_open:
                    logger.info(f"🛡️ [RISK VETO] {item['symbol']}: {risk_reason}")
                    continue

                is_valid, audit_reason, plan = await asyncio.to_thread(auditor.audit_trade, item)
                if is_valid:
                    logger.info(f"🚀 [DUYỆT LỆNH] Phát tín hiệu {plan['symbol']} ({plan['direction']}) lên Telegram!")
                    await asyncio.to_thread(auditor.broadcast_signal_with_button, plan)
                    await asyncio.sleep(1.0)
                else:
                    logger.info(f"🛡️ [AUDITOR VETO] {item['symbol']}: {audit_reason}")

        except Exception as e:
            logger.error(f"Lỗi pipeline: {e}")

        await asyncio.sleep(180)

async def main():
    logger.info("🚀 KHỞI ĐỘNG HỆ THỐNG QUANT MULTI-AGENT COPILOT V2")
    
    # Gửi tin nhắn test lên Telegram xác nhận kết nối
    send_startup_alert()

    # Khởi chạy đồng thời: Cổng HTTP cho Render + Vòng quét thị trường + Giám sát lệnh + Lắng nghe Telegram
    await asyncio.gather(
        start_health_server(),
        pipeline_scan_loop(),
        position_monitor_loop(),
        telegram_button_listener()
    )

if __name__ == "__main__":
    asyncio.run(main())
