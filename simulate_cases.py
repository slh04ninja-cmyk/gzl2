#!/usr/bin/env python3
"""
Simulation complète de tous les cas de trading — VERSION CORRIGÉE
Règle : quand TP3 atteint et qu'il y a une position ouverte + un ordre pending,
        l'ordre pending s'annule et la position ouverte continue avec BE + trailing.
"""

import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, List

# ============================================================
# CONFIGURATION SIMULATION
# ============================================================
TRAIL_POINTS = 200       # 2$ pour XAUUSD
POINT_VALUE = 0.01       # 1 point XAUUSD = 0.01$
TRAIL_STEP = TRAIL_POINTS * POINT_VALUE   # 2$
TRIGGER_STEP = TRAIL_STEP * 2              # 4$
TP_TRIGGER = 3

# ============================================================
# MOCK MT5
# ============================================================
@dataclass
class MockPosition:
    ticket: int
    symbol: str
    type: str  # "BUY" or "SELL"
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float = 0.0

@dataclass
class MockOrder:
    ticket: int
    symbol: str
    type: str
    volume: float
    price: float
    sl: float
    tp: float

class MockMT5:
    def __init__(self):
        self.positions = {}
        self.orders = {}
        self.next_ticket = 1000
        self.current_price = 0.0
        self.action = "BUY"
        self.log = []
    
    def place_market(self, symbol, action, lot, sl, tp, comment=""):
        ticket = self.next_ticket
        self.next_ticket += 1
        pos = MockPosition(ticket, symbol, action, lot, self.current_price, sl, tp)
        self.positions[ticket] = pos
        self.log.append(f"  📈 MARKET {action} #{ticket} @{self.current_price} SL={sl} TP={tp} [{comment}]")
        return ticket
    
    def place_limit(self, symbol, action, lot, price, sl, tp, comment=""):
        ticket = self.next_ticket
        self.next_ticket += 1
        order = MockOrder(ticket, symbol, action, lot, price, sl, tp)
        self.orders[ticket] = order
        self.log.append(f"  📋 LIMIT {action} #{ticket} @{price} SL={sl} TP={tp} [{comment}]")
        return ticket
    
    def close_position(self, ticket, comment=""):
        if ticket in self.positions:
            pos = self.positions[ticket]
            pnl = (self.current_price - pos.price_open) * pos.volume * 100
            if pos.type == "SELL":
                pnl = -pnl
            self.log.append(f"  🔴 CLOSE #{ticket} @{self.current_price} P&L={pnl:+.2f} [{comment}]")
            del self.positions[ticket]
            return pnl
        return 0
    
    def cancel_order(self, ticket, comment=""):
        if ticket in self.orders:
            self.log.append(f"  ❌ CANCEL LIMIT #{ticket} [{comment}]")
            del self.orders[ticket]
            return True
        return False
    
    def modify_sl(self, ticket, new_sl, comment=""):
        if ticket in self.positions:
            old_sl = self.positions[ticket].sl
            self.positions[ticket].sl = new_sl
            self.log.append(f"  🔄 SL #{ticket}: {old_sl} → {new_sl} [{comment}]")
            return True
        return False
    
    def check_limits_filled(self):
        """Vérifie si des ordres LIMIT sont remplis au prix actuel."""
        filled = []
        for ticket, order in list(self.orders.items()):
            if order.type == "BUY" and self.current_price <= order.price:
                pos = MockPosition(ticket, order.symbol, "BUY", order.volume, 
                                   order.price, order.sl, order.tp)
                self.positions[ticket] = pos
                filled.append(ticket)
                self.log.append(f"  ✅ LIMIT #{ticket} REMPLIE @{order.price}")
                del self.orders[ticket]
            elif order.type == "SELL" and self.current_price >= order.price:
                pos = MockPosition(ticket, order.symbol, "SELL", order.volume,
                                   order.price, order.sl, order.tp)
                self.positions[ticket] = pos
                filled.append(ticket)
                self.log.append(f"  ✅ LIMIT #{ticket} REMPLIE @{order.price}")
                del self.orders[ticket]
        return filled
    
    def get_position(self, ticket):
        return self.positions.get(ticket)
    
    def set_price(self, price):
        self.current_price = price


