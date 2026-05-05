"""
Supabase Logger — Envoie les données de trading à Supabase
Version 4.1 — Async-safe, retry backoff, timezone-aware
"""
import os
import logging
import time
from datetime import datetime, timezone
from supabase import create_client, Client

log = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "")


class SupabaseLogger:
    """Logger Supabase avec retry exponentiel et gestion d'erreurs robuste."""

    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # secondes

    def __init__(self):
        self.client: Client | None = None
        self.session_id: str | None = None

    def _retry_call(self, func, *args, **kwargs):
        """Exécute une fonction avec retry exponentiel."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BASE_DELAY * (2 ** attempt)
                    log.warning(f"[SUPA] Tentative {attempt + 1}/{self.MAX_RETRIES} échouée: {e}. Retry dans {delay}s...")
                    time.sleep(delay)
                else:
                    log.error(f"[SUPA] Échec définitif après {self.MAX_RETRIES} tentatives: {e}")
                    raise

    def connect(self) -> bool:
        if not SUPABASE_URL or not SUPABASE_KEY:
            log.warning("[SUPA] Variables manquantes — pas de log distant")
            return False
        try:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
            log.info("[SUPA] Connecté à Supabase")
            return True
        except Exception as e:
            log.error(f"[SUPA] Connexion échouée: {e}")
            return False

    def start_session(self, runtime_minutes: int, channels: list,
                      lot_size: float, mode: str) -> str | None:
        if not self.client:
            return None
        try:
            result = self._retry_call(
                lambda: self.client.table("sessions").insert({
                    "runtime_minutes": runtime_minutes,
                    "channels": channels,
                    "lot_size": lot_size,
                    "mode": mode,
                    "status": "running",
                }).execute()
            )
            self.session_id = result.data[0]["id"]
            log.info(f"[SUPA] Session créée: {self.session_id}")
            return self.session_id
        except Exception as e:
            log.error(f"[SUPA] Erreur start_session: {e}")
            return None

    def end_session(self, total_trades: int, total_pnl: float):
        if not self.client or not self.session_id:
            return
        try:
            self._retry_call(
                lambda: self.client.table("sessions").update({
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                    "total_trades": total_trades,
                    "total_pnl": round(total_pnl, 2),
                    "status": "stopped",
                }).eq("id", self.session_id).execute()
            )
            log.info("[SUPA] Session terminée")
        except Exception as e:
            log.error(f"[SUPA] Erreur end_session: {e}")

    def log_trade_open(self, signal: dict, entry_price: float,
                       lot_size: float, tickets: list) -> str | None:
        if not self.client or not self.session_id:
            return None
        try:
            tps = signal.get("tps", [])
            result = self._retry_call(
                lambda: self.client.table("trades").insert({
                    "session_id": self.session_id,
                    "canal": signal.get("source_channel", "Inconnu"),
                    "symbol": signal["symbol"],
                    "action": signal["action"],
                    "zone_low": signal["zone_low"],
                    "zone_high": signal["zone_high"],
                    "sl": signal["sl"],
                    "tps": tps,
                    "tp_count": len(tps),
                    "tp_final": tps[-1] if tps else 0,
                    "entry_price": entry_price,
                    "lot_size": lot_size,
                    "result": "OPEN",
                    "pnl": 0,
                    "tickets": tickets,
                }).execute()
            )
            trade_id = result.data[0]["id"]
            log.info(f"[SUPA] Trade ouvert: {trade_id}")

            self._retry_call(
                lambda: self.client.table("events").insert({
                    "trade_id": trade_id,
                    "session_id": self.session_id,
                    "event_type": "OPEN",
                    "details": {
                        "symbol": signal["symbol"],
                        "action": signal["action"],
                        "canal": signal.get("source_channel", "Inconnu"),
                        "entry_price": entry_price,
                    },
                }).execute()
            )

            return trade_id
        except Exception as e:
            log.error(f"[SUPA] Erreur log_trade_open: {e}")
            return None

    def log_trade_close(self, trade_id: str, result: str,
                        pnl: float, duree_min: float):
        if not self.client:
            return
        try:
            self._retry_call(
                lambda: self.client.table("trades").update({
                    "closed_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                    "pnl": round(pnl, 2),
                    "duree_min": round(duree_min, 1),
                }).eq("id", trade_id).execute()
            )

            self._retry_call(
                lambda: self.client.table("events").insert({
                    "trade_id": trade_id,
                    "session_id": self.session_id,
                    "event_type": "CLOSE",
                    "details": {
                        "result": result,
                        "pnl": round(pnl, 2),
                        "duree_min": round(duree_min, 1),
                    },
                }).execute()
            )
            log.info(f"[SUPA] Trade fermé: {trade_id} -> {result} {pnl:+.2f}")
        except Exception as e:
            log.error(f"[SUPA] Erreur log_trade_close: {e}")

    def log_event(self, trade_id: str, event_type: str, details: dict):
        if not self.client or not self.session_id:
            return
        try:
            self._retry_call(
                lambda: self.client.table("events").insert({
                    "trade_id": trade_id,
                    "session_id": self.session_id,
                    "event_type": event_type,
                    "details": details,
                }).execute()
            )
        except Exception as e:
            log.error(f"[SUPA] Erreur log_event: {e}")

    def log_tp_hit(self, trade_id: str, tp_name: str,
                   tp_value: float, pnl: float):
        self.log_event(trade_id, "TP_HIT", {
            "tp_name": tp_name,
            "tp_value": tp_value,
            "pnl": round(pnl, 2),
        })

    def log_sl_hit(self, trade_id: str, pnl: float):
        self.log_event(trade_id, "SL_HIT", {
            "pnl": round(pnl, 2),
        })

    def log_sl_move(self, trade_id: str, new_sl: float):
        self.log_event(trade_id, "SL_MOVE", {
            "new_sl": new_sl,
        })
