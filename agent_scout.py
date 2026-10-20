import logging
from typing import List, Dict, Any, Optional
from data.mexc_client import MexcPublicClient

logger = logging.getLogger("TheScout")

class ScoutAgent:
    def __init__(self, client: MexcPublicClient):
        self.client = client

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
    def calculate_ema(closes: List[float], period: int) -> float:
        if len(closes) < period:
            return closes[-1] if closes else 0.0
        multiplier = 2.0 / (period + 1.0)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def calculate_bollinger_bands(candles: List[tuple], period: int = 20) -> tuple:
        if len(candles) < period:
            return 0.0, 0.0, 0.0, 0.0
        closes = [c[3] for c in candles[-period:]]
        sma = sum(closes) / period
        variance = sum((x - sma) ** 2 for x in closes) / period
        std = (variance ** 0.5)
        upper = sma + (2.0 * std)
        lower = sma - (2.0 * std)
        bw = (upper - lower) / sma if sma > 0 else 0.0
        return upper, sma, lower, bw

    @staticmethod
    def calculate_rsi(candles: List[tuple], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(-period, 0):
            diff = candles[i][3] - candles[i - 1][3]
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

    @staticmethod
    def calculate_adx_dmi(candles: List[tuple], period: int = 14) -> tuple:
        if len(candles) < period * 2:
            return 20.0, 20.0, 20.0
        tr_list, dm_pos, dm_neg = [], [], []
        for i in range(1, len(candles)):
            h, l, prev_h, prev_l, prev_c = candles[i][1], candles[i][2], candles[i-1][1], candles[i-1][2], candles[i-1][3]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            up_move = h - prev_h
            down_move = prev_l - l
            tr_list.append(tr)
            dm_pos.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            dm_neg.append(down_move if down_move > up_move and down_move > 0 else 0.0)

        if len(tr_list) < period:
            return 20.0, 20.0, 20.0

        atr = sum(tr_list[:period])
        smooth_pos = sum(dm_pos[:period])
        smooth_neg = sum(dm_neg[:period])
        dx_list = []
        di_pos, di_neg = 20.0, 20.0

        for i in range(period, len(tr_list)):
            atr = atr - (atr / period) + tr_list[i]
            smooth_pos = smooth_pos - (smooth_pos / period) + dm_pos[i]
            smooth_neg = smooth_neg - (smooth_neg / period) + dm_neg[i]
            if atr == 0:
                continue
            di_pos = (smooth_pos / atr) * 100.0
            di_neg = (smooth_neg / atr) * 100.0
            di_sum = di_pos + di_neg
            dx = (abs(di_pos - di_neg) / di_sum) * 100.0 if di_sum > 0 else 0.0
            dx_list.append(dx)

        adx = sum(dx_list[-period:]) / period if len(dx_list) >= period else 20.0
        return adx, di_pos, di_neg

    def scan_candidate(self, symbol: str, btc_bias: str) -> Optional[Dict[str, Any]]:
        k15m_raw = self.client.get_kline(symbol, interval="Min15", limit=100)
        k1h_raw = self.client.get_kline(symbol, interval="Min60", limit=100)
        if not k15m_raw or len(k15m_raw) < 70:
            return None

        raw_15m = [self._extract_ohlc(k) for k in k15m_raw if self._extract_ohlc(k)[3] > 0]
        raw_1h = [self._extract_ohlc(k) for k in (k1h_raw or []) if self._extract_ohlc(k)[3] > 0]

        c15m = raw_15m[:-1]
        c1h = raw_1h[:-1] if len(raw_1h) >= 20 else c15m
        curr_p = float(raw_15m[-1][3])

        last_c = c15m[-1]
        vols_15m = [float(c[4]) for c in c15m]
        ma20_vol = sum(vols_15m[-21:-1]) / 20.0 if len(vols_15m) >= 21 else 1.0

        vol_ratio = (float(last_c[4]) / ma20_vol) if ma20_vol > 0 else 1.0

        closes_15m = [float(c[3]) for c in c15m]
        ema20_15m = self.calculate_ema(closes_15m, 20)
        ema50_15m = self.calculate_ema(closes_15m, 50)
        adx15m, di_pos, di_neg = self.calculate_adx_dmi(c15m, 14)
        up_bb, mid_bb, low_bb, bbw = self.calculate_bollinger_bands(c15m, 20)
        rsi6_15m = self.calculate_rsi(c15m, 6)

        is_bull = (adx15m >= 22.0) and (di_pos > di_neg) and (last_c[3] > ema50_15m)
        is_bear = (adx15m >= 22.0) and (di_neg > di_pos) and (last_c[3] < ema50_15m)

        bias = None
        setup_desc = ""

        if vol_ratio >= 2.5:
            if is_bull and btc_bias != "BEAR":
                bias = "LONG"
                setup_desc = f"Bùng nổ nén hộp Vol lớn ({vol_ratio:.1f}x MA20), phá cản tăng"
            elif is_bear and btc_bias != "BULL":
                bias = "SHORT"
                setup_desc = f"Bùng nổ nén hộp Vol lớn ({vol_ratio:.1f}x MA20), phá cản giảm"

        if not bias:
            if is_bull and (48.0 <= rsi6_15m <= 68.0) and btc_bias != "BEAR":
                dist = (curr_p - ema20_15m) / curr_p
                if 0.002 <= dist <= 0.015:
                    bias = "LONG"
                    setup_desc = f"Trend Tăng (ADX {adx15m:.1f}), Pullback cản EMA20 (RSI: {rsi6_15m:.1f})"
            elif is_bear and (32.0 <= rsi6_15m <= 52.0) and btc_bias != "BULL":
                dist = (ema20_15m - curr_p) / curr_p
                if 0.002 <= dist <= 0.015:
                    bias = "SHORT"
                    setup_desc = f"Trend Giảm (ADX {adx15m:.1f}), Hồi cản EMA20 (RSI: {rsi6_15m:.1f})"

        if not bias:
            return None

        return {
            "symbol": symbol,
            "bias": bias,
            "current_price": curr_p,
            "signal_desc": setup_desc,
            "vol_ratio": vol_ratio,
            "indicators": {
                "adx": adx15m,
                "ema20": ema20_15m,
                "bbw": bbw,
                "bb": {"upper": up_bb, "mid": mid_bb, "lower": low_bb}
            },
            "ohlcv_closed": {
                "15m": c15m[-30:],
                "1h": c1h[-30:]
            }
        }
