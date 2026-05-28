"""
Tracking Module — Enrichit le suivi des trades dans Supabase
Version 1.0 — Séparé du bot principal pour ne pas impacter la logique de trading

Fonctionnalités :
- Calcul Risk/Reward par trade
- Tracking du type de signal (CAS1, CAS2, PU)
- Calcul du drawdown max par trade
- Tracking des TP atteints
- Enrichissement des données envoyées à Supabase
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict

log = logging.getLogger(__name__)


class TradeTracker:
    """Enrichit et track les métriques avancées des trades."""

    def __init__(self, supa_logger=None):
        self.supa = supa_logger
        self._active_trades = {}  # trade_id -> trade_data

    # ================================================================
    # CALCULS
    # ================================================================

    @staticmethod
    def calculate_risk_reward(entry_price: float, sl: float, tp_final: float) -> float:
        """Calcule le ratio Risk/Reward."""
        risk = abs(entry_price - sl)
        reward = abs(tp_final - entry_price)
        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)

    @staticmethod
    def detect_signal_type(signal: dict) -> str:
        """Détecte le type de signal : CAS1, CAS2, PU."""
        is_single = signal.get("is_single_price", False)
        if is_single:
            return "PU"
        
        # CAS 1 ou 2 dépend de si le prix est dans la zone
        # (sera déterminé au moment de l'exécution)
        return "UNKNOWN"  # Sera mis à jour lors de l'exécution

    @staticmethod
    def calculate_position_drawdown(entry_price: float, current_price: float, 
                                     action: str, sl: float) -> float:
        """Calcule le drawdown actuel d'une position en %."""
        if action == "BUY":
            if current_price >= entry_price:
                return 0.0
            drawdown = (entry_price - current_price) / entry_price * 100
        else:  # SELL
            if current_price <= entry_price:
                return 0.0
            drawdown = (current_price - entry_price) / entry_price * 100
        return round(drawdown, 2)

    @staticmethod
    def calculate_max_drawdown(prices: List[float], action: str) -> float:
        """Calcule le drawdown max à partir d'une série de prix."""
        if not prices:
            return 0.0
        
        max_dd = 0.0
        peak = prices[0]
        
        for price in prices:
            if action == "BUY":
                if price > peak:
                    peak = price
                dd = (peak - price) / peak * 100 if peak > 0 else 0
            else:
                if price < peak:
                    peak = price
                dd = (price - peak) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return round(max_dd, 2)

    @staticmethod
    def calculate_r_multiple(entry_price: float, sl: float, pnl: float, 
                              lot_size: float) -> float:
        """Calcule le R-multiple (gain/perte en unités de risque)."""
        risk_per_unit = abs(entry_price - sl)
        if risk_per_unit <= 0:
            return 0.0
        # PnL en pips * lot_size * 100 (pour XAUUSD)
        risk_amount = risk_per_unit * lot_size * 100
        if risk_amount <= 0:
            return 0.0
        return round(pnl / risk_amount, 2)

    # ================================================================
    # TRACKING ACTIF
    # ================================================================

    def track_open(self, trade_id: str, signal: dict, entry_price: float,
                   lot_size: float, sl: float, tp_final: float,
                   signal_type: str = "UNKNOWN") -> dict:
        """Enrichit et stocke les données d'un trade ouvert."""
        
        rr = self.calculate_risk_reward(entry_price, sl, tp_final)
        
        trade_data = {
            "trade_id": trade_id,
            "signal_type": signal_type,
            "risk_reward": rr,
            "entry_price": entry_price,
            "sl": sl,
            "tp_final": tp_final,
            "lot_size": lot_size,
            "max_drawdown": 0.0,
            "tp_hit": None,
            "opened_at": datetime.now(timezone.utc),
            "prices_seen": [entry_price],
        }
        
        self._active_trades[trade_id] = trade_data
        
        log.info(f"[TRACK] Trade {trade_id[:8]} | Type={signal_type} | R:R={rr}")
        
        return trade_data

    def track_price_update(self, trade_id: str, current_price: float, action: str):
        """Met à jour le prix et le drawdown d'un trade actif."""
        if trade_id not in self._active_trades:
            return
        
        td = self._active_trades[trade_id]
        td["prices_seen"].append(current_price)
        
        # Calculer drawdown actuel
        dd = self.calculate_position_drawdown(
            td["entry_price"], current_price, action, td["sl"]
        )
        td["max_drawdown"] = max(td["max_drawdown"], dd)

    def track_tp_hit(self, trade_id: str, tp_name: str):
        """Enregistre quel TP a été atteint."""
        if trade_id not in self._active_trades:
            return
        
        self._active_trades[trade_id]["tp_hit"] = tp_name
        log.info(f"[TRACK] Trade {trade_id[:8]} | TP atteint: {tp_name}")

    def track_close(self, trade_id: str, pnl: float, result: str) -> Optional[dict]:
        """Finalise le tracking d'un trade fermé."""
        if trade_id not in self._active_trades:
            return None
        
        td = self._active_trades[trade_id]
        
        # Calculer R-multiple
        r_mult = self.calculate_r_multiple(
            td["entry_price"], td["sl"], pnl, td["lot_size"]
        )
        
        # Calculer drawdown max final
        max_dd = self.calculate_max_drawdown(td["prices_seen"], "BUY")  # Simplifié
        
        duration = (datetime.now(timezone.utc) - td["opened_at"]).total_seconds() / 60
        
        final_data = {
            "trade_id": trade_id,
            "signal_type": td["signal_type"],
            "risk_reward": td["risk_reward"],
            "r_multiple": r_mult,
            "max_drawdown": max_dd,
            "tp_hit": td["tp_hit"],
            "duration_min": round(duration, 1),
            "pnl": pnl,
            "result": result,
        }
        
        # Nettoyer
        del self._active_trades[trade_id]
        
        log.info(f"[TRACK] Trade {trade_id[:8]} fermé | R={r_mult} | DD={max_dd}% | {result}")
        
        return final_data

    # ================================================================
    # ENRICHISSEMENT SUPABASE
    # ================================================================

    def enrich_trade_data(self, signal: dict, entry_price: float,
                          lot_size: float, sl: float, tp_final: float,
                          cas_num: int = 0) -> dict:
        """Retourne les données enrichies pour Supabase."""
        
        signal_type = "PU" if signal.get("is_single_price") else f"CAS{cas_num}"
        rr = self.calculate_risk_reward(entry_price, sl, tp_final)
        
        return {
            "signal_type": signal_type,
            "risk_reward": rr,
        }

    def update_trade_tracking(self, trade_id: str, extra_data: dict):
        """Met à jour un trade dans Supabase avec les données de tracking."""
        if not self.supa or not self.supa.client:
            return
        
        try:
            self.supa._retry_call(
                lambda: self.supa.table("trades").update(extra_data)
                .eq("id", trade_id).execute()
            )
            log.debug(f"[TRACK] Trade {trade_id[:8]} mis à jour dans Supabase")
        except Exception as e:
            log.warning(f"[TRACK] Erreur mise à jour trade: {e}")

    def log_tracking_event(self, trade_id: str, event_type: str, details: dict):
        """Log un événement de tracking dans Supabase."""
        if not self.supa:
            return
        
        self.supa.log_event(trade_id, event_type, details)


# ================================================================
# INSTANCE GLOBALE
# ================================================================
_tracker_instance = None


def get_tracker(supa_logger=None) -> TradeTracker:
    """Retourne l'instance singleton du tracker."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = TradeTracker(supa_logger)
    return _tracker_instance
