import json
import os
import time
import logging
import requests
import asyncio
from typing import Dict, Any, Optional
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

    def register_trade(self, symbol: str, direction: str, entry: float, sl: float, tp1: float, tp2: float, tier: str, auditor_msg_id: Optional[int] = None):
        trades = self.load_trades()
        init_oi = self.client.get_open_interest(symbol)
        trades[symbol] = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tier": tier,
            "auditor_msg_id": auditor_msg_id,
            "status": "OPEN",
            "entry_time": time.time(),
            "initial_oi": init_oi,
            "hit_tp1": False
        }
        self.save_trades(trades)
        tier_desc = "Sóng mạnh" if "TIER_1" in tier else "Scalp biên ngắn (Giới hạn 45p)"
        
        # CHUỖI 2: Reply trực tiếp vào tin nhắn thẩm định của Auditor
        self.send_alert(
            f"🎯 <b>[AGENT 4 - POSITION MONITOR]</b>: Đã tiếp nhận giám sát <b>{symbol} ({direction} - {tier_desc})</b>.\n"
            f"• Lệnh gốc: Entry <code>{entry:.5f}</code> | SL: <code>{sl:.5f}</code>\n"
            f"• Quét dữ liệu & tư vấn kịch bản mỗi 60 giây.",
            reply_to_id=auditor_msg_id
        )

    def send_alert(self, text: str, reply_to_id: Optional[int] = None):
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        if reply_to_id:
            payload["reply_to_message_id"] = reply_to_id
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
            tier = t.get("tier", "TIER_1_STRONG_TREND")
            parent_id = t.get("auditor_msg_id")

            # 1. Kịch bản Hit TP1: Chốt lời, dời SL về Entry
            if not t.get("hit_tp1", False):
                if (direction == "LONG" and curr_p >= tp1) or (direction == "SHORT" and curr_p <= tp1):
                    t["hit_tp1"] = True
                    t["stop_loss"] = entry
                    self.save_trades(trades)
                    tp_pct = "70%" if tier == "TIER_2_SCALP_QUICK" else "50%"
                    self.send_alert(
                        f"🎉 <b>[CẢNH BÁO HIT TP1 - {sym}]</b>\n"
                        f"Giá hiện tại <code>{curr_p:.5f}</code> đã chạm TP1 (<code>{tp1:.5f}</code>)!\n"
                        f"👉 <b>HÀNH ĐỘNG NGAY:</b> Khớp lệnh chốt {tp_pct} khối lượng và <b>DỜI SL VỀ GIÁ VỐN (<code>{entry:.5f}</code>)</b> để bảo toàn lợi nhuận!",
                        reply_to_id=parent_id
                    )
                    continue

            # 2. Tiệm cận TP2 xuất hiện cạn kiệt Open Interest (OI) > 5% hoặc râu đảo chiều
            dist_to_tp2 = abs(tp2 - curr_p) / curr_p
            if dist_to_tp2 <= 0.008 and t.get("hit_tp1", False):
                curr_oi = self.client.get_open_interest(sym)
                init_oi = t.get("initial_oi", curr_oi)
                oi_drop = (init_oi - curr_oi) / init_oi if init_oi > 0 else 0.0

                if oi_drop >= 0.05:
                    self.send_alert(
                        f"⚠️ <b>[CẢNH BÁO HỤT HƠI TRƯỚC TP2 - {sym}]</b>\n"
                        f"Giá cách TP2 <b>{dist_to_tp2*100:.2f}%</b> nhưng OI sụt giảm <b>{oi_drop*100:.1f}%</b> (dòng tiền rút ra sớm).\n"
                        f"👉 <b>KHUYẾN NGHỊ:</b> Đóng chủ động 100% phần còn lại tại giá thị trường <code>{curr_p:.5f}</code>!",
                        reply_to_id=parent_id
                    )

            # 3. Quy tắc 3 cây nến (45 phút) cho Tier 2 Range Scalping
            held_minutes = (now - t["entry_time"]) / 60.0
            if tier == "TIER_2_SCALP_QUICK" and held_minutes >= 45.0 and not t.get("hit_tp1", False):
                self.send_alert(
                    f"⚠️ <b>[QUY TẮC 3 CÂY NẾN TIER 2 - {sym}]</b>\n"
                    f"Vị thế Scalp biên đã chạy <b>{held_minutes:.0f} phút</b> nhưng giá chưa chạm TP1.\n"
                    f"👉 <b>KHUYẾN NGHỊ:</b> Đóng hòa vốn tại <code>{curr_p:.5f}</code> để tránh bẫy thủng biên.",
                    reply_to_id=parent_id
                )
                t["entry_time"] = now

            # 4. Kịch bản Gãy cản động EMA20: Cắt lỗ sớm (Early Cut)
            closes = [float(k.get("close", 0) if isinstance(k, dict) else k[2]) for k in k15m_raw[-20:]]
            ema20 = sum(closes) / len(closes)
            if (direction == "LONG" and curr_p < ema20 and curr_p < entry) or (direction == "SHORT" and curr_p > ema20 and curr_p > entry):
                self.send_alert(
                    f"⚠️ <b>[CẢNH BÁO GÃY CẢN ĐỘNG - {sym}]</b>\n"
                    f"Nến 15M đã xuyên thủng trục EMA20 (<code>{ema20:.5f}</code>), giá đang ở <code>{curr_p:.5f}</code>.\n"
                    f"👉 <b>KHUYẾN NGHỊ:</b> Đóng lệnh chủ động để cắt lỗ sớm, không đợi quét SL <code>{sl:.5f}</code>.",
                    reply_to_id=parent_id
                )

            # 5. Kịch bản Om vốn Tier 1 (> 4 tiếng)
            held_hours = (now - t["entry_time"]) / 3600.0
            if tier == "TIER_1_STRONG_TREND" and held_hours >= 4.0:
                dist_entry = abs(curr_p - entry) / entry
                if dist_entry < 0.005:
                    self.send_alert(
                        f"⏳ <b>[CẢNH BÁO OM VỐN - {sym}]</b>\n"
                        f"Vị thế đã giam vốn <b>{held_hours:.1f}h</b> nhưng giá chỉ quanh quẩn Entry ({dist_entry*100:.2f}%).\n"
                        f"👉 <b>KHUYẾN NGHỊ:</b> Đóng lệnh hòa vốn để giải phóng ký quỹ sang cơ hội khác.",
                        reply_to_id=parent_id
                    )
                    t["entry_time"] = now

            # 6. Kiểm tra chạm SL hoặc TP2
            if (direction == "LONG" and curr_p <= sl) or (direction == "SHORT" and curr_p >= sl):
                t["status"] = "CLOSED_SL"
                self.save_trades(trades)
                self.send_alert(f"🛑 <b>[VỊ THẾ ĐÃ CHẠM SL - {sym}]</b> Ghi nhận đóng lệnh tại <code>{curr_p:.5f}</code>.", reply_to_id=parent_id)
            elif (direction == "LONG" and curr_p >= tp2) or (direction == "SHORT" and curr_p <= tp2):
                t["status"] = "CLOSED_TP2"
                self.save_trades(trades)
                self.send_alert(f"🚀 <b>[HOÀN TẤT CHỐT LỜI TP2 - {sym}]</b> Lệnh đã đạt mục tiêu trọn vẹn tại <code>{curr_p:.5f}</code>.", reply_to_id=parent_id)

async def run_position_monitor_loop():
    client = MexcPublicClient()
    monitor = PositionMonitorAgent(client)
    logger.info("Agent 4 (Position Monitor) đã kích hoạt chu kỳ 60s.")
    while True:
        try:
            await asyncio.to_thread(monitor.check_active_positions)
        except Exception as e:
            logger.error(f"Lỗi monitor loop: {e}")
        await asyncio.sleep(60)
