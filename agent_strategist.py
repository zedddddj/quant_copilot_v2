import json
import logging
import datetime
import requests
import asyncio
from typing import Dict, Any, List, Tuple
from config import VN_TZ, STATE_MACRO_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM
from data.mexc_client import MexcPublicClient

logger = logging.getLogger("TheStrategist")

class StrategistAgent:
    def __init__(self, client: MexcPublicClient):
        self.client = client
        self.last_news_fetch = 0
        self.cached_events = []
        self.notified_news = set()

    @staticmethod
    def _extract_ohlc(k) -> tuple:
        if isinstance(k, dict):
            return (
                float(k.get("open", 0.0) or 0.0),
                float(k.get("high", 0.0) or 0.0),
                float(k.get("low", 0.0) or 0.0),
                float(k.get("close", 0.0) or 0.0),
                float(k.get("volume", 0.0) or 0.0)
            )
        elif isinstance(k, (list, tuple)) and len(k) >= 5:
            try:
                o, c, h, l = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                if h < l:
                    h, l = l, h
                v = float(k[5]) if len(k) >= 6 else 0.0
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

    def send_telegram(self, text: str):
        if not ENABLE_TELEGRAM or not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=8)
        except Exception as e:
            logger.error(f"Lỗi gửi tin tức Strategist: {e}")

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
            logger.warning(f"Lỗi tải lịch ForexFactory: {e}")
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

                # Bắn cảnh báo trước 60p và 30p
                ev_id = f"{ev.get('title')}_{date_str}"
                if 25.0 <= diff_m <= 35.0 and f"{ev_id}_30" not in self.notified_news:
                    self.send_telegram(f"🚨 <b>[CẢNH BÁO TIN ĐỎ USD TRONG 30 PHÚT]</b>\nSự kiện: <b>{ev.get('title')}</b>\nKích hoạt khóa mở lệnh mới (NEWS_FREEZE)!")
                    self.notified_news.add(f"{ev_id}_30")
                elif 55.0 <= diff_m <= 65.0 and f"{ev_id}_60" not in self.notified_news:
                    self.send_telegram(f"⚠️ <b>[CẢNH BÁO TIN ĐỎ USD TRONG 60 PHÚT]</b>\nSự kiện: <b>{ev.get('title')}</b> lúc {ev_time.astimezone(VN_TZ).strftime('%H:%M')}.")
                    self.notified_news.add(f"{ev_id}_60")

                # Cờ NEWS_FREEZE trong khoảng [-30m, +30m]
                if -30.0 <= diff_m <= 30.0:
                    active_freeze = True
                    freeze_reason = f"NEWS_FREEZE: Tin đỏ USD [{ev.get('title')}] (cách {abs(diff_m):.0f} phút)"

                if diff_m > 0 and (next_event is None or diff_m < next_event["minutes"]):
                    next_event = {"title": ev.get("title"), "minutes": round(diff_m), "time": ev_time.astimezone(VN_TZ).strftime('%H:%M')}
            except Exception:
                continue

        return active_freeze, freeze_reason or "An toàn trước tin tức", next_event

    def update_btc_macro(self) -> Dict[str, Any]:
        k15m_raw = self.client.get_kline("BTC_USDT", interval="Min15", limit=100)
        k1h_raw = self.client.get_kline("BTC_USDT", interval="Min60", limit=100)
        k4h_raw = self.client.get_kline("BTC_USDT", interval="Min240", limit=60)

        c15m = [self._extract_ohlc(k) for k in (k15m_raw or []) if self._extract_ohlc(k)[3] > 0]
        c1h = [self._extract_ohlc(k) for k in (k1h_raw or []) if self._extract_ohlc(k)[3] > 0]
        c4h = [self._extract_ohlc(k) for k in (k4h_raw or []) if self._extract_ohlc(k)[3] > 0]

        if not c15m or not c1h:
            return {"regime": "UNKNOWN", "allow_trade": True, "bias": "NEUTRAL"}

        last_15m = c15m[-1]
        curr_btc = float(last_15m[3])
        btc_change_15m = abs(curr_btc - float(last_15m[0])) / float(last_15m[0])
        rsi6_15m = float(self.calculate_rsi(c15m, 6))

        # Cầu chì nến 15M BTC >= 0.8% HOẶC RSI6 >= 85 hoặc <= 15
        is_shock = (btc_change_15m >= 0.008) or (rsi6_15m >= 85.0) or (rsi6_15m <= 15.0)
        has_news, news_reason, next_ev = self.check_news_radar()

        closes_1h = [float(c[3]) for c in c1h]
        ema20_1h = sum(closes_1h[-20:]) / 20.0 if len(closes_1h) >= 20 else curr_btc

        closes_4h = [float(c[3]) for c in c4h] if c4h else closes_1h
        ema20_4h = sum(closes_4h[-20:]) / 20.0 if len(closes_4h) >= 20 else curr_btc

        if curr_btc > ema20_1h and curr_btc > ema20_4h:
            bias = "BULL"
            regime = "TREND_BULLISH"
        elif curr_btc < ema20_1h and curr_btc < ema20_4h:
            bias = "BEAR"
            regime = "TREND_BEARISH"
        else:
            bias = "NEUTRAL"
            regime = "RANGE_STABLE"

        state = {
            "timestamp": datetime.datetime.now(VN_TZ).timestamp(),
            "btc_price": curr_btc,
            "btc_change_15m_pct": round(btc_change_15m * 100, 2),
            "rsi6_15m": round(rsi6_15m, 1),
            "circuit_breaker": is_shock,
            "news_freeze": has_news,
            "freeze_reason": news_reason if has_news else ("BTC biến động sốc 15M" if is_shock else "Bình thường"),
            "allow_trade": (not is_shock) and (not has_news),
            "bias": bias,
            "regime": regime,
            "next_news": next_ev
        }

        try:
            with open(STATE_MACRO_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Lỗi ghi state macro: {e}")

        return state

async def run_strategist_and_news_radar():
    """Tác vụ chạy ngầm độc lập của Agent 1"""
    client = MexcPublicClient()
    agent = StrategistAgent(client)
    logger.info("Agent 1 (Strategist & News Radar) đã kích hoạt.")

    while True:
        try:
            await asyncio.to_thread(agent.update_btc_macro)
        except Exception as e:
            logger.error(f"Lỗi vòng lặp Strategist: {e}")
        await asyncio.sleep(60)
