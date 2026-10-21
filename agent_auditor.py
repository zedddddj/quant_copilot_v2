import logging
import datetime
import requests
from typing import Dict, Any, Tuple, Optional
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
        tier = candidate.get("tier", "TIER_1_STRONG_TREND")
        curr_p = float(candidate["current_price"])
        c15m = candidate.get("ohlcv_closed_15m", [])

        if not c15m or curr_p <= 0:
            return False, "Dữ liệu nến không hợp lệ", {}

        # =========================================================================
        # 1. VETO QUÁ MUA / QUÁ BÁN KHUNG LỚN 4H (CHỐNG ĐU ĐỈNH RÂU NẾN NHƯ LIT)
        # =========================================================================
        ind_4h = candidate.get("indicators_4h", {})
        rsi_4h = float(ind_4h.get("rsi6", 50.0))
        boll_ub_4h = float(ind_4h.get("boll_ub", 0.0))
        boll_lb_4h = float(ind_4h.get("boll_lb", 0.0))

        if bias == "LONG" and (rsi_4h >= 80.0 or curr_p > boll_ub_4h):
            return False, f"VETO_HTF_OVERBOUGHT: Khung 4H quá mua cực hạn (RSI6 {rsi_4h:.1f} >= 80 hoặc vượt BOLL UB)", {}
        if bias == "SHORT" and (rsi_4h <= 20.0 or curr_p < boll_lb_4h):
            return False, f"VETO_HTF_OVERSOLD: Khung 4H quá bán cực hạn (RSI6 {rsi_4h:.1f} <= 20 hoặc thủng BOLL LB)", {}

        # =========================================================================
        # 2. KHÓA CỨNG KHOẢNG CÁCH LIMIT & TỶ LỆ R:R THEO PHÂN CẤP TIER
        # =========================================================================
        atr = self.calculate_atr(c15m, 14)
        closes_15m = [float(c[3]) for c in c15m]
        ema12 = sum(closes_15m[-12:]) / 12.0

        if tier == "TIER_1_STRONG_TREND":
            limit_cap = 0.008  # Tối đa 0.8%
            tp1_r, tp2_r = 1.5, 2.2
        else:  # TIER_2_SCALP_QUICK
            limit_cap = 0.004  # Kê sát tối đa 0.4%
            tp1_r, tp2_r = 1.0, 1.4

        if bias == "LONG":
            limit_entry = min(curr_p * (1.0 - (limit_cap / 2.0)), ema12)
            if limit_entry >= curr_p:
                limit_entry = curr_p * (1.0 - (limit_cap / 3.0))

            dist_pct = abs(curr_p - limit_entry) / curr_p
            if dist_pct > limit_cap:
                return False, f"LIMIT_TOO_FAR: Cách giá thị trường {dist_pct*100:.2f}% (> {limit_cap*100:.1f}%)", {}

            recent_low = min(float(c[2]) for c in c15m[-5:])
            stop_loss = min(recent_low - (0.4 * atr), limit_entry * 0.985)
            risk_dist = limit_entry - stop_loss
            if risk_dist <= 0:
                return False, "Khoảng cách rủi ro Long không hợp lệ", {}

            tp1 = limit_entry + (tp1_r * risk_dist)
            tp2 = limit_entry + (tp2_r * risk_dist)

        else:  # SHORT
            limit_entry = max(curr_p * (1.0 + (limit_cap / 2.0)), ema12)
            if limit_entry <= curr_p:
                limit_entry = curr_p * (1.0 + (limit_cap / 3.0))

            dist_pct = abs(limit_entry - curr_p) / curr_p
            if dist_pct > limit_cap:
                return False, f"LIMIT_TOO_FAR: Cách giá thị trường {dist_pct*100:.2f}% (> {limit_cap*100:.1f}%)", {}

            recent_high = max(float(c[1]) for c in c15m[-5:])
            stop_loss = max(recent_high + (0.4 * atr), limit_entry * 1.015)
            risk_dist = stop_loss - limit_entry
            if risk_dist <= 0:
                return False, "Khoảng cách rủi ro Short không hợp lệ", {}

            tp1 = limit_entry - (tp1_r * risk_dist)
            tp2 = limit_entry - (tp2_r * risk_dist)

        # Cố định rủi ro đúng $4.00, đòn bẩy 5x
        sl_pct_move = risk_dist / limit_entry
        margin = round(self.fixed_risk / (sl_pct_move * self.leverage), 2)

        return True, "Hợp lệ", {
            "symbol": sym,
            "direction": bias,
            "tier": tier,
            "entry": limit_entry,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_r": tp1_r,
            "tp2_r": tp2_r,
            "margin": margin,
            "sl_pct": sl_pct_move * 100.0,
            "dist_pct": dist_pct * 100.0,
            "signal_desc": candidate.get("signal_desc", ""),
            "scout_msg_id": candidate.get("scout_msg_id")
        }

    def broadcast_signal(self, plan: Dict[str, Any]) -> Optional[int]:
        """Gửi tín hiệu duyệt lệnh, REPLY trực tiếp vào tin nhắn phát hiện của Scout (Chuỗi 2)"""
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return None

        sym = plan["symbol"]
        d = plan["direction"]
        icon = "🟢" if d == "LONG" else "🔴"
        tier = plan["tier"]
        tier_tag = "🚀 [TIER 1 - SÓNG MẠNH]" if tier == "TIER_1_STRONG_TREND" else "⚡ [TIER 2 - SCALP BIÊN NGẮN]"
        now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M:%S")

        msg = (
            f"🛡️ <b>[AGENT 3 - THẨM ĐỊNH AUDITOR] ({now_str})</b>\n"
            f"<b>{tier_tag}</b>\n"
            f"✅ <b>KÈO ĐẠT CHUẨN AN TOÀN! ({icon} {d})</b>\n"
            f"• <b>Mã:</b> {sym} | <b>Thiết lập:</b> {plan['signal_desc']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Lệnh:</b> LIMIT <code>{plan['entry']:.5f}</code> (Cách TT: <b>{plan['dist_pct']:.2f}%</b>)\n"
            f"• <b>Stop Loss:</b> <code>{plan['stop_loss']:.5f}</code> (Đệm: <b>{plan['sl_pct']:.2f}%</b>)\n"
            f"• <b>TP1 ({plan['tp1_r']}R):</b> <code>{plan['tp1']:.5f}</code> | 🎯 <b>TP2 ({plan['tp2_r']}R):</b> <code>{plan['tp2']:.5f}</code>\n"
            f"• <b>Margin:</b> <b>{plan['margin']:.2f} USDT</b> (Đòn bẩy 5x | Rủi ro đúng $4.00)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👉 <i>Sau khi vào lệnh trên sàn, bấm nút dưới để Agent 4 bám sát nến:</i>"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        }
        # Reply vào tin nhắn gốc của Scout nếu có
        if plan.get("scout_msg_id"):
            payload["reply_to_message_id"] = plan["scout_msg_id"]

        try:
            # Gửi tin nhắn trước để lấy message_id của Auditor
            res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8)
            if res.status_code == 200:
                auditor_msg_id = res.json().get("result", {}).get("message_id")
                # Gắn nút bấm kèm auditor_msg_id vào callback để Agent 4 reply tiếp vào đây
                cb_data = f"ENTER_{sym}_{d}_{plan['entry']:.5f}_{plan['stop_loss']:.5f}_{plan['tp1']:.5f}_{plan['tp2']:.5f}_{tier}_{auditor_msg_id}"
                edit_payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "message_id": auditor_msg_id,
                    "reply_markup": {
                        "inline_keyboard": [[
                            {"text": f"✅ ĐÃ VÀO LỆNH {sym}", "callback_data": cb_data}
                        ]]
                    }
                }
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup", json=edit_payload, timeout=6)
                return auditor_msg_id
        except Exception as e:
            logger.error(f"Lỗi gửi tín hiệu: {e}")
        return None
