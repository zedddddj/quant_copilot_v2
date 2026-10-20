import logging
import math
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
    def calculate_correlation_and_beta(alt_closes: List[float], btc_closes: List[float]) -> tuple:
        """Tính hệ số tương quan Pearson và Beta giữa Altcoin và BTC trên 30 nến 15M gần nhất"""
        n = min(len(alt_closes), len(btc_closes), 30)
        if n < 15:
            return 0.0, 1.0

        alt_ret = [(alt_closes[i] - alt_closes[i - 1]) / alt_closes[i - 1] for i in range(-n + 1, 0)]
        btc_ret = [(btc_closes[i] - btc_closes[i - 1]) / btc_closes[i - 1] for i in range(-n + 1, 0)]

        mean_alt = sum(alt_ret) / len(alt_ret)
        mean_btc = sum(btc_ret) / len(btc_ret)

        cov = sum((a - mean_alt) * (b - mean_btc) for a, b in zip(alt_ret, btc_ret))
        var_alt = sum((a - mean_alt) ** 2 for a in alt_ret)
        var_btc = sum((b - mean_btc) ** 2 for b in btc_ret)

        if var_alt == 0 or var_btc == 0:
            return 0.0, 1.0

        correlation = cov / ((var_alt * var_btc) ** 0.5)
        beta = cov / var_btc if var_btc > 0 else 1.0
        return correlation, beta

    def scan_candidate(self, symbol: str, macro_state: Dict[str, Any], btc_candles_15m: List[tuple]) -> Optional[Dict[str, Any]]:
        """
        DUAL-ENGINE SCANNER:
        - Chế độ 1: Beta Trend (Đồng pha với BTC khi BTC có Trend)
        - Chế độ 2: Decoupled Alpha (Săn sóng riêng khi BTC Sideway/Range)
        """
        k1h_raw = self.client.get_kline(symbol, interval="Min60", limit=100)
        k15m_raw = self.client.get_kline(symbol, interval="Min15", limit=100)

        if not k1h_raw or not k15m_raw or len(k1h_raw) < 50 or len(k15m_raw) < 50:
            return None

        c1h = [self._extract_ohlc(k) for k in k1h_raw if self._extract_ohlc(k)[3] > 0][:-1]
        c15m = [self._extract_ohlc(k) for k in k15m_raw if self._extract_ohlc(k)[3] > 0][:-1]
        curr_p = float(k15m_raw[-1].get("close", 0.0) if isinstance(k15m_raw[-1], dict) else k15m_raw[-1][2])

        last_15m = c15m[-1]
        vols_15m = [float(c[4]) for c in c15m]
        ma20_vol_15m = sum(vols_15m[-21:-1]) / 20.0 if len(vols_15m) >= 21 else 1.0
        vol_ratio_15m = (float(last_15m[4]) / ma20_vol_15m) if ma20_vol_15m > 0 else 1.0

        closes_15m = [float(c[3]) for c in c15m]
        closes_1h = [float(c[3]) for c in c1h]

        ema12_15m = self.calculate_ema(closes_15m, 12)
        ema20_15m = self.calculate_ema(closes_15m, 20)
        ema20_1h = self.calculate_ema(closes_1h, 20)

        regime = macro_state.get("regime", "RANGE_STABLE")
        btc_bias = macro_state.get("bias", "NEUTRAL")

        # =========================================================================
        # CHẾ ĐỘ 1: ALTCOIN BÁM THEO BTC (HIGH-BETA TREND)
        # Kích hoạt khi BTC có sóng đẩy rõ rệt (TREND_BULLISH hoặc TREND_BEARISH)
        # =========================================================================
        if regime in ["TREND_BULLISH", "TREND_BEARISH"]:
            btc_closes_15m = [float(c[3]) for c in btc_candles_15m] if btc_candles_15m else []
            corr, beta = self.calculate_correlation_and_beta(closes_15m, btc_closes_15m)

            # Điều kiện 1: Tương quan thuận chặt chẽ (corr >= 0.70) và nhạy sóng (beta >= 1.1)
            # Điều kiện 2: Volume chỉ cần ổn định (>= 1.0x trung bình 20 nến)
            if corr >= 0.70 and beta >= 1.10 and vol_ratio_15m >= 1.0:
                if btc_bias == "BULL":
                    # Altcoin giữ trên EMA20 1H và đang test hỗ trợ EMA12/EMA20 15M
                    if last_15m[3] > ema20_1h and (curr_p >= ema20_15m * 0.995):
                        dist = abs(curr_p - ema12_15m) / curr_p
                        if dist <= 0.008:
                            return {
                                "symbol": symbol,
                                "bias": "LONG",
                                "engine_mode": "BETA_TREND",
                                "current_price": curr_p,
                                "vol_ratio": round(vol_ratio_15m, 2),
                                "corr_btc": round(corr, 2),
                                "beta": round(beta, 2),
                                "signal_desc": f"Sóng Đồng Pha BTC (Corr: {corr:.2f}, Beta: {beta:.2f}x) - Nảy hỗ trợ EMA12/20",
                                "ohlcv_closed": {"15m": c15m[-30:], "1h": c1h[-30:]}
                            }
                elif btc_bias == "BEAR":
                    if last_15m[3] < ema20_1h and (curr_p <= ema20_15m * 1.005):
                        dist = abs(curr_p - ema12_15m) / curr_p
                        if dist <= 0.008:
                            return {
                                "symbol": symbol,
                                "bias": "SHORT",
                                "engine_mode": "BETA_TREND",
                                "current_price": curr_p,
                                "vol_ratio": round(vol_ratio_15m, 2),
                                "corr_btc": round(corr, 2),
                                "beta": round(beta, 2),
                                "signal_desc": f"Sóng Xả Đồng Pha BTC (Corr: {corr:.2f}, Beta: {beta:.2f}x) - Hồi cản EMA12/20",
                                "ohlcv_closed": {"15m": c15m[-30:], "1h": c1h[-30:]}
                            }

        # =========================================================================
        # CHẾ ĐỘ 2: ALTCOIN SÓNG RIÊNG (DECOUPLED ALPHA BREAKOUT)
        # Kích hoạt khi BTC Sideway đi ngang (RANGE_STABLE) hoặc tiền xoay vòng
        # =========================================================================
        # Điều kiện khắt khe: Volume đột biến nổ >= 2.5x
        if vol_ratio_15m >= 2.5:
            up_bb_1h, mid_bb_1h, low_bb_1h, bw_1h = self.calculate_bollinger_bands(c1h, 20)
            last_1h = c1h[-1]

            bias = None
            # Nén hộp Bollinger Bands 1H co thắt <= 5% rồi bung mạnh
            if bw_1h <= 0.05:
                if last_1h[3] >= up_bb_1h and btc_bias != "BEAR":
                    bias = "LONG"
                elif last_1h[3] <= low_bb_1h and btc_bias != "BULL":
                    bias = "SHORT"
            else:
                # Đột phá nến 15M vượt cản độc lập
                if last_15m[3] > mid_bb_1h and btc_bias != "BEAR":
                    bias = "LONG"
                elif last_15m[3] < mid_bb_1h and btc_bias != "BULL":
                    bias = "SHORT"

            if bias:
                return {
                    "symbol": symbol,
                    "bias": bias,
                    "engine_mode": "DECOUPLED_ALPHA",
                    "current_price": curr_p,
                    "vol_ratio": round(vol_ratio_15m, 2),
                    "bw_1h": round(bw_1h * 100, 2),
                    "signal_desc": f"Bùng Nổ Độc Lập Nén Hộp (Vol: {vol_ratio_15m:.1f}x MA20, BBW 1H: {bw_1h*100:.1f}%)",
                    "ohlcv_closed": {"15m": c15m[-30:], "1h": c1h[-30:]}
                }

        return None