# ============================================================
# SIMULATION TRAILING
# ============================================================
def simulate_trailing(mt5, ticket, entry_price, tp3_level, action, prices):
    """Simule le trailing SL sur une série de prix."""
    pos = mt5.get_position(ticket)
    if not pos:
        return
    
    trail_last_price = mt5.current_price
    trail_active = True
    
    mt5.log.append(f"\n  📍 Trailing activé sur #{ticket} (BE @{entry_price})")
    mt5.log.append(f"     trail_step={TRAIL_STEP}$ trigger_step={TRIGGER_STEP}$")
    
    for price in prices:
        mt5.set_price(price)
        
        if action == "BUY":
            price_moved = price - trail_last_price
            if price_moved >= TRIGGER_STEP:
                new_sl = pos.sl + TRAIL_STEP if pos.sl > 0 else price - TRAIL_STEP
                mt5.modify_sl(ticket, round(new_sl, 2), f"Trail BUY @{price}")
                trail_last_price = price
        else:
            price_moved = trail_last_price - price
            if price_moved >= TRIGGER_STEP:
                new_sl = pos.sl - TRAIL_STEP if pos.sl > 0 else price + TRAIL_STEP
                mt5.modify_sl(ticket, round(new_sl, 2), f"Trail SELL @{price}")
                trail_last_price = price
        
        pos = mt5.get_position(ticket)
        if not pos:
            mt5.log.append(f"  💥 Position #{ticket} fermée (SL touché)")
            break


# ============================================================
# CAS 1 : Prix dans la zone (MARKET + LIMIT)
# ============================================================
def simulate_cas1(zone_low=3230, zone_high=3250, sl=3210, tps=[3260, 3270, 3280], 
                  tp_final=3280, scenario="2a"):
    """
    CAS 1: Prix dans la zone d'entrée
    - MARKET (50% lot) → TP = tp_final
    - LIMIT (50% lot) entre SL et zone → TP = tp_final
    - TP3 atteint :
      - 2-a : LIMIT non exécuté → annuler LIMIT, MARKET continue avec BE + trailing
      - 2-b : LIMIT exécuté → fermer MARKET, BE @ entrée MARKET + trailing sur LIMIT
    """
    mt5 = MockMT5()
    mt5.action = "BUY"
    entry_price = (zone_low + zone_high) / 2
    tp3 = tps[TP_TRIGGER - 1]  # TP3 = 3280
    
    mt5.log.append("=" * 60)
    mt5.log.append(f"📊 CAS 1 — Scénario {scenario}")
    mt5.log.append(f"   Zone: [{zone_low} — {entry_price} — {zone_high}]")
    mt5.log.append(f"   SL: {sl} | TPs: {tps} | TP3 trigger: {tp3}")
    mt5.log.append("=" * 60)
    
    # Étape 1 : Prix dans la zone → MARKET + LIMIT
    mt5.set_price(entry_price)
    mt5.log.append(f"\n🔹 ÉTAPE 1 : Prix dans la zone (@{entry_price})")
    
    # MARKET
    market_ticket = mt5.place_market("XAUUSD", "BUY", 0.01, sl, tp_final, "CAS1-market")
    
    # LIMIT entre SL et zone
    limit_price = round((sl + zone_low) / 2, 2)
    limit_ticket = mt5.place_limit("XAUUSD", "BUY", 0.01, limit_price, sl, tp_final, "CAS1-limit")
    
    # Étape 2 : Prix évolue vers TP3
    if scenario == "2a":
        # LIMIT pas encore remplie (prix trop haut)
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix monte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        # CORRECTION : Vérifier le niveau de prix TP3
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit} (prix={mt5.current_price} >= TP3={tp3})")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — CAS 2-a (limit non remplie)")
            # Annuler la LIMIT (pending)
            mt5.cancel_order(limit_ticket, "CAS1-limit-non-remplie")
            # MARKET continue avec BE + trailing (PAS fermé !)
            mt5.modify_sl(market_ticket, entry_price, "CAS1-BE-market-entry")
            mt5.log.append(f"   ✅ Résultat : LIMIT annulée, MARKET continue BE @{entry_price} + trailing")
            
            # Trailing sur le MARKET
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, market_ticket, entry_price, tp3, "BUY", trail_prices)
    
    elif scenario == "2b":
        # LIMIT remplie d'abord (prix baisse vers limit)
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT remplie @{limit_price}")
        mt5.set_price(limit_price)
        mt5.check_limits_filled()
        
        # Prix remonte vers TP3
        mt5.log.append(f"\n🔹 ÉTAPE 3 : Prix remonte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — CAS 2-b (limit remplie)")
            # Fermer le MARKET (les 2 sont ouvertes)
            mt5.close_position(market_ticket, "CAS1-TP3-close-market")
            # BE sur la LIMIT @ entrée MARKET
            mt5.modify_sl(limit_ticket, entry_price, "CAS1-BE-market-entry")
            mt5.log.append(f"   ✅ Résultat : MARKET fermé, LIMIT BE @{entry_price} + trailing")
            
            # Trailing sur la LIMIT
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, limit_ticket, entry_price, tp3, "BUY", trail_prices)
    
    return mt5.log


