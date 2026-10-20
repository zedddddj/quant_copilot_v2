import json
import os
import time
import logging
import requests
import asyncio
from typing import Dict, Any
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM, STATE_TRADES_FILE
from data.mexc_client import MexcPublicClient

logger = logging.getLogger("ThePositionMonitor")

class PositionMonitorAgent:
    def __init__(self, client: MexcPublicClient):
        self.client = client
        self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(STATE_TRADES_FILE):
            with open(STATE_TRADES_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def load_trades(self) -> Dict[str, Any]:
        try:
            with open(STATE_TRADES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_trades(self, trades: Dict[str, Any]):
        try:
            with open(STATE_TRADES_FILE, "w", encoding="utf-8") as f:
                json.dump(trades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Lỗi lưu trades: {e}")

    def register_trade(self, symbol: str, direction: str, entry: float, sl: float, tp1: float, tp2: float):
        trades = self.load_trades()
        trades[symbol] = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "status": "OPEN",
            "entry_time": time.time(),
            "hit_tp1": False
        }
        self.save_trades(trades)
        self.send_alert(f"🎯 <b>[COPILOT ĐÃ KÍCH HOẠT THEO DÕI]</b>\nTiếp nhận giám sát <b>{symbol} ({direction})</b>. Quét nhịp nến mỗi 30–60 giây.")

    def send_alert(self, text: str):
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8)
        except Exception as e:
            logger.error(f"Lỗi gửi alert monitor: {e}")

    def check_active_positions(self):
        trades = self.load_trades()
        if not trades:
            return

        now = time.time()
        for sym, t in list(trades.items()):
            if t.get("status") != "OPEN":
                continue

            k15m_raw = self.client.get_kline(sym, interval="Min15", limit=30)
            if not k15m_raw:
                continue

            last_c = k15m_raw[-1]
            curr_p = float(last_c.get("close", 0.0) if isinstance(last_c, dict) else last_c[2])
            direction = t["direction"]
            entry = float(t["entry"])
            sl = float(t["stop_loss"])
            tp1 = float(t["tp1"])
            tp2 = float(t["tp2"])

            # 1. Kịch bản Hit TP1 (1.5R): Chốt 50%, dời SL về Entry
            if not t.get("hit_tp1", False):
                if (direction == "LONG" and curr_p >= tp1) or (direction == "SHORT" and curr_p <= tp1):
                    t["hit_tp1"] = True
                    t["stop_loss"] = entry
                    self.save_trades(trades)
                    self.send_alert(
                        f"🎉 <b>[CẢNH BÁO HIT TP1 - {sym}]</b>\n"
                        f"Giá hiện tại <code>{curr_p:.5f}</code> đã chạm TP1 (<code>{tp1:.5f}</code>)!\n"
                        f"👉 <b>HÀNH ĐỘNG NGAY:</b> Chốt 50% khối lượng và <b>DỜI SL VỀ GIÁ VỐN (<code>{entry:.5f}</code>)</b> để gồng lãi 50% còn lại an toàn!"
                    )
                    continue

            # 2. Kịch bản Gãy cản động EMA20: Cắt lỗ sớm (Early Cut)
            closes = [float(k.get("close", 0) if isinstance(k, dict) else k[2]) for k in k15m_raw[-20:]]
            ema20 = sum(closes) / len(closes)
            if (direction == "LONG" and curr_p < ema20 and curr_p < entry) or (direction == "SHORT" and curr_p > ema20 and curr_p > entry):
                self.send_alert(
                    f"⚠️ <b>[CẢNH BÁO GÃY CẢN ĐỘNG - {sym}]</b>\n"
                    f"Nến 15M đã gãy trục EMA20 (<code>{ema20:.5f}</code>), giá đang ở <code>{curr_p:.5f}</code>.\n"
                    f"👉 <b>KHUYẾN NGHỊ:</b> Đóng lệnh chủ động để cắt lỗ sớm, không nên đợi quét Stop Loss <code>{sl:.5f}</code>."
                )

            # 3. Kịch bản Om vốn > 4 tiếng (Stagnant Capital)
            held_hours = (now - t["entry_time"]) / 3600.0
            if held_hours >= 4.0:
                dist_entry = abs(curr_p - entry) / entry
                if dist_entry < 0.005:
                    self.send_alert(
                        f"⏳ <b>[CẢNH BÁO OM VỐN - {sym}]</b>\n"
                        f"Vị thế đã giam vốn <b>{held_hours:.1f}h</b> nhưng giá chỉ biến động quanh Entry ({dist_entry*100:.2f}%).\n"
                        f"👉 <b>KHUYẾN NGHỊ:</b> Đóng lệnh hòa vốn để giải phóng ký quỹ sang cơ hội khác."
                    )
                    t["entry_time"] = now

            # 4. Kiểm tra chạm SL hoặc TP2
            if (direction == "LONG" and curr_p <= sl) or (direction == "SHORT" and curr_p >= sl):
                t["status"] = "CLOSED_SL"
                self.save_trades(trades)
                self.send_alert(f"🛑 <b>[VỊ THẾ ĐÃ CHẠM SL - {sym}]</b> Đã ghi nhận đóng lệnh tại <code>{curr_p:.5f}</code>.")
            elif (direction == "LONG" and curr_p >= tp2) or (direction == "SHORT" and curr_p <= tp2):
                t["status"] = "CLOSED_TP2"
                self.save_trades(trades)
                self.send_alert(f"🚀 <b>[HOÀN TẤT CHỐT LỜI TP2 - {sym}]</b> Lệnh đã đạt trọn vẹn mục tiêu tại <code>{curr_p:.5f}</code>.")

async def run_position_monitor_loop():
    client = MexcPublicClient()
    monitor = PositionMonitorAgent(client)
    logger.info("Agent 4 (Position Monitor) đã kích hoạt chu kỳ 30s-60s.")
    while True:
        try:
            await asyncio.to_thread(monitor.check_active_positions)
        except Exception as e:
            logger.error(f"Lỗi monitor loop: {e}")
        await asyncio.sleep(45)
