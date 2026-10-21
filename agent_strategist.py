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
                # Bắn cảnh báo trước 60p và 30p kích hoạt chuỗi thảo luận
                if 25.0 <= diff_m <= 35.0 and f"{ev_id}_30" not in self.notified_news:
                    self.broadcast_macro_shock_thread(
                        event_title=f"Tin Đỏ USD: {ev.get('title')}",
                        detail=f"Sẽ công bố sau {abs(diff_m):.0f} phút ({ev_time.astimezone(VN_TZ).strftime('%H:%M')})"
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

    def broadcast_macro_shock_thread(self, event_title: str, detail: str):
        """CHUỖI 1: TỰ ĐỘNG THẢO LUẬN THEO CHUỖI KHI CÓ BIẾN CỐ VĨ MÔ / SHOCK BTC"""
        # 1. Agent 1 phát tin gốc
        msg_1 = (
            f"🚨 <b>[AGENT 1 - STRATEGIST] CẢNH BÁO VĨ MÔ</b>\n"
            f"• <b>Sự kiện:</b> {event_title}\n"
            f"• <b>Chi tiết:</b> {detail}\n"
            f"• <b>Yêu cầu:</b> Khóa mở vị thế mới, rà soát toàn bộ danh mục!"
        )
        root_id = self.send_telegram(msg_1)
        if not root_id:
            return

        time.sleep(0.8)
        # 2. Agent 2 phản hồi vào tin gốc
        self.send_telegram(
            "🤖 <b>[AGENT 2 - SCOUT]</b>: Đã nhận chỉ thị vĩ mô. Tạm ngưng quét tín hiệu Altcoin Long trong 45 phút.",
            reply_to_id=root_id
        )

        time.sleep(0.8)
        # 3. Agent 4 phản hồi rà soát lệnh
        self.send_telegram(
            "⏳ <b>[AGENT 4 - MONITOR]</b>: Đang rà soát các vị thế mở. Khuyến nghị kiểm tra dời Stop Loss về hòa vốn.",
            reply_to_id=root_id
        )

        time.sleep(0.8)
        # 4. Agent 5 phản hồi kích hoạt cầu chì
        self.send_telegram(
            "🛡️ <b>[AGENT 5 - RISK CONTROLLER]</b>: Đã kích hoạt chốt khóa mở lệnh mới (CIRCUIT_LOCK). Bảo toàn an toàn danh mục.",
            reply_to_id=root_id
        )

    def update_btc_macro(self) -> Dict[str, Any]:
        c15m, c1h, c4h, c1d = [], [], [], []

        for _ in range(3):
            k15m_raw = self.client.get_kline("BTC_USDT", interval="Min15", limit=60)
            k1h_raw = self.client.get_kline("BTC_USDT", interval="Min60", limit=60)
            k4h_raw = self.client.get_kline("BTC_USDT", interval="Min240", limit=60)
            k1d_raw = self.client.get_kline("BTC_USDT", interval="Day1", limit=10)

            c15m = [self._extract_ohlc(k) for k in (k15m_raw or []) if self._extract_ohlc(k)[3] > 0]
            c1h = [self._extract_ohlc(k) for k in (k1h_raw or []) if self._extract_ohlc(k)[3] > 0]
            c4h = [self._extract_ohlc(k) for k in (k4h_raw or []) if self._extract_ohlc(k)[3] > 0]
            c1d = [self._extract_ohlc(k) for k in (k1d_raw or []) if self._extract_ohlc(k)[3] > 0]

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

        # SỬA LỖI ĐỘNG LƯỢNG 15M: Tính trên cây nến đã đóng cửa hoàn chỉnh (c15m[-2])
        if len(c15m) >= 2:
            last_closed_15m = c15m[-2]
            open_15m = float(last_closed_15m[0])
            close_15m = float(last_closed_15m[3])
            btc_change_15m = abs(close_15m - open_15m) / open_15m if open_15m > 0 else 0.0
            rsi6_15m = float(self.calculate_rsi(c15m[:-1], 6))
        else:
            btc_change_15m = 0.0
            rsi6_15m = 50.0

        # Cầu chì nến 15M BTC >= 0.8% HOẶC RSI6 cực trị >= 85 / <= 15
        is_shock = (btc_change_15m >= 0.008) or (rsi6_15m >= 85.0) or (rsi6_15m <= 15.0)
        has_news, news_reason, next_ev = self.check_news_radar()

        # Kích hoạt chuỗi thảo luận nếu phát hiện BTC Shock
        if is_shock and not self.last_state.get("circuit_breaker", False):
            self.broadcast_macro_shock_thread(
                event_title=f"Biến động giật nến 15M ({btc_change_15m*100:.2f}%)",
                detail=f"RSI6 đạt mốc cực trị {rsi6_15m:.1f} tại vùng giá ${curr_btc:,.1f}"
            )

        closes_1h = [float(c[3]) for c in c1h] if c1h else [curr_btc]
        ema20_1h = sum(closes_1h[-20:]) / len(closes_1h[-20:])

        closes_4h = [float(c[3]) for c in c4h] if c4h else closes_1h
        ema20_4h = sum(closes_4h[-20:]) / len(closes_4h[-20:])

        if curr_btc > ema20_1h and curr_btc > ema20_4h:
            bias = "BULL"
            regime = "TREND_BULLISH"
        elif curr_btc < ema20_1h and curr_btc < ema20_4h:
            bias = "BEAR"
            regime = "TREND_BEARISH"
        else:
            bias = "NEUTRAL"
            regime = "RANGE_STABLE"

        funding_btc = self.client.get_funding_rate("BTC_USDT")
        oi_btc = self.client.get_open_interest("BTC_USDT")

        state = {
            "timestamp": datetime.datetime.now(VN_TZ).timestamp(),
            "btc_price": curr_btc,
            "btc_change_15m_pct": round(btc_change_15m * 100, 2),
            "rsi6_15m": round(rsi6_15m, 1),
            "circuit_breaker": is_shock,
            "news_freeze": has_news,
            "freeze_reason": news_reason if has_news else ("BTC xả giật nến 15M > 0.8%" if is_shock else "Bình thường"),
            "allow_trade": (not is_shock) and (not has_news),
            "bias": bias,
            "regime": regime,
            "funding_btc": round(funding_btc * 100, 4),
            "oi_btc": oi_btc,
            "next_news": next_ev,
            "pwh": max(c[1] for c in c4h[-42:]) if len(c4h) >= 42 else curr_btc * 1.05,
            "pwl": min(c[2] for c in c4h[-42:]) if len(c4h) >= 42 else curr_btc * 0.95,
            "daily_open": float(c1d[-1][0]) if c1d else curr_btc
        }

        self.last_state = state
        try:
            with open(STATE_MACRO_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Lỗi ghi state macro: {e}")

        return state

    def generate_and_send_macro_briefing(self, title_prefix: str = "BẢN TIN VĨ MÔ"):
        state = self.update_btc_macro()
        btc_p = state["btc_price"]
        regime = state["regime"]
        bias = state["bias"]
        chg_15m = state["btc_change_15m_pct"]
        rsi_15m = state["rsi6_15m"]
        funding = state.get("funding_btc", 0.0)
        next_ev = state.get("next_news")
        now_str = datetime.datetime.now(VN_TZ).strftime("%H:%M - %d/%m/%Y")

        news_str = f"🔴 {next_ev['title']} ({next_ev['time']} - còn {next_ev['minutes']}p)" if next_ev else "🟢 Không có tin tức đỏ USD đáng chú ý"
        cb_str = "🔴 KÍCH HOẠT (Tạm khóa Altcoin)" if state["circuit_breaker"] else "🟢 AN TOÀN (Cho phép quét Altcoin)"
        pwh = state.get("pwh", 0.0)
        pwl = state.get("pwl", 0.0)

        msg = (
            f"🌐 <b>[{title_prefix}] ({now_str})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>BTC Realtime:</b> <code>${btc_p:,.1f}</code> | <b>Bias:</b> <b>{bias}</b>\n"
            f"• <b>Cấu trúc Vĩ mô:</b> <b>{regime}</b>\n"
            f"• <b>Nến đóng 15M gần nhất:</b> <b>{chg_15m}%</b> | <b>RSI6:</b> <b>{rsi_15m}</b>\n"
            f"• <b>Funding Rate BTC:</b> <code>{funding:+0.4f}%</code>\n"
            f"• <b>Cản tuần HTF:</b> Đỉnh: <code>${pwh:,.1f}</code> | Đáy: <code>${pwl:,.1f}</code>\n"
            f"• <b>Cầu chì BTC:</b> {cb_str}\n"
            f"• <b>Lịch tin ForexFactory:</b> {news_str}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <i>Chiến lược: { 'Ưu tiên bám sóng đồng pha High-Beta' if 'TREND' in regime else 'Chỉ bắt bùng nổ nén hộp độc lập (Tier 1/Tier 2)' }</i>"
        )
        self.send_telegram(msg)

    def check_scheduled_checkpoints(self):
        """Báo cáo đa tầng: Báo cáo tuần Thứ Hai, 07:05 Hàng ngày, Checkpoint 13:00 & 19:30"""
        now_vn = datetime.datetime.now(VN_TZ)
        hour = now_vn.hour
        minute = now_vn.minute
        weekday = now_vn.weekday() # 0 là Thứ Hai
        today_str = now_vn.strftime("%Y-%m-%d")

        # 1. Báo cáo Chiến lược Tuần (07:05 Sáng Thứ Hai)
        if weekday == 0 and hour == 7 and 5 <= minute <= 10 and self.last_monday_date != today_str:
            self.generate_and_send_macro_briefing(title_prefix="BẢN TIN CHIẾN LƯỢC ĐẦU TUẦN (WEEKLY REGIME)")
            self.last_monday_date = today_str
            self.last_briefing_date = today_str

        # 2. Bản tin Sáng 07:05 Hàng ngày (Daily Close)
        elif hour == 7 and 5 <= minute <= 10 and self.last_briefing_date != today_str:
            self.generate_and_send_macro_briefing(title_prefix="BẢN TIN SÁNG 07:05 AM - DAILY CLOSE")
            self.last_briefing_date = today_str

        # 3. Checkpoint 13:00 PM (Giữa ngày)
        elif hour == 13 and 0 <= minute <= 5 and self.last_checkpoint_hour != 13:
            self.generate_and_send_macro_briefing(title_prefix="CHECKPOINT GIỮA NGÀY 13:00 PM")
            self.last_checkpoint_hour = 13

        # 4. Checkpoint 19:30 PM (Trước phiên Mỹ)
        elif hour == 19 and 30 <= minute <= 35 and self.last_checkpoint_hour != 19:
            self.generate_and_send_macro_briefing(title_prefix="CHECKPOINT TRƯỚC PHIÊN MỸ 19:30 PM")
            self.last_checkpoint_hour = 19


async def run_strategist_and_news_radar():
    client = MexcPublicClient()
    agent = StrategistAgent(client)

    logger.info("⚡ [STRATEGIST STARTUP] Phân tích vĩ mô tức thì ngay lúc khởi động...")
    await asyncio.to_thread(agent.generate_and_send_macro_briefing, "KHỞI ĐỘNG HỆ THỐNG - BẢN TIN VĨ MÔ BAN ĐẦU")

    while True:
        try:
            await asyncio.to_thread(agent.update_btc_macro)
            await asyncio.to_thread(agent.check_scheduled_checkpoints)
        except Exception as e:
            logger.error(f"Lỗi Strategist Watchdog: {e}")
        await asyncio.sleep(60)