# ============================================================
# CAS 2-a : Prix entre zone et TP1 (MARKET + LIMIT)
# ============================================================
def simulate_cas2a(zone_low=3230, zone_high=3250, sl=3210, tps=[3260, 3270, 3280],
                   tp_final=3280, scenario="3a1"):
    """
    CAS 2-a: Prix entre zone et TP1
    - MARKET @ prix actuel (50% lot) → TP = tp_final
    - LIMIT @ l'autre limite de zone (50% lot) → TP = tp_final
    - TP3 atteint :
      - 3-a-1 : LIMIT non exécutée → annuler LIMIT, MARKET continue avec BE + trailing
      - 3-a-2 : LIMIT exécutée → fermer MARKET, BE @ entrée MARKET + trailing sur LIMIT
    """
    mt5 = MockMT5()
    mt5.action = "BUY"
    tp1 = tps[0]
    tp3 = tps[TP_TRIGGER - 1]
    current_price = zone_high + 2  # Prix entre zone et TP1
    
    mt5.log.append("=" * 60)
    mt5.log.append(f"📊 CAS 2-a — Scénario {scenario}")
    mt5.log.append(f"   Zone: [{zone_low} — {zone_high}] | TP1: {tp1}")
    mt5.log.append(f"   SL: {sl} | TPs: {tps} | TP3 trigger: {tp3}")
    mt5.log.append(f"   Prix actuel: {current_price} (entre zone et TP1)")
    mt5.log.append("=" * 60)
    
    # Étape 1 : Prix entre zone et TP1 → MARKET + LIMIT
    mt5.set_price(current_price)
    mt5.log.append(f"\n🔹 ÉTAPE 1 : Prix entre zone et TP1 (@{current_price})")
    
    market_ticket = mt5.place_market("XAUUSD", "BUY", 0.01, sl, tp_final, "C2a-market")
    
    # LIMIT à l'autre limite de zone (zone_low pour BUY)
    limit_price = zone_low
    limit_ticket = mt5.place_limit("XAUUSD", "BUY", 0.01, limit_price, sl, tp_final, "C2a-limit")
    
    if scenario == "3a1":
        # LIMIT pas remplie → prix monte direct vers TP3
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix monte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        # CORRECTION : Vérifier le niveau de prix TP3
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit} (prix={mt5.current_price} >= TP3={tp3})")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — CAS 3-a-1 (limit non remplie)")
            # Annuler la LIMIT (pending)
            mt5.cancel_order(limit_ticket, "C2a-limit-non-remplie")
            # MARKET continue avec BE + trailing (PAS fermé !)
            mt5.modify_sl(market_ticket, current_price, "C2a-BE-market-entry")
            mt5.log.append(f"   ✅ Résultat : LIMIT annulée, MARKET continue BE @{current_price} + trailing")
            
            # Trailing sur le MARKET
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, market_ticket, current_price, tp3, "BUY", trail_prices)
    
    elif scenario == "3a2":
        # LIMIT remplie d'abord
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT remplie @{limit_price}")
        mt5.set_price(limit_price)
        mt5.check_limits_filled()
        
        # Prix remonte vers TP3
        mt5.log.append(f"\n🔹 ÉTAPE 3 : Prix remonte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — CAS 3-a-2 (limit remplie)")
            # Fermer le MARKET (les 2 sont ouvertes)
            mt5.close_position(market_ticket, "C2a-TP3-close-market")
            # BE sur la LIMIT @ entrée MARKET
            mt5.modify_sl(limit_ticket, current_price, "C2a-BE-market-entry")
            mt5.log.append(f"   ✅ Résultat : MARKET fermé, LIMIT BE @{current_price} + trailing")
            
            # Trailing sur la LIMIT
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, limit_ticket, current_price, tp3, "BUY", trail_prices)
    
    return mt5.log


