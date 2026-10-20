import json
import os
import time
import logging
from typing import Dict, Any, Tuple
from config import STATE_RISK_FILE, STATE_TRADES_FILE

logger = logging.getLogger("TheRiskController")

class RiskControllerAgent:
    def __init__(self):
        self._ensure_state_files()

    def _ensure_state_files(self):
        if not os.path.exists(STATE_RISK_FILE):
            initial_state = {
                "recent_stop_losses": [],
                "circuit_breaker_lock_until": 0
            }
            with open(STATE_RISK_FILE, "w", encoding="utf-8") as f:
                json.dump(initial_state, f, indent=2)

    def load_risk_state(self) -> Dict[str, Any]:
        try:
            with open(STATE_RISK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"recent_stop_losses": [], "circuit_breaker_lock_until": 0}

    def record_stop_loss(self, symbol: str):
        state = self.load_risk_state()
        now = time.time()
        recent_sl = [sl for sl in state.get("recent_stop_losses", []) if (now - sl["time"]) < 43200]
        recent_sl.append({"symbol": symbol, "time": now})
        state["recent_stop_losses"] = recent_sl

        if len(recent_sl) >= 2:
            state["circuit_breaker_lock_until"] = now + 43200
            logger.critical("🚨 CẦU CHÌ TÀI KHOẢN KÍCH HOẠT: Dính 2 SL liên tiếp! Khóa mở lệnh 12 tiếng.")

        with open(STATE_RISK_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def can_open_position(self, direction: str, funding_rate: float) -> Tuple[bool, str]:
        state = self.load_risk_state()
        now = time.time()

        lock_until = state.get("circuit_breaker_lock_until", 0)
        if now < lock_until:
            rem_h = (lock_until - now) / 3600.0
            return False, f"Cầu chì tài khoản đang kích hoạt: Khóa mở lệnh còn {rem_h:.1f}h do dính 2 SL liên tiếp"

        fr_pct = funding_rate * 100.0
        if direction == "LONG" and fr_pct >= 0.10:
            return False, f"Funding Rate quá cao ({fr_pct:.3f}% >= +0.10%), rủi ro Long bị đè phí"
        if direction == "SHORT" and fr_pct <= -0.10:
            return False, f"Funding Rate quá âm ({fr_pct:.3f}% <= -0.10%), rủi ro Short bị đè phí"

        try:
            if os.path.exists(STATE_TRADES_FILE):
                with open(STATE_TRADES_FILE, "r", encoding="utf-8") as f:
                    trades = json.load(f)
                same_dir_count = sum(1 for t in trades.values() if t.get("direction") == direction and t.get("status") == "OPEN")
                if same_dir_count >= 2:
                    return False, f"Đã có {same_dir_count} vị thế {direction} đang mở. Giới hạn tối đa 2 vị thế cùng chiều!"
        except Exception:
            pass

        return True, "Hợp lệ"
