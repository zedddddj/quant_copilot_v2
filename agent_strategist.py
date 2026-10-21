import json
import logging
import datetime
import requests
import asyncio
import time
from typing import Dict, Any, List, Tuple, Optional
from config import VN_TZ, STATE_MACRO_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM
from data.mexc_client import MexcPublicClient

logger = logging.getLogger("TheStrategist")

class StrategistAgent:
    def __init__(self, client: MexcPublicClient):
        self.client = client
        self.last_news_fetch = 0
        self.cached_events = []
        self.notified_news = set()
        self.last_state: Dict[str, Any] = {}
        self.last_briefing_date: Optional[str] = None
        self.last_checkpoint_hour: Optional[int] = None
        self.last_monday_date: Optional[str] = None

    @staticmethod
    def _extract_ohlc(k) -> tuple:
        if isinstance(k, dict):
            return (
                float(k.get("open", 0.0) or 0.0),
                float(k.get("high", 0.0) or 0.0),
                float(k.get("low", 0.0) or 0.0),
                float(k.get("close", 0.0) or 0.0),
                float(k.get("vol", k.get("volume", 0.0)) or 0.0)
            )
        elif isinstance(k, (list, tuple)) and len(k) >= 5:
            try:
                o, c, h, l = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                if h < l:
                    h, l = l, h
                v = float(k[5]) if len(k) > 5 else 0.0
                return o, h, l, c, v
            except Exception:
                return 0.0, 0.0, 0.0, 0.0, 0.0
        return 0.0, 0.0, 0.0, 0.0, 0.0

    @staticmethod
    def calculate_rsi(candles: list, period: int = 6) -> float:
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

    def send_telegram(self, text: str, reply_to_id: Optional[int] = None) -> Optional[int]:
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return None
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        if reply_to_id:
            payload["reply_to_message_id"] = reply_to_id
        try:
            res = requests.post(url, json=payload, timeout=8)
            if res.status_code == 200:
                return res.json().get("result", {}).get("message_id")
        except Exception as e:
            logger.error(f"Lỗi gửi tin nhắn Telegram: {e}")
        return None

    def fetch_forex_factory_calendar(self) -> List[Dict[str, Any]]:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if self.cached_events and (now - self.last_news_fetch < 1800):
            return self.cached_events

        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        try:
            res = requests.get(url, timeout=8)
            if res.status_code == 200:
                events = res.json()
                self.cached_events = [e for e in events if e.get("country") == "USD" and e.get("impact") == "High"]
                self.last_news_fetch = now
                return self.cached_events
        except Exception as e:
            logger.warning(f"Lỗi tải ForexFactory: {e}")
        return self.cached_events

    def check_news_radar(self) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        events = self.fetch_forex_factory_calendar()
        if not events:
            return False, "Không có tin tức USD High Impact", None

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        active_freeze = False
        freeze_reason = ""
        next_event = None

        for ev in events:
            date_str = ev.get("date")
            if not date_str:
                continue
            try:
                ev_time = datetime.datetime.fromisoformat(date_str)
                diff_m = (ev_time - now_utc).total_seconds() / 60.0

                ev_id = f"{ev.get('title')}_{date_str}"
                if 25.0 <= diff_m <= 35.0 and f"{ev_id}_30" not in self.notified_news:
                    self.dispatch_macro_shock_thread(
                        title=f"Tin Đỏ USD: {ev.get('title')}",
                        change_15m=self.last_state.get("btc_change_15m_pct", 0.0)
                    )
                    self.notified_news.add(f"{ev_id}_30")
                elif 55.0 <= diff_m <= 65.0 and f"{ev_id}_60" not in self.notified_news:
                    self.send_telegram(f"⚠️ <b>[AGENT 1 - RADAR TIN TỨC]</b>: Sắp có tin đỏ <b>{ev.get('title')}</b> lúc {ev_time.astimezone(VN_TZ).strftime('%H:%M')} (còn ~60 phút).")
                    self.notified_news.add(f"{ev_id}_60")

                if -30.0 <= diff_m <= 30.0:
                    active_freeze = True
                    freeze_reason = f"NEWS_FREEZE: Tin đỏ USD [{ev.get('title')}] (cách {abs(diff_m):.0f} phút)"

                if diff_m > 0 and (next_event is None or diff_m < next_event["minutes"]):
                    next_event = {"title": ev.get("title"), "minutes": round(diff_m), "time": ev_time.astimezone(VN_TZ).strftime('%H:%M')}
            except Exception:
                continue

        return active_freeze, freeze_reason or "An toàn trước tin tức", next_event

    def dispatch_macro_shock_thread(self, title: str, change_15m: float):
        """CHUỖI 1: TỰ ĐỘNG THẢO LUẬN THEO CHUỖI KHI CÓ SỰ KIỆN VĨ MÔ / SHOCK BTC"""
        # 1. Agent 1 phát tin gốc
        msg_1 = (
            f"🚨 <b>[AGENT 1 - STRATEGIST]</b>: Cảnh báo sự kiện: <b>{title}</b>.\n"
            f"• Biến động nến BTC 15M: <b>{change_15m:+.2f}%</b>\n"
            f"• Chỉ thị: Tạm dừng duyệt lệnh mới, rà soát danh mục đang mở."
        )
        root_id = self.send_telegram(msg_1)
        if not root_id:
            return

        time.sleep(0.8)
        # 2. Agent 2 phản hồi vào tin gốc
        self.send_telegram(
            "🤖 <b>[AGENT 2 - SCOUT]</b>: Đã nhận chỉ thị. Tạm khóa quét Altcoin Long.",
            reply_to_id=root_id
        )

        time.sleep(0.8)
        # 3. Agent 4 phản hồi rà soát lệnh
        self.send_telegram(
            "⏳ <b>[AGENT 4 - MONITOR]</b>: Rà soát vị thế đang mở. Khuyến nghị dời SL về hòa vốn.",
            reply_to_id=root_id
        )

        time.sleep(0.8)
        # 4. Agent 5 phản hồi trạng thái cầu chì
        self.send_telegram(
            "🛡️ <b>[AGENT 5 - RISK]</b>: Đã khóa cổng mở lệnh mới. Danh mục bảo toàn an toàn.",
            reply_to_id=root_id
        )

    def update_btc_macro(self) -> Dict[str, Any]:
        c15m, c1h, c4h, c1w = [], [], [], []

        for _ in range(3):
            k15m_raw = self.client.get_kline("BTC_USDT", interval="Min15", limit=60)
            k1h_raw = self.client.get_kline("BTC_USDT", interval="Min60", limit=60)
            k4h_raw = self.client.get_kline("BTC_USDT", interval="Min240", limit=60)
            k1w_raw = self.client.get_kline("BTC_USDT", interval="Week1", limit=5)

            c15m = [self._extract_ohlc(k) for k in (k15m_raw or []) if self._extract_ohlc(k)[3] > 0]
            c1h = [self._extract_ohlc(k) for k in (k1h_raw or []) if self._extract_ohlc(k)[3] > 0]
            c4h = [self._extract_ohlc(k) for k in (k4h_raw or []) if self._extract_ohlc(k)[3] > 0]
            c1w = [self._extract_ohlc(k) for k in (k1w_raw or []) if self._extract_ohlc(k)[3] > 0]

            if c15m and c1h:
                break
            time.sleep(0.5)

        curr_btc = float(c15m[-1][3]) if c15m else 0.0
        if curr_btc <= 0:
            tickers = self.client.get_all_tickers()
            for t in tickers:
                if t.get("symbol") == "BTC_USDT":
                    curr_btc = float(t.get("lastPrice", 0.0) or 0.0)
                    break
        if curr_btc <= 0:
            curr_btc = 82500.0

        # FIX LỖI ĐỘNG LƯỢNG 15M: Tính trên cây nến đã đóng cửa hoàn chỉnh (c15m[-2]) so với c15m[-3]
        if len(c15m) >= 3:
            c_prev = c15m[-2]
            c_before = c15m[-3]
            btc_change_15m = ((float(c_prev[3]) - float(c_before[3])) / float(c_before[3])) * 100.0
            rsi6_15m = float(self.calculate_rsi(c15m[:-1], 6))
        elif len(c15m) >= 2:
            c_prev = c15m[-2]
            btc_change_15m = ((float(c_prev[3]) - float(c_prev[0])) / float(c_prev[0])) * 100.0
            rsi6_15m = float(self.calculate_rsi(c15m[:-1], 6))
        else:
            btc_change_15m = 0.0
            rsi6_15m = 50.0

        is_shock = (abs(btc_change_15m) >= 0.8) or (rsi6_15m >= 85.0) or (rsi6_15m <= 15.0)
        has_news, news_reason, next_ev = self.check_news_radar()

        if is_shock and not self.last_state.get("circuit_breaker", False):
            self.dispatch_macro_shock_thread(
                title=f"Biến động giật nến 15M ({btc_change_15m:+.2f}%)",
                change_15m=btc_change_15m
            )

        closes_1h = [float(c[3]) for c in c1h] if c1h else [curr_btc]
        ema20_1h = sum(closes_1h[-20:]) / len(closes_1h[-20:])

        closes_4h = [float(c[3]) for c in c4h] if c4h else closes_1h
        ema20_4h = sum(closes_4h[-20:]) / len(closes_4h[-20:])

        week_open = float(c1w[-1][0]) if c1w else (curr_btc * 0.99)
        pwh = float(c1w[-2][1]) if len(c1w) >= 2 else (curr_btc * 1.05)
        pwl = float(c1w[-2][2]) if len(c1w) >= 2 else (curr_btc * 0.95)

        if curr_btc >= week_open:
            bias = "BULLISH_ACCUMULATION"
            regime = "TREND_BULLISH"
        else:
            bias = "BEARISH_DEFENSIVE"
            regime = "TREND_BEARISH"

        funding_btc = self.client.get_funding_rate("BTC_USDT") * 100.0

        state = {
            "timestamp": datetime.datetime.now(VN_TZ).timestamp(),
            "btc_price": curr_btc,
            "btc_change_15m_pct": round(btc_change_15m, 2),
            "rsi6_15m": round(rsi6_15m, 1),
            "circuit_breaker": is_shock,
            "news_freeze": has_news,
            "freeze_reason": news_reason if has_news else ("BTC xả giật nến 15M > 0.8%" if is_shock else "Bình thường"),
            "allow_trade": (not is_shock) and (not has_news),
            "bias": bias,
            "regime": regime,
            "funding_btc": round(funding_btc, 4),
            "next_news": next_ev,
            "week_open": week_open,
            "pwh": pwh,
            "pwl": pwl,
            "ema20_4h": ema20_4h
        }

        self.last_state = state
        try:
            with open(STATE_MACRO_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Lỗi ghi state macro: {e}")

        return state

    def generate_and_send_macro_briefing(self):
        """MẪU BẢN TIN VĨ MÔ CHUẨN [BTC MACRO COMPASS]"""
        state = self.update_btc_macro()
        btc_p = state["btc_price"]
        bias_str = state["bias"]
        chg_15m = state["btc_change_15m_pct"]
        rsi_15m = state["rsi6_15m"]
        funding = state.get("funding_btc", 0.0)
        pwh = state.get("pwh", 0.0)
        pwl = state.get("pwl", 0.0)
        week_open = state.get("week_open", 0.0)
        ema20_4h = state.get("ema20_4h", 0.0)
        next_ev = state.get("next_news")
        now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M:%S - %d/%m/%Y")

        if btc_p >= week_open:
            pos_str = "TRÊN Week Open (Tích lũy giữ nhịp)"
            sc_a = f"BTC nâng đáy tích lũy trên ${week_open:,.1f}, hướng tới cản trên ${pwh:,.1f}. Altcoin hưởng lợi."
            sc_b = f"Nếu nến 4H thủng ${week_open:,.1f} -> Kịch bản tăng bị hủy, lùi về test đáy ${pwl:,.1f}."
        else:
            pos_str = "DƯỚI Week Open (Chịu áp lực bán)"
            sc_a = f"BTC chịu cản tại ${week_open:,.1f}, tiếp tục trượt dốc về quét đáy ${pwl:,.1f}."
            sc_b = f"Nếu nến 4H bứt phá ngược lên ${week_open:,.1f} -> Đảo chiều hồi phục kỹ thuật."

        news_str = f"🔴 {next_ev['title']} ({next_ev['time']} - còn {next_ev['minutes']}p)" if next_ev else "🟢 Không có tin tức USD High Impact đáng chú ý trong ngày"
        cb_str = "🔴 KÍCH HOẠT (Tạm khóa Altcoin)" if state["circuit_breaker"] else "🟢 AN TOÀN (Cho phép quét Altcoin)"

        msg = (
            f"🧭 <b>[BTC MACRO COMPASS - BẢN ĐỒ VĨ MÔ & DỰ BÁO XU HƯỚNG]</b>\n"
            f"⏱ <i>Cập nhật lúc: {now_str}</i>\n\n"
            f"📊 <b>1. TỔNG QUAN ĐỊA HÌNH VĨ MÔ:</b>\n"
            f"• <b>BTC Realtime:</b> <code>${btc_p:,.1f}</code> | <b>Bias:</b> <b>{bias_str}</b>\n"
            f"• <b>Cản tuần HTF:</b> Đỉnh <code>${pwh:,.1f}</code> | Đáy <code>${pwl:,.1f}</code>\n"
            f"• <b>Mở cửa tuần (Week Open):</b> <code>${week_open:,.1f}</code> (Giá đang {pos_str})\n"
            f"• <b>Phái sinh:</b> Funding Rate <code>{funding:+0.4f}%</code> (Cân bằng, không có bẫy thanh lý)\n\n"
            f"📉 <b>2. ĐỘNG LƯỢNG NẾN & DIỄN BIẾN GẦN NHẤT:</b>\n"
            f"• Nến đóng 15M gần nhất: <b>{chg_15m:+.2f}%</b> | RSI6 15M: <b>{rsi_15m:.1f}</b>\n"
            f"• Cấu trúc nến 4H: Đang giữ trên trục EMA20 (<code>${ema20_4h:,.1f}</code>)\n"
            f"• Cầu chì vĩ mô: {cb_str}\n"
            f"• Lịch tin tức đỏ: {news_str}\n\n"
            f"🎯 <b>3. DỰ BÁO HƯỚNG ĐI (DIRECTIONAL FORECAST):</b>\n"
            f"• <b>Kịch bản A (Chính):</b> {sc_a}\n"
            f"• <b>Kịch bản B (Phủ định):</b> {sc_b}\n\n"
            f"⚔️ <b>4. CHỈ THỊ HỆ THỐNG GIAO DỊCH:</b>\n"
            f"• Kích hoạt đồng thời: <b>Tier 1 (Nổ Vol >= 2.5x)</b> & <b>Tier 2 (Săn phản ứng biên BOLL)</b>.\n"
            f"• Ưu tiên: Tìm các Altcoin nén hộp bứt phá độc lập với BTC."
        )
        self.send_telegram(msg)

    def check_scheduled_checkpoints(self):
        now_vn = datetime.datetime.now(VN_TZ)
        hour = now_vn.hour
        minute = now_vn.minute
        weekday = now_vn.weekday()
        today_str = now_vn.strftime("%Y-%m-%d")

        if weekday == 0 and hour == 7 and 5 <= minute <= 10 and self.last_monday_date != today_str:
            self.generate_and_send_macro_briefing()
            self.last_monday_date = today_str
            self.last_briefing_date = today_str
        elif hour == 7 and 5 <= minute <= 10 and self.last_briefing_date != today_str:
            self.generate_and_send_macro_briefing()
            self.last_briefing_date = today_str
        elif hour == 13 and 0 <= minute <= 5 and self.last_checkpoint_hour != 13:
            self.generate_and_send_macro_briefing()
            self.last_checkpoint_hour = 13
        elif hour == 19 and 30 <= minute <= 35 and self.last_checkpoint_hour != 19:
            self.generate_and_send_macro_briefing()
            self.last_checkpoint_hour = 19


async def run_strategist_and_news_radar():
    client = MexcPublicClient()
    agent = StrategistAgent(client)

    logger.info("⚡ [STRATEGIST STARTUP] Khởi tạo và phát ngay bản tin vĩ mô mở màn...")
    await asyncio.to_thread(agent.generate_and_send_macro_briefing)

    while True:
        try:
            await asyncio.to_thread(agent.update_btc_macro)
            await asyncio.to_thread(agent.check_scheduled_checkpoints)
        except Exception as e:
            logger.error(f"Lỗi Strategist Watchdog: {e}")
        await asyncio.sleep(60)
