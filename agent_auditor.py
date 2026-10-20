import logging
import datetime
import requests
from typing import Dict, Any, Tuple, Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM, FIXED_RISK_USDT, MAX_MARGIN_CAP, DEFAULT_LEVERAGE, VN_TZ

logger = logging.getLogger("TheAuditor")

class RiskAuditorAgent:
    def __init__(self):
        self.fixed_risk = FIXED_RISK_USDT
        self.max_margin = MAX_MARGIN_CAP
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

    @staticmethod
    def calculate_rsi(candles: list, period: int = 14) -> float:
        if len(candles) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(-period, 0):
            diff = float(candles[i][3]) - float(candles[i - 1][3])
            if diff >= 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

    def audit_trade(self, data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        bias = data.get("bias")
        curr_p = float(data.get("current_price", 0.0))
        c15m = data.get("ohlcv_closed", {}).get("15m", [])
        if not c15m or curr_p <= 0:
            return False, "Dữ liệu nến không hợp lệ", {}

        rsi6_15m = float(self.calculate_rsi(c15m, 6))
        rsi14_15m = float(self.calculate_rsi(c15m, 14))

        if bias == "LONG" and (rsi6_15m >= 78.0 or rsi14_15m >= 75.0):
            return False, f"OVERBOUGHT_VETO: RSI ({rsi6_15m:.1f}) quá mua nặng, cấm đu đỉnh", {}
        if bias == "SHORT" and (rsi6_15m <= 22.0 or rsi14_15m <= 25.0):
            return False, f"OVERSOLD_VETO: RSI ({rsi6_15m:.1f}) quá bán nặng, cấm Short đáy", {}

        last_c = c15m[-1]
        o_p, h_p, l_p, c_p = float(last_c[0]), float(last_c[1]), float(last_c[2]), float(last_c[3])
        body = abs(c_p - o_p)

        if bias == "LONG":
            lower_wick = min(o_p, c_p) - l_p
            if c_p < o_p and lower_wick < body:
                return False, f"CANDLE_DUMPING: Nến 15M đỏ đặc áp đảo (Râu dưới {lower_wick:.4f} < Thân {body:.4f}), cấm bắt dao", {}

        elif bias == "SHORT":
            upper_wick = h_p - max(o_p, c_p)
            if c_p > o_p and upper_wick < body:
                return False, f"CANDLE_PUMPING: Nến 15M xanh đặc đâm lên (Râu trên {upper_wick:.4f} < Thân {body:.4f}), cấm đón đầu tàu", {}

        atr = self.calculate_atr(c15m, 14)
        ind = data.get("indicators", {})
        ema20 = float(ind.get("ema20", curr_p))

        if bias == "LONG":
            limit_entry = min(curr_p * 0.998, ema20)
            if limit_entry >= curr_p:
                limit_entry = curr_p * 0.9975

            dist_pct = abs(curr_p - limit_entry) / curr_p
            if dist_pct > 0.008:
                return False, f"LIMIT_TOO_FAR: Điểm đón Limit cách giá {dist_pct*100:.2f}% (> 0.8%), tránh bẫy gãy nền", {}

            recent_low = min(float(c[2]) for c in c15m[-5:])
            stop_loss = min(recent_low - (0.5 * atr), limit_entry * 0.985)
            risk_dist = limit_entry - stop_loss
            if risk_dist <= 0:
                return False, "Khoảng cách rủi ro Long không hợp lệ", {}

            tp1 = limit_entry + (1.2 * risk_dist)
            tp2 = limit_entry + (1.8 * risk_dist)

        else:
            limit_entry = max(curr_p * 1.002, ema20)
            if limit_entry <= curr_p:
                limit_entry = curr_p * 1.0025

            dist_pct = abs(limit_entry - curr_p) / curr_p
            if dist_pct > 0.008:
                return False, f"LIMIT_TOO_FAR: Điểm đón Limit cách giá {dist_pct*100:.2f}% (> 0.8%), tránh bẫy bứt phá giả", {}

            recent_high = max(float(c[1]) for c in c15m[-5:])
            stop_loss = max(recent_high + (0.5 * atr), limit_entry * 1.015)
            risk_dist = stop_loss - limit_entry
            if risk_dist <= 0:
                return False, "Khoảng cách rủi ro Short không hợp lệ", {}

            tp1 = limit_entry - (1.2 * risk_dist)
            tp2 = limit_entry - (1.8 * risk_dist)

        risk_pct_move = risk_dist / limit_entry
        total_margin = round(min(self.max_margin, self.fixed_risk / (risk_pct_move * self.leverage)), 2)

        return True, "Hợp lệ", {
            "symbol": data["symbol"],
            "direction": bias,
            "entry": limit_entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "margin": total_margin,
            "sl_pct": (risk_dist / limit_entry) * 100.0,
            "retest_dist_pct": dist_pct * 100.0
        }

    def broadcast_signal_with_button(self, plan: Dict[str, Any], scout_msg_id: Optional[int] = None):
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return

        sym = plan["symbol"]
        d = plan["direction"]
        icon = "🟢" if d == "LONG" else "🔴"
        now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M:%S")

        msg = (
            f"🛡️ <b>[BOT 2 - THẨM ĐỊNH COPILOT] ({now_str})</b>\n"
            f"✅ <b>KÈO ĐẠT CHUẨN AN TOÀN! ({icon} {d})</b>\n"
            f"• <b>Mã:</b> {sym}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Lệnh:</b> LIMIT <code>{plan['entry']:.5f}</code> (Cách TT <b>{plan['retest_dist_pct']:.2f}%</b>)\n"
            f"• <b>Stop Loss:</b> <code>{plan['stop_loss']:.5f}</code> (Đệm <b>{plan['sl_pct']:.2f}%</b>)\n"
            f"• <b>TP1 (R:R 1.2):</b> <code>{plan['tp1']:.5f}</code> | 🚀 <b>TP2:</b> <code>{plan['tp2']:.5f}</code>\n"
            f"• <b>Margin khuyên dùng:</b> <b>{plan['margin']:.2f} USDT</b> (Đòn bẩy 5x | Lỗ cố định $4.00)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <i>Sau khi đặt lệnh xong trên sàn, bấm nút bên dưới để Trợ lý bắt đầu giám sát:</i>"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": f"✅ ĐÃ VÀO LỆNH {sym}", "callback_data": f"ENTER_{sym}_{d}_{plan['entry']:.5f}_{plan['stop_loss']:.5f}_{plan['tp1']:.5f}_{plan['tp2']:.5f}"}
                ]]
            }
        }
        try:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8)
        except Exception as e:
            logger.error(f"Lỗi gửi tin nhắn kèm nút Telegram: {e}")