# ============================================================
# CAS 2-b : Prix loin de la zone (2 LIMIT)
# ============================================================
def simulate_cas2b(zone_low=3230, zone_high=3250, sl=3210, tps=[3260, 3270, 3280],
                   tp_final=3280, scenario="3b1"):
    """
    CAS 2-b: Prix loin de la zone (au-delà de TP1)
    - LIMIT_1 (50% lot) au bord de zone → TP = tp_final
    - LIMIT_2 (50% lot) côté opposé → TP = tp_final
    - TP3 atteint :
      - 3-b-1 : aucun rempli → annuler les 2
      - 3-b-2 : LIMIT_1 remplie, LIMIT_2 non → annuler LIMIT_2, LIMIT_1 continue avec BE + trailing
      - 3-b-3 : les 2 remplies → fermer LIMIT_1, BE @ entrée LIMIT_1 + trailing sur LIMIT_2
    """
    mt5 = MockMT5()
    mt5.action = "BUY"
    tp1 = tps[0]
    tp3 = tps[TP_TRIGGER - 1]
    current_price = tp1 + 10  # Prix loin de la zone
    
    mt5.log.append("=" * 60)
    mt5.log.append(f"📊 CAS 2-b — Scénario {scenario}")
    mt5.log.append(f"   Zone: [{zone_low} — {zone_high}] | TP1: {tp1}")
    mt5.log.append(f"   SL: {sl} | TPs: {tps} | TP3 trigger: {tp3}")
    mt5.log.append(f"   Prix actuel: {current_price} (loin de la zone)")
    mt5.log.append("=" * 60)
    
    # Étape 1 : Prix loin → 2 LIMIT
    mt5.set_price(current_price)
    mt5.log.append(f"\n🔹 ÉTAPE 1 : Prix loin de la zone → 2 LIMIT")
    
    # LIMIT_1 au bord de zone (plus proche du prix)
    price_1 = zone_high
    limit1_ticket = mt5.place_limit("XAUUSD", "BUY", 0.01, price_1, sl, tp_final, "C2b-L1")
    
    # LIMIT_2 côté opposé
    price_2 = zone_low
    limit2_ticket = mt5.place_limit("XAUUSD", "BUY", 0.01, price_2, sl, tp_final, "C2b-L2")
    
    if scenario == "3b1":
        # Aucun rempli → prix monte vers TP3
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix monte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — CAS 3-b-1 (aucun rempli)")
            mt5.cancel_order(limit1_ticket, "C2b-L1-non-remplie")
            mt5.cancel_order(limit2_ticket, "C2b-L2-non-remplie")
            mt5.log.append(f"   ✅ Résultat : Les 2 LIMIT annulées")
    
    elif scenario == "3b2":
        # LIMIT_1 remplie seulement
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT_1 remplie @{price_1}")
        mt5.set_price(price_1)
        mt5.check_limits_filled()
        
        # Prix remonte vers TP3
        mt5.log.append(f"\n🔹 ÉTAPE 3 : Prix remonte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — CAS 3-b-2 (L1 remplie, L2 pending)")
            # Annuler LIMIT_2 (pending)
            mt5.cancel_order(limit2_ticket, "C2b-L2-non-remplie")
            # LIMIT_1 continue avec BE + trailing (PAS fermé !)
            mt5.modify_sl(limit1_ticket, price_1, "C2b-BE-L1-entry")
            mt5.log.append(f"   ✅ Résultat : L2 annulée, L1 continue BE @{price_1} + trailing")
            
            # Trailing sur L1
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, limit1_ticket, price_1, tp3, "BUY", trail_prices)
    
    elif scenario == "3b3":
        # Les 2 remplies
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT_1 remplie @{price_1}")
        mt5.set_price(price_1)
        mt5.check_limits_filled()
        
        mt5.log.append(f"\n🔹 ÉTAPE 3 : Prix continue de baisser → LIMIT_2 remplie @{price_2}")
        mt5.set_price(price_2)
        mt5.check_limits_filled()
        
        # Prix remonte vers TP3
        mt5.log.append(f"\n🔹 ÉTAPE 4 : Prix remonte vers TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 5 : TP3 atteint — CAS 3-b-3 (les 2 remplies)")
            # Fermer L1
            mt5.close_position(limit1_ticket, "C2b-close-L1")
            # BE sur L2 @ entrée L1
            mt5.modify_sl(limit2_ticket, price_1, "C2b-BE-L2-at-L1-entry")
            mt5.log.append(f"   ✅ Résultat : L1 fermée, L2 BE @{price_1} + trailing")
            
            # Trailing sur L2
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, limit2_ticket, price_1, tp3, "BUY", trail_prices)
    
    return mt5.log


