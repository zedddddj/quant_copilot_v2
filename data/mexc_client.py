import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging
import time
from typing import List, Dict, Any, Optional

logger = logging.getLogger("MEXC_Client")

class MexcPublicClient:
    BASE_URL = "https://contract.mexc.com/api/v1/contract"

    def __init__(self):
        self.session = requests.Session()
        
        # MỞ RỘNG CONNECTION POOL LÊN 100 ĐỂ CHẠY SONG SONG KHÔNG BỊ TRÀN POOL
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"]
        )
        adapter = HTTPAdapter(
            pool_connections=100,  # Cho phép giữ 100 connection sẵn sàng
            pool_maxsize=100,      # Tối đa 100 connection đồng thời
            max_retries=retry_strategy
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        self.session.headers.update({
            "User-Agent": "QuantCopilotV2/2.0",
            "Content-Type": "application/json"
        })

    def get_all_tickers(self) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/ticker"
        try:
            res = self.session.get(url, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    return data.get("data", [])
        except Exception as e:
            logger.error(f"Lỗi lấy Tickers: {e}")
        return []

    def get_kline(self, symbol: str, interval: str = "Min15", limit: int = 100) -> List[Any]:
        url = f"{self.BASE_URL}/kline/{symbol}"
        params = {"interval": interval, "limit": limit}
        try:
            res = self.session.get(url, params=params, timeout=6)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    return data.get("data", [])
        except Exception:
            pass
        return []

    def get_funding_rate(self, symbol: str) -> float:
        url = f"{self.BASE_URL}/funding_rate/{symbol}"
        try:
            res = self.session.get(url, timeout=6)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    return float(data.get("data", {}).get("fundingRate", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0

    def get_open_interest(self, symbol: str) -> float:
        url = f"{self.BASE_URL}/open_interest/{symbol}"
        try:
            res = self.session.get(url, timeout=6)
            if res.status_code == 200:
                data = res.json()
                if data.get("success"):
                    return float(data.get("data", {}).get("openInterest", 0.0) or 0.0)
        except Exception:
            pass
        return 0.0
