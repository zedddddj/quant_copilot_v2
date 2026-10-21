import logging
import math
import asyncio
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
    def calculate_bollinger_bands(candles: list, period: int = 20) -> tuple:
        if len(candles) < period:
            return 0.0, 0.0, 0.0, 1.0
        closes = [float(c[3]) for c in candles[-period:]]
        sma = sum(closes) / period
        variance = sum((x - sma) ** 2 for x in closes) / period
        std = (variance ** 0.5)
        upper = sma + (2.0 * std)
        lower = sma - (2.0 * std)
        bandwidth = (upper - lower) / sma if sma > 0 else 0.0
        return upper, sma, lower, bandwidth

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

    @staticmethod
    def calculate_adx(candles: list, period: int = 14) -> float:
        if len(candles) < period * 2:
            return 20.0
        tr_list, dm_pos, dm_neg = [], [], []
        for i in range(1, len(candles)):
            h, l = float(candles[i][1]), float(candles[i][2])
            prev_h, prev_l, prev_c = float(candles[i-1][1]), float(candles[i-1][2]), float(candles[i-1][3])
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            up_move = h - prev_h
            down_move = prev_l - l
            tr_list.append(tr)
            dm_pos.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            dm_neg.append(down_move if down_move > up_move and down_move > 0 else 0.0)

        if len(tr_list) < period:
            return 20.0

        atr = sum(tr_list[:period])
        smooth_pos = sum(dm_pos[:period])
        smooth_neg = sum(dm_neg[:period])
        dx_list = []

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

        return sum(dx_list[-period:]) / period if len(dx_list) >= period else 20.0

    async def scan_single_symbol(self, symbol: str, macro_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        TẦNG 2: LỌC SÂU ĐA KHUNG (Chạy song song bằng asyncio cho từng coin)
        """
        loop = asyncio.get_running_loop()
        # Kéo đồng thời nến 4H, 1H và 15M
        k4h_fut = loop.run_in_executor(None, self.client.get_kline, symbol, "Min240", 40)
        k1h_fut = loop.run_in_executor(None, self.client.get_kline, symbol, "Min60", 60)
        k15m_fut = loop.run_in_executor(None, self.client.get_kline, symbol, "Min15", 60)

        k4h_raw, k1h_raw, k15m_raw = await asyncio.gather(k4h_fut, k1h_fut, k15m_fut)

        if not k4h_raw or not k1h_raw or not k15m_raw or len(k15m_raw) < 30 or len(k4h_raw) < 25:
            return None

        c4h = [self._extract_ohlc(k) for k in k4h_raw if self._extract_ohlc(k)[3] > 0]
        c1h = [self._extract_ohlc(k) for k in k1h_raw if self._extract_ohlc(k)[3] > 0]
        c15m = [self._extract_ohlc(k) for k in k15m_raw if self._extract_ohlc(k)[3] > 0]

        curr_p = float(c15m[-1][3])
        c15m_closed = c15m[:-1]
        c1h_closed = c1h[:-1]
        c4h_closed = c4h[:-1]

        last_15m = c15m_closed[-1]
        vols_15m = [float(c[4]) for c in c15m_closed]
        avg_vol_20 = sum(vols_15m[-21:-1]) / 20.0 if len(vols_15m) >= 21 else 1.0
        vol_spike = (float(last_15m[4]) / avg_vol_20) if avg_vol_20 > 0 else 1.0

        adx_15m = self.calculate_adx(c15m_closed, 14)
        up_bb_1h, mid_bb_1h, low_bb_1h, bw_1h = self.calculate_bollinger_bands(c1h_closed, 20)
        up_bb_15m, mid_bb_15m, low_bb_15m, _ = self.calculate_bollinger_bands(c15m_closed, 20)
        rsi6_15m = self.calculate_rsi(c15m_closed, 6)

        # Tính chỉ báo 4H để cung cấp cho Auditor Veto
        up_bb_4h, mid_bb_4h, low_bb_4h, _ = self.calculate_bollinger_bands(c4h_closed, 20)
        rsi6_4h = self.calculate_rsi(c4h_closed, 6)

        # =========================================================================
        # 1. TIÊU CHUẨN TIER 1: SÓNG MẠNH (TREND EXPANSION)
        # =========================================================================
        if vol_spike >= 2.5 and adx_15m >= 30.0 and bw_1h <= 0.05:
            last_1h = c1h_closed[-1]
            bias = "LONG" if last_1h[3] >= up_bb_1h else ("SHORT" if last_1h[3] <= low_bb_1h else None)
            if bias:
                return {
                    "symbol": symbol,
                    "tier": "TIER_1_STRONG_TREND",
                    "bias": bias,
                    "current_price": curr_p,
                    "vol_spike": round(vol_spike, 2),
                    "adx": round(adx_15m, 1),
                    "bw_1h": round(bw_1h * 100, 2),
                    "signal_desc": f"Tier 1 Sóng Mạnh (Vol {vol_spike:.1f}x, ADX {adx_15m:.1f}, BBW {bw_1h*100:.1f}%)",
                    "indicators_4h": {"rsi6": rsi6_4h, "boll_ub": up_bb_4h, "boll_lb": low_bb_4h},
                    "indicators_15m": {"boll_ub": up_bb_15m, "boll_mid": mid_bb_15m, "boll_lb": low_bb_15m, "rsi6": rsi6_15m},
                    "ohlcv_closed_15m": c15m_closed[-30:]
                }

        # =========================================================================
        # 2. TIÊU CHUẨN TIER 2: SÓNG NGẮN (RANGE SCALPING)
        # =========================================================================
        if vol_spike >= 1.2:
            o_p, h_p, l_p, c_p = float(last_15m[0]), float(last_15m[1]), float(last_15m[2]), float(last_15m[3])
            body = abs(c_p - o_p)

            # Xét Scalp Long tại mép dưới (BOLL LB)
            if last_15m[2] <= low_bb_15m and rsi6_15m <= 28.0:
                lower_wick = min(o_p, c_p) - l_p
                # Nến rút chân từ chối giá: râu dưới >= 1.2x thân nến
                if lower_wick >= (1.2 * body):
                    # Khoảng cách từ Entry đến trục giữa BOLL Mid >= 0.9%
                    dist_to_mid = (mid_bb_15m - curr_p) / curr_p
                    if dist_to_mid >= 0.009:
                        return {
                            "symbol": symbol,
                            "tier": "TIER_2_SCALP_QUICK",
                            "bias": "LONG",
                            "current_price": curr_p,
                            "vol_spike": round(vol_spike, 2),
                            "adx": round(adx_15m, 1),
                            "signal_desc": f"Tier 2 Scalp Biên Dưới (Râu rút chân {lower_wick/max(body,1e-6):.1f}x, Biên tới Mid {dist_to_mid*100:.2f}%)",
                            "indicators_4h": {"rsi6": rsi6_4h, "boll_ub": up_bb_4h, "boll_lb": low_bb_4h},
                            "indicators_15m": {"boll_ub": up_bb_15m, "boll_mid": mid_bb_15m, "boll_lb": low_bb_15m, "rsi6": rsi6_15m},
                            "ohlcv_closed_15m": c15m_closed[-30:]
                        }

            # Xét Scalp Short tại mép trên (BOLL UB)
            elif last_15m[1] >= up_bb_15m and rsi6_15m >= 72.0:
                upper_wick = h_p - max(o_p, c_p)
                # Nến rụt đầu từ chối giá: râu trên >= 1.2x thân nến
                if upper_wick >= (1.2 * body):
                    dist_to_mid = (curr_p - mid_bb_15m) / curr_p
                    if dist_to_mid >= 0.009:
                        return {
                            "symbol": symbol,
                            "tier": "TIER_2_SCALP_QUICK",
                            "bias": "SHORT",
                            "current_price": curr_p,
                            "vol_spike": round(vol_spike, 2),
                            "adx": round(adx_15m, 1),
                            "signal_desc": f"Tier 2 Scalp Biên Trên (Râu rụt đầu {upper_wick/max(body,1e-6):.1f}x, Biên tới Mid {dist_to_mid*100:.2f}%)",
                            "indicators_4h": {"rsi6": rsi6_4h, "boll_ub": up_bb_4h, "boll_lb": low_bb_4h},
                            "indicators_15m": {"boll_ub": up_bb_15m, "boll_mid": mid_bb_15m, "boll_lb": low_bb_15m, "rsi6": rsi6_15m},
                            "ohlcv_closed_15m": c15m_closed[-30:]
                        }

        return None

    async def scan_market_parallel(self, symbols: List[str], macro_state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Gửi request song song cho toàn bộ 25-30 coin, hoàn tất < 5s"""
        tasks = [self.scan_single_symbol(sym, macro_state) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_candidates = [r for r in results if isinstance(r, dict) and r is not None]
        return valid_candidates