# ============================================================
# PRIX UNIQUE S1/S2
# ============================================================
def simulate_prix_unique(entry=3240, sl=3210, tps=[3260, 3270, 3280],
                         tp_final=3280, scenario="S1"):
    """
    Prix unique (pas de zone)
    - S1 : prix entre entry et TP1 → MARKET @ prix actuel
    - S2 : prix entre TP1 et TP2 → LIMIT @ entry
    """
    mt5 = MockMT5()
    mt5.action = "BUY"
    tp1 = tps[0]
    tp2 = tps[1] if len(tps) > 1 else tps[0]
    tp3 = tps[TP_TRIGGER - 1]
    
    mt5.log.append("=" * 60)
    mt5.log.append(f"📊 PRIX UNIQUE — Scénario {scenario}")
    mt5.log.append(f"   Entry: {entry} | SL: {sl}")
    mt5.log.append(f"   TPs: {tps} | TP3 trigger: {tp3}")
    mt5.log.append("=" * 60)
    
    if scenario == "S1":
        # Prix entre entry et TP1 → MARKET
        current_price = entry + 2
        mt5.set_price(current_price)
        mt5.log.append(f"\n🔹 ÉTAPE 1 : Prix entre entry et TP1 (@{current_price})")
        
        market_ticket = mt5.place_market("XAUUSD", "BUY", 0.01, sl, tp_final, "PU-S1-market")
        
        # Prix atteint TP3
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix atteint TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — PU S1")
            # MARKET continue avec BE + trailing
            mt5.modify_sl(market_ticket, entry, "PU-S1-BE")
            mt5.log.append(f"   ✅ Résultat : MARKET BE @{entry} + trailing")
            
            # Trailing
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, market_ticket, entry, tp3, "BUY", trail_prices)
    
    elif scenario == "S2":
        # Prix entre TP1 et TP2 → LIMIT
        current_price = tp1 + 2
        mt5.set_price(current_price)
        mt5.log.append(f"\n🔹 ÉTAPE 1 : Prix entre TP1 et TP2 (@{current_price})")
        
        limit_ticket = mt5.place_limit("XAUUSD", "BUY", 0.01, entry, sl, tp_final, "PU-S2-limit")
        
        # LIMIT remplie
        mt5.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT remplie @{entry}")
        mt5.set_price(entry)
        mt5.check_limits_filled()
        
        # Prix atteint TP3
        mt5.log.append(f"\n🔹 ÉTAPE 3 : Prix atteint TP3 (@{tp3})")
        mt5.set_price(tp3)
        
        tp3_hit = (mt5.action == "BUY" and mt5.current_price >= tp3)
        mt5.log.append(f"   TP3 hit: {tp3_hit}")
        
        if tp3_hit:
            mt5.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — PU S2")
            mt5.modify_sl(limit_ticket, entry, "PU-S2-BE")
            mt5.log.append(f"   ✅ Résultat : BE @{entry} + trailing activé")
            
            # Trailing
            trail_prices = [tp3 + 2, tp3 + 4, tp3 + 8, tp3 + 12]
            simulate_trailing(mt5, limit_ticket, entry, tp3, "BUY", trail_prices)
    
    return mt5.log


