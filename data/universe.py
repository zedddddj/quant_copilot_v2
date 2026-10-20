import logging
import requests
import time
from typing import List, Dict, Any, Tuple

logger = logging.getLogger("UniverseManager")

BANNED_SUBSTRINGS = [
    "STOCK", "INDEX", "ETF", "FOREX", "COMMODITY", "SHARES", "DIREXION", 
    "ISHARES", "INVESCO", "VANGUARD", "SPDR", "PROSHARES", "BOEING",
    "APPLE", "TESLA", "NVIDIA", "MICROSOFT", "AMAZON", "GOOGLE", "ALPHABET",
    "SAMSUNG", "ALIBABA", "TAIWAN", "SEMICONDUCTOR", "KOREA"
]

KNOWN_TRADFI_SYMBOLS = {
    "NVIDIA_USDT", "NVDA_USDT", "MU_USDT", "KORU_USDT", "SOXL_USDT", "SOXS_USDT",
    "TSLA_USDT", "AAPL_USDT", "MSFT_USDT", "AMZN_USDT", "GOOGL_USDT", "META_USDT",
    "AMD_USDT", "INTC_USDT", "QCOM_USDT", "AVGO_USDT", "ARM_USDT", "TSM_USDT",
    "SMCI_USDT", "PLTR_USDT", "COIN_USDT", "MSTR_USDT", "BABA_USDT", "NIO_USDT",
    "NAS100_USDT", "SP500_USDT", "US30_USDT", "GOLD_USDT", "SILVER_USDT", "OIL_USDT"
}

class UniverseManager:
    def __init__(self, client):
        self.client = client
        self.tradfi_cache = set()
        self.last_fetch = 0

    def _update_tradfi_cache(self):
        url = "https://contract.mexc.com/api/v1/contract/detail"
        discovered = set(KNOWN_TRADFI_SYMBOLS)
        try:
            res = requests.get(url, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if data.get("success") and "data" in data:
                    for item in data["data"]:
                        sym = str(item.get("symbol", "")).upper()
                        display = str(item.get("displayName", "")).upper()
                        base = str(item.get("baseCoin", "")).upper()
                        if any(sub in display or sub in sym or sub in base for sub in BANNED_SUBSTRINGS):
                            discovered.add(sym)
                        if base.endswith(("3L", "3S", "2L", "2S")):
                            discovered.add(sym)
        except Exception as e:
            logger.warning(f"Lỗi fetch TradFi metadata: {e}")
        self.tradfi_cache = discovered
        self.last_fetch = time.time()

    def is_tradfi(self, symbol: str) -> bool:
        if not self.tradfi_cache or (time.time() - self.last_fetch > 21600):
            self._update_tradfi_cache()
        sym_u = symbol.upper()
        if sym_u in self.tradfi_cache:
            return True
        return any(sub in sym_u for sub in BANNED_SUBSTRINGS)

    def get_eligible_symbols(self, min_turnover_24h: float = 1500000.0) -> Tuple[List[Dict[str, Any]], List[str]]:
        tickers = self.client.get_all_tickers()
        if not tickers:
            return [], []

        eligible = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("_USDT") or self.is_tradfi(sym):
                continue
            amt = float(t.get("amount24", 0.0) or 0.0)
            if amt == 0.0:
                amt = float(t.get("volume24", 0.0) or 0.0) * float(t.get("lastPrice", 0.0) or 0.0)
            if amt >= min_turnover_24h:
                eligible.append({
                    "symbol": sym,
                    "volume_24h": amt,
                    "last_price": float(t.get("lastPrice", 0.0) or 0.0)
                })
        eligible.sort(key=lambda x: x["volume_24h"], reverse=True)
        return eligible, [x["symbol"] for x in eligible]