async def run_scout_and_auditor_loop():
    """Vòng lặp phối hợp Agent 2 (Scout), Agent 3 (Auditor) & Agent 5 (Risk Controller)"""
    import json
    import os
    import asyncio
    from config import STATE_MACRO_FILE
    from data.universe import UniverseManager
    from agent_auditor import RiskAuditorAgent
    from agent_risk import RiskControllerAgent

    client = MexcPublicClient()
    universe = UniverseManager(client)
    scout = ScoutAgent(client)
    auditor = RiskAuditorAgent()
    risk_ctrl = RiskControllerAgent()

    logger.info("Agent 2, 3 & 5 (Scout, Auditor & Risk) đã kích hoạt chu kỳ 180s (Dual-Engine Mode).")

    while True:
        try:
            # 1. Đọc macro_state.json từ Agent 1
            macro = {}
            if os.path.exists(STATE_MACRO_FILE):
                try:
                    with open(STATE_MACRO_FILE, "r", encoding="utf-8") as f:
                        macro = json.load(f)
                except Exception:
                    pass

            # Gatekeeper: Nếu có tin tức đỏ hoặc BTC biến động sốc -> Dừng chu kỳ
            if not macro.get("allow_trade", True):
                freeze_reason = macro.get("freeze_reason", "Cầu chì kích hoạt")
                logger.warning(f"⏸️ [GATEKEEPER CHẶN QUÉT]: {freeze_reason}")
                await asyncio.sleep(180)
                continue

            regime = macro.get("regime", "RANGE_STABLE")
            mode_desc = "CHẾ ĐỘ 1: BÁM SÓNG ĐỒNG PHA BTC (BETA)" if "TREND" in regime else "CHẾ ĐỘ 2: SĂN SÓNG ĐỘC LẬP (ALPHA)"
            logger.info(f"🔄 [SCOUT DUAL-ENGINE] Trạng thái: {regime} ➔ Kích hoạt {mode_desc}")

            # Kéo nến 15M của BTC để làm mốc đối chiếu tương quan
            btc_raw = await asyncio.to_thread(client.get_kline, "BTC_USDT", "Min15", 35)
            btc_candles_15m = [scout._extract_ohlc(k) for k in (btc_raw or []) if scout._extract_ohlc(k)[3] > 0][:-1]

            # 2. Lọc sàn thanh khoản >= 1.5M USDT
            eligible, _ = await asyncio.to_thread(universe.get_eligible_symbols, 1500000.0)
            target_symbols = [x["symbol"] for x in eligible[:120]]

            # 3. Agent 2 quét ứng viên theo 2 chế độ song song
            candidates = []
            for sym in target_symbols:
                c = await asyncio.to_thread(scout.scan_candidate, sym, macro, btc_candles_15m)
                if c:
                    candidates.append(c)
                await asyncio.sleep(0.02)

            logger.info(f"🎯 Scout tìm thấy {len(candidates)} ứng viên hợp lệ.")

            # 4. Agent 3 thẩm định an toàn & Agent 5 duyệt rủi ro
            for item in candidates[:3]:
                funding = await asyncio.to_thread(client.get_funding_rate, item["symbol"])
                can_open, risk_reason = risk_ctrl.can_open_position(item["bias"], funding)
                if not can_open:
                    logger.info(f"Agent 5 Veto {item['symbol']}: {risk_reason}")
                    continue

                is_valid, audit_reason, plan = await asyncio.to_thread(auditor.audit_trade, item)
                if is_valid:
                    # Gán nhãn chế độ vào plan để hiển thị rõ lên Telegram
                    plan["engine_mode"] = item.get("engine_mode", "ALPHA")
                    plan["signal_desc"] = item.get("signal_desc", "")
                    await asyncio.to_thread(auditor.broadcast_signal, plan)
                    await asyncio.sleep(1.0)
                else:
                    logger.info(f"Agent 3 Veto {item['symbol']}: {audit_reason}")

        except Exception as e:
            logger.error(f"Lỗi vòng quét Scout/Auditor: {e}")

        await asyncio.sleep(180)
