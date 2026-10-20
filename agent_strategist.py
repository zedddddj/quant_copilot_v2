import json
import logging
import datetime
import requests
from typing import Dict, Any, List, Tuple
from config import VN_TZ, STATE_MACRO_FILE
from data.mexc_client import MexcPublicClient

logger = logging.getLogger("TheStrategist")

class StrategistAgent:
    def __init__(self, client: MexcPublicClient):
        self.client = client
        self.last_news_fetch = 0
        self.cached_events = []

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
            logger.warning(f"Lỗi lấy lịch ForexFactory: {e}")
        return self.cached_events

    def check_news_impact(self) -> Tuple[bool, str]:
        events = self.fetch_forex_factory_calendar()
        if not events:
            return False, "Không có tin tức USD High Impact"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        for ev in events:
            date_str = ev.get("date")
            if not date_str:
                continue
            try:
                ev_time = datetime.datetime.fromisoformat(date_str)
                diff_minutes = (ev_time - now_utc).total_seconds() / 60.0
                if -30.0 <= diff_minutes <= 30.0:
                    return True, f"NEWS_FREEZE: Cờ đỏ USD [{ev.get('title')}] lúc {ev_time.astimezone(VN_TZ).strftime('%H:%M')} (cách {abs(diff_minutes):.0f} phút)"
            except Exception:
                continue
        return False, "An toàn trước tin tức"

    def update_btc_macro(self) -> Dict[str, Any]:
        k15m_raw = self.client.get_kline("BTC_USDT", interval="Min15", limit=30)
        k4h_raw = self.client.get_kline("BTC_USDT", interval="Min240", limit=50)

        c15m = [self._extract_ohlc(k) for k in (k15m_raw or []) if self._extract_ohlc(k)[3] > 0]
        c4h = [self._extract_ohlc(k) for k in (k4h_raw or []) if self._extract_ohlc(k)[3] > 0]

        if not c15m:
            return {"mode": "TREND_MACRO", "allow_trade": True, "bias": "NEUTRAL"}

        last_15m = c15m[-1]
        btc_change_15m = abs(last_15m[3] - last_15m[0]) / last_15m[0] if last_15m[0] > 0 else 0.0

        is_shock = btc_change_15m > 0.008
        has_news, news_reason = self.check_news_impact()

        pwh = max(c[1] for c in c4h[-42:]) if len(c4h) >= 42 else (c15m[-1][1] * 1.05)
        pwl = min(c[2] for c in c4h[-42:]) if len(c4h) >= 42 else (c15m[-1][2] * 0.95)

        closes_4h = [c[3] for c in c4h]
        ema20_4h = closes_4h[-1]
        if len(closes_4h) >= 20:
            m = 2.0 / 21.0
            ema = sum(closes_4h[:20]) / 20.0
            for p in closes_4h[20:]:
                ema = (p - ema) * m + ema
            ema20_4h = ema

        curr_btc = last_15m[3]
        bias = "BULL" if curr_btc > ema20_4h else "BEAR"

        state = {
            "timestamp": datetime.datetime.now(VN_TZ).timestamp(),
            "btc_price": curr_btc,
            "btc_change_15m_pct": round(btc_change_15m * 100, 2),
            "is_shock_freeze": is_shock,
            "has_news_freeze": has_news,
            "news_reason": news_reason,
            "allow_trade": (not is_shock) and (not has_news),
            "bias": bias,
            "pwh": pwh,
            "pwl": pwl
        }

        try:
            with open(STATE_MACRO_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Lỗi ghi state macro: {e}")

        return state
