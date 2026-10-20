import os
import sys
import logging
import asyncio
from agent_strategist import run_strategist_and_news_radar
from agent_scout import run_scout_and_auditor_loop
from agent_monitor import run_position_monitor_loop
from telegram_handler import run_telegram_bot_listener

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("MultiAgentEngineV2")

# =========================================================================
# ASYNC HTTP HEALTH CHECK SERVER CHO RENDER (WEB SERVICE MIỄN PHÍ)
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
    logger.info(f"🌐 Server HTTP Health Check đã mở cổng {port} cho Render.")
    async with server:
        await server.serve_forever()

# =========================================================================
# SUPERVISOR AUTO-RESTART PATTERN
# =========================================================================
async def supervisor_task(coro_func, task_name: str):
    """Tự động restart một Agent nếu xảy ra lỗi ngoại lệ mà không làm chết hệ thống"""
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
    logger.info(">>> KHỞI ĐỘNG HỆ SINH THÁI 5-AGENT QUANT COPILOT V2...")
    await asyncio.gather(
        start_health_server(),
        supervisor_task(run_strategist_and_news_radar, "Agent 1: Strategist & News Radar"),
        supervisor_task(run_scout_and_auditor_loop, "Agent 2, 3 & 5: Scout & Auditor & Risk"),
        supervisor_task(run_position_monitor_loop, "Agent 4: Position Monitor"),
        supervisor_task(run_telegram_bot_listener, "Telegram Handler (/status & Buttons)")
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Hệ thống đã dừng an toàn.")
