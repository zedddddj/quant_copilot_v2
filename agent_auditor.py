import logging
import datetime
import requests
from typing import Dict, Any, Tuple
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM, FIXED_RISK_USDT, DEFAULT_LEVERAGE, VN_TZ

logger = logging.getLogger("TheAuditor")

class RiskAuditorAgent:
    def __init__(self):
        self.fixed_risk = FIXED_RISK_USDT
        self.leverage = DEFAULT_LEVERAGE

    @staticmethod
    def calculate_atr(candles: list, period: int = 14) -> float:
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(-period, 0):
            h, l, prev_c = float(candles[i][1]), float(candles[i][2]), float(candles[i - 1][3])
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        return sum(trs) / len(trs) if trs else 0.0

    def audit_trade(self, candidate: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        sym = candidate["symbol"]
        bias = candidate["bias"]
        curr_p = float(candidate["current_price"])
        c15m = candidate["ohlcv_closed"]["15m"]

        if not c15m or curr_p <= 0:
            return False, "Dữ liệu nến không hợp lệ", {}

        last_c = c15m[-1]
        o_p, h_p, l_p, c_p = float(last_c[0]), float(last_c[1]), float(last_c[2]), float(last_c[3])
        body = abs(c_p - o_p)

        # 1. Chặn nến xả dốc (Anti-Falling Knife): râu dưới < 80% thân nến
        if bias == "LONG":
            lower_wick = min(o_p, c_p) - l_p
            if c_p < o_p and lower_wick < (0.80 * body):
                return False, f"CANDLE_DUMPING: Nến 15M đỏ đặc áp đảo (Râu dưới {lower_wick:.4f} < 80% Thân {body:.4f})", {}
        elif bias == "SHORT":
            upper_wick = h_p - max(o_p, c_p)
            if c_p > o_p and upper_wick < (0.80 * body):
                return False, f"CANDLE_PUMPING: Nến 15M xanh đặc đâm lên (Râu trên {upper_wick:.4f} < 80% Thân {body:.4f})", {}

        # 2. Khóa cứng khoảng cách Limit <= 0.8%
        atr = self.calculate_atr(c15m, 14)
        closes_15m = [float(c[3]) for c in c15m]
        ema12 = sum(closes_15m[-12:]) / 12.0
        ema20 = sum(closes_15m[-20:]) / 20.0

        if bias == "LONG":
            limit_entry = min(curr_p * 0.998, ema12)
            if limit_entry >= curr_p:
                limit_entry = curr_p * 0.9975

            dist_pct = abs(curr_p - limit_entry) / curr_p
            if dist_pct > 0.008:
                return False, f"LIMIT_TOO_FAR: Cách giá {dist_pct*100:.2f}% (> 0.8%)", {}

            recent_low = min(float(c[2]) for c in c15m[-5:])
            stop_loss = min(recent_low - (0.5 * atr), limit_entry * 0.985)
            risk_dist = limit_entry - stop_loss
            if risk_dist <= 0:
                return False, "Khoảng cách rủi ro Long không hợp lệ", {}

            tp1 = limit_entry + (1.5 * risk_dist)
            tp2 = limit_entry + (2.2 * risk_dist)

        else:  # SHORT
            limit_entry = max(curr_p * 1.002, ema12)
            if limit_entry <= curr_p:
                limit_entry = curr_p * 1.0025

            dist_pct = abs(limit_entry - curr_p) / curr_p
            if dist_pct > 0.008:
                return False, f"LIMIT_TOO_FAR: Cách giá {dist_pct*100:.2f}% (> 0.8%)", {}

            recent_high = max(float(c[1]) for c in c15m[-5:])
            stop_loss = max(recent_high + (0.5 * atr), limit_entry * 1.015)
            risk_dist = stop_loss - limit_entry
            if risk_dist <= 0:
                return False, "Khoảng cách rủi ro Short không hợp lệ", {}

            tp1 = limit_entry - (1.5 * risk_dist)
            tp2 = limit_entry - (2.2 * risk_dist)

        # 3. Tính Margin chuẩn rủi ro cố định $4.00, đòn bẩy 5x
        sl_pct_move = risk_dist / limit_entry
        margin = round(self.fixed_risk / (sl_pct_move * self.leverage), 2)

        return True, "Hợp lệ", {
            "symbol": sym,
            "direction": bias,
            "entry": limit_entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "margin": margin,
            "sl_pct": sl_pct_move * 100.0,
            "dist_pct": dist_pct * 100.0
        }
def broadcast_signal(self, plan: Dict[str, Any]):
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return

        sym = plan["symbol"]
        d = plan["direction"]
        icon = "🟢" if d == "LONG" else "🔴"
        now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M:%S")

        # Lấy nhãn chế độ quét và kịch bản thiết lập từ Scout
        engine_mode = plan.get("engine_mode", "ALPHA")
        mode_tag = "🌊 [SÓNG ĐỒNG PHA BETA]" if engine_mode == "BETA_TREND" else "⚡ [SÓNG NỔ ĐỘC LẬP ALPHA]"
        setup_desc = plan.get("signal_desc", "Theo dõi tín hiệu kỹ thuật")

        msg = (
            f"🛡️ <b>[TÍN HIỆU COPILOT V2] ({now_str})</b>\n"
            f"<b>{mode_tag}</b>\n"
            f"✅ <b>KÈO ĐẠT CHUẨN AN TOÀN! ({icon} {d})</b>\n"
            f"• <b>Mã:</b> {sym}\n"
            f"• <b>Thiết lập:</b> {setup_desc}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Lệnh:</b> LIMIT <code>{plan['entry']:.5f}</code> (Cách TT <b>{plan['dist_pct']:.2f}%</b>)\n"
            f"• <b>Stop Loss:</b> <code>{plan['stop_loss']:.5f}</code> (Đệm <b>{plan['sl_pct']:.2f}%</b>)\n"
            f"• <b>TP1 (1.5R):</b> <code>{plan['tp1']:.5f}</code> | 🚀 <b>TP2 (2.2R):</b> <code>{plan['tp2']:.5f}</code>\n"
            f"• <b>Margin:</b> <b>{plan['margin']:.2f} USDT</b> (Đòn bẩy 5x | Lỗ cố định $4.00)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <i>Vào lệnh xong trên sàn, bấm nút bên dưới để Copilot bám sát nến:</i>"
        )

        cb_data = f"ENTER_{sym}_{d}_{plan['entry']:.5f}_{plan['stop_loss']:.5f}_{plan['tp1']:.5f}_{plan['tp2']:.5f}"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": f"✅ ĐÃ VÀO LỆNH {sym}", "callback_data": cb_data}
                ]]
            }
        }
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8)
        except Exception as e:
            logger.error(f"Lỗi gửi tín hiệu: {e}")