# ============================================================
# EXÉCUTION DE TOUTES LES SIMULATIONS
# ============================================================
def main():
    print("\n" + "🚀" * 30)
    print("   SIMULATION COMPLÈTE — VERSION CORRIGÉE")
    print("   Règle : position ouverte + pending → annuler pending, position continue BE + trailing")
    print("🚀" * 30 + "\n")
    
    all_logs = []
    
    # CAS 1 : Scénario 2-a (limit non remplie → annuler limit, MARKET continue)
    all_logs.append(simulate_cas1(scenario="2a"))
    all_logs.append([""])
    
    # CAS 1 : Scénario 2-b (limit remplie → fermer MARKET, LIMIT continue)
    all_logs.append(simulate_cas1(scenario="2b"))
    all_logs.append([""])
    
    # CAS 2-a : Scénario 3-a-1 (limit non remplie → annuler limit, MARKET continue)
    all_logs.append(simulate_cas2a(scenario="3a1"))
    all_logs.append([""])
    
    # CAS 2-a : Scénario 3-a-2 (limit remplie → fermer MARKET, LIMIT continue)
    all_logs.append(simulate_cas2a(scenario="3a2"))
    all_logs.append([""])
    
    # CAS 2-b : Scénario 3-b-1 (aucun rempli → annuler les 2)
    all_logs.append(simulate_cas2b(scenario="3b1"))
    all_logs.append([""])
    
    # CAS 2-b : Scénario 3-b-2 (L1 remplie, L2 pending → annuler L2, L1 continue)
    all_logs.append(simulate_cas2b(scenario="3b2"))
    all_logs.append([""])
    
    # CAS 2-b : Scénario 3-b-3 (les 2 remplies → fermer L1, L2 continue)
    all_logs.append(simulate_cas2b(scenario="3b3"))
    all_logs.append([""])
    
    # Prix Unique S1 (market seul → BE + trailing)
    all_logs.append(simulate_prix_unique(scenario="S1"))
    all_logs.append([""])
    
    # Prix Unique S2 (limit seule → BE + trailing)
    all_logs.append(simulate_prix_unique(scenario="S2"))
    
    # Afficher tous les logs
    for log_group in all_logs:
        for line in log_group:
            print(line)
    
    print("\n" + "=" * 60)
    print("✅ SIMULATION TERMINÉE")
    print("=" * 60)


if __name__ == "__main__":
    main()
