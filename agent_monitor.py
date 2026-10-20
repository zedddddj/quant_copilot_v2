import json
import os
import time
import logging
import datetime
import requests
from typing import Dict, Any
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM, STATE_TRADES_FILE, VN_TZ
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
        self.send_alert(f"🎯 <b>[COPILOT ĐÃ KÍCH HOẠT GIÁM SÁT]</b>\nĐã tiếp nhận theo dõi vị thế <b>{symbol} ({direction})</b>. Tần suất cập nhật mỗi 60 giây.")

    def send_alert(self, text: str):
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
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

            last_candle = k15m_raw[-1]
            curr_p = float(last_candle.get("close", 0.0) if isinstance(last_candle, dict) else last_candle[2])
            direction = t["direction"]
            entry = t["entry"]
            sl = t["stop_loss"]
            tp1 = t["tp1"]
            tp2 = t["tp2"]

            if not t.get("hit_tp1", False):
                if (direction == "LONG" and curr_p >= tp1) or (direction == "SHORT" and curr_p <= tp1):
                    t["hit_tp1"] = True
                    t["stop_loss"] = entry
                    self.save_trades(trades)
                    self.send_alert(
                        f"🎉 <b>[CẢNH BÁO HIT TP1 - {sym}]</b>\n"
                        f"Giá hiện tại <code>{curr_p:.5f}</code> đã chạm mốc TP1 (<code>{tp1:.5f}</code>)!\n"
                        f"👉 <b>HÀNH ĐỘNG NGAY:</b> Chốt lời 50% vị thế và <b>DỜI STOP LOSS VỀ GIÁ VỐN (<code>{entry:.5f}</code>)</b> để gồng lãi 50% còn lại an toàn!"
                    )
                    continue

            closes = [float(k.get("close", 0) if isinstance(k, dict) else k[2]) for k in k15m_raw[-20:]]
            ema20 = sum(closes) / len(closes)
            if direction == "LONG" and curr_p < ema20 and curr_p < entry:
                self.send_alert(
                    f"⚠️ <b>[CẢNH BÁO GÃY CẢN ĐỘNG - {sym}]</b>\n"
                    f"Nến 15M đã thủng trục EMA20 (<code>{ema20:.5f}</code>), giá đang rơi về <code>{curr_p:.5f}</code>.\n"
                    f"👉 <b>KHUYẾN NGHỊ:</b> Đóng lệnh chủ động để cắt lỗ sớm, không nên đợi quét Stop Loss <code>{sl:.5f}</code>."
                )

            held_hours = (now - t["entry_time"]) / 3600.0
            if held_hours >= 4.0:
                dist_entry = abs(curr_p - entry) / entry
                if dist_entry < 0.005:
                    self.send_alert(
                        f"⏳ <b>[CẢNH BÁO OM VỐN - {sym}]</b>\n"
                        f"Vị thế đã giam vốn hơn <b>{held_hours:.1f} tiếng</b> nhưng giá chỉ quanh quẩn mốc Entry ({dist_entry*100:.2f}%).\n"
                        f"👉 <b>KHUYẾN NGHỊ:</b> Đóng lệnh hòa vốn để giải phóng ký quỹ sang các cơ hội có dòng tiền tốt hơn."
                    )
                    t["entry_time"] = now

            if (direction == "LONG" and curr_p <= sl) or (direction == "SHORT" and curr_p >= sl):
                t["status"] = "CLOSED_SL"
                self.save_trades(trades)
                self.send_alert(f"🛑 <b>[VỊ THẾ ĐÃ CHẠM SL - {sym}]</b> Đã ghi nhận đóng vị thế tại <code>{curr_p:.5f}</code>.")
            elif (direction == "LONG" and curr_p >= tp2) or (direction == "SHORT" and curr_p <= tp2):
                t["status"] = "CLOSED_TP2"
                self.save_trades(trades)
                self.send_alert(f"🚀 <b>[HOÀN TẤT CHỐT LỜI TP2 - {sym}]</b> Chúc mừng! Lệnh đã đạt trọn vẹn mục tiêu tại <code>{curr_p:.5f}</code>.")
