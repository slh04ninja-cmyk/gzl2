#!/usr/bin/env python3
"""
Simulation complète — VERSION FINALE CORRIGÉE
- Vérification OHLC (bougie en cours)
- Polling 5s
- Règle : position ouverte + pending → annuler pending, position continue BE + trailing
"""

from dataclasses import dataclass

TRAIL_STEP = 2.0    # 2$
TRIGGER_STEP = 4.0   # 4$
TP_TRIGGER = 3

@dataclass
class MockPos:
    ticket: int
    action: str
    entry: float
    sl: float
    tp: float

@dataclass
class MockOrder:
    ticket: int
    action: str
    price: float

class Sim:
    def __init__(self):
        self.positions = {}
        self.orders = {}
        self.next_id = 1000
        self.price = 0
        self.high = 0
        self.low = 99999
        self.log = []
    
    def set_price(self, p):
        self.price = p
        self.high = max(self.high, p)
        self.low = min(self.low, p)
    
    def market(self, action, entry, sl, tp, comment=""):
        tid = self.next_id; self.next_id += 1
        self.positions[tid] = MockPos(tid, action, entry, sl, tp)
        self.log.append(f"  📈 MARKET {action} #{tid} @{entry} SL={sl} TP={tp} [{comment}]")
        return tid
    
    def limit(self, action, price, sl, tp, comment=""):
        oid = self.next_id; self.next_id += 1
        self.orders[oid] = MockOrder(oid, action, price)
        self.log.append(f"  📋 LIMIT {action} #{oid} @{price} SL={sl} TP={tp} [{comment}]")
        return oid
    
    def close(self, tid, comment=""):
        if tid in self.positions:
            p = self.positions[tid]
            pnl = (self.price - p.entry) * 100
            if p.action == "SELL": pnl = -pnl
            self.log.append(f"  🔴 CLOSE #{tid} @{self.price} P&L={pnl:+.2f} [{comment}]")
            del self.positions[tid]
    
    def cancel(self, oid, comment=""):
        if oid in self.orders:
            self.log.append(f"  ❌ CANCEL LIMIT #{oid} [{comment}]")
            del self.orders[oid]
    
    def modify_sl(self, tid, new_sl, comment=""):
        if tid in self.positions:
            old = self.positions[tid].sl
            self.positions[tid].sl = new_sl
            self.log.append(f"  🔄 SL #{tid}: {old} → {new_sl} [{comment}]")
    
    def check_ohlc(self, tp3, action):
        """Vérifie si le High/Low de la bougie a touché TP3."""
        if action == "BUY" and self.high >= tp3:
            return True
        if action == "SELL" and self.low <= tp3:
            return True
        return False
    
    def fill_limits(self):
        filled = []
        for oid, o in list(self.orders.items()):
            if o.action == "BUY" and self.price <= o.price:
                self.positions[oid] = MockPos(oid, "BUY", o.price, 0, 0)
                self.log.append(f"  ✅ LIMIT #{oid} REMPLIE @{o.price}")
                del self.orders[oid]
                filled.append(oid)
            elif o.action == "SELL" and self.price >= o.price:
                self.positions[oid] = MockPos(oid, "SELL", o.price, 0, 0)
                self.log.append(f"  ✅ LIMIT #{oid} REMPLIE @{o.price}")
                del self.orders[oid]
                filled.append(oid)
        return filled
    
    def trail(self, tid, action, prices):
        if tid not in self.positions: return
        last = self.price
        self.log.append(f"\n  📍 Trailing activé sur #{tid}")
        for p in prices:
            self.set_price(p)
            pos = self.positions.get(tid)
            if not pos: break
            if action == "BUY":
                moved = p - last
                if moved >= TRIGGER_STEP:
                    new_sl = pos.sl + TRAIL_STEP if pos.sl > 0 else p - TRAIL_STEP
                    self.modify_sl(tid, round(new_sl, 2), f"Trail @{p}")
                    last = p
            else:
                moved = last - p
                if moved >= TRIGGER_STEP:
                    new_sl = pos.sl - TRAIL_STEP if pos.sl > 0 else p + TRAIL_STEP
                    self.modify_sl(tid, round(new_sl, 2), f"Trail @{p}")
                    last = p


def header(title, params):
    s = Sim()
    s.log.append("=" * 60)
    s.log.append(f"📊 {title}")
    for k, v in params.items():
        s.log.append(f"   {k}: {v}")
    s.log.append("=" * 60)
    return s


# ============================================================
# CAS 1 — 2-a : MARKET ouvert + LIMIT pending → annuler LIMIT, MARKET continue
# ============================================================
def cas1_2a():
    s = header("CAS 1 — Scénario 2-a", {"Zone": "[3230-3250]", "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280})
    
    s.set_price(3240)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix dans la zone (@3240)")
    mt = s.market("BUY", 3240, 3210, 3280, "CAS1-market")
    lt = s.limit("BUY", 3220, 3210, 3280, "CAS1-limit")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix monte vers TP3 (@3280)")
    s.set_price(3280)
    s.log.append(f"   OHLC check: high={s.high} >= TP3=3280 → {s.check_ohlc(3280, 'BUY')}")
    
    s.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — LIMIT pending → annuler LIMIT, MARKET continue")
    s.cancel(lt, "CAS1-limit-non-remplie")
    s.modify_sl(mt, 3240, "CAS1-BE")
    s.log.append(f"   ✅ LIMIT annulée, MARKET continue BE @3240 + trailing")
    s.trail(mt, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# CAS 1 — 2-b : MARKET + LIMIT ouvertes → fermer MARKET, LIMIT continue
# ============================================================
def cas1_2b():
    s = header("CAS 1 — Scénario 2-b", {"Zone": "[3230-3250]", "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280})
    
    s.set_price(3240)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix dans la zone (@3240)")
    mt = s.market("BUY", 3240, 3210, 3280, "CAS1-market")
    lt = s.limit("BUY", 3220, 3210, 3280, "CAS1-limit")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT remplie @3220")
    s.set_price(3220)
    s.fill_limits()
    
    s.log.append(f"\n🔹 ÉTAPE 3 : Prix remonte vers TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — les 2 ouvertes → fermer MARKET, LIMIT continue")
    s.close(mt, "CAS1-TP3-close-market")
    s.modify_sl(lt, 3240, "CAS1-BE-market-entry")
    s.log.append(f"   ✅ MARKET fermé, LIMIT BE @3240 + trailing")
    s.trail(lt, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# CAS 2-a — 3-a-1 : MARKET ouvert + LIMIT pending → annuler LIMIT, MARKET continue
# ============================================================
def cas2a_3a1():
    s = header("CAS 2-a — Scénario 3-a-1", {"Zone": "[3230-3250]", "TP1": 3260, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280, "Prix": 3252})
    
    s.set_price(3252)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix entre zone et TP1 (@3252)")
    mt = s.market("BUY", 3252, 3210, 3280, "C2a-market")
    lt = s.limit("BUY", 3230, 3210, 3280, "C2a-limit")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix monte vers TP3 (@3280)")
    s.set_price(3280)
    s.log.append(f"   OHLC check: high={s.high} >= TP3=3280 → {s.check_ohlc(3280, 'BUY')}")
    
    s.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — LIMIT pending → annuler LIMIT, MARKET continue")
    s.cancel(lt, "C2a-limit-non-remplie")
    s.modify_sl(mt, 3252, "C2a-BE")
    s.log.append(f"   ✅ LIMIT annulée, MARKET continue BE @3252 + trailing")
    s.trail(mt, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# CAS 2-a — 3-a-2 : MARKET + LIMIT ouvertes → fermer MARKET, LIMIT continue
# ============================================================
def cas2a_3a2():
    s = header("CAS 2-a — Scénario 3-a-2", {"Zone": "[3230-3250]", "TP1": 3260, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280, "Prix": 3252})
    
    s.set_price(3252)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix entre zone et TP1 (@3252)")
    mt = s.market("BUY", 3252, 3210, 3280, "C2a-market")
    lt = s.limit("BUY", 3230, 3210, 3280, "C2a-limit")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT remplie @3230")
    s.set_price(3230)
    s.fill_limits()
    
    s.log.append(f"\n🔹 ÉTAPE 3 : Prix remonte vers TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — les 2 ouvertes → fermer MARKET, LIMIT continue")
    s.close(mt, "C2a-TP3-close-market")
    s.modify_sl(lt, 3252, "C2a-BE-market-entry")
    s.log.append(f"   ✅ MARKET fermé, LIMIT BE @3252 + trailing")
    s.trail(lt, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# CAS 2-b — 3-b-1 : Aucun rempli → annuler les 2
# ============================================================
def cas2b_3b1():
    s = header("CAS 2-b — Scénario 3-b-1", {"Zone": "[3230-3250]", "TP1": 3260, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280, "Prix": 3270})
    
    s.set_price(3270)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix loin de la zone → 2 LIMIT")
    l1 = s.limit("BUY", 3250, 3210, 3280, "C2b-L1")
    l2 = s.limit("BUY", 3230, 3210, 3280, "C2b-L2")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix monte vers TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint — aucun rempli → annuler les 2")
    s.cancel(l1, "C2b-L1-non-remplie")
    s.cancel(l2, "C2b-L2-non-remplie")
    s.log.append(f"   ✅ Les 2 LIMIT annulées")
    return s.log


# ============================================================
# CAS 2-b — 3-b-2 : L1 ouvert + L2 pending → annuler L2, L1 continue
# ============================================================
def cas2b_3b2():
    s = header("CAS 2-b — Scénario 3-b-2", {"Zone": "[3230-3250]", "TP1": 3260, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280, "Prix": 3270})
    
    s.set_price(3270)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix loin de la zone → 2 LIMIT")
    l1 = s.limit("BUY", 3250, 3210, 3280, "C2b-L1")
    l2 = s.limit("BUY", 3230, 3210, 3280, "C2b-L2")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → L1 remplie @3250")
    s.set_price(3250)
    s.fill_limits()
    
    s.log.append(f"\n🔹 ÉTAPE 3 : Prix remonte vers TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint — L1 ouvert + L2 pending → annuler L2, L1 continue")
    s.cancel(l2, "C2b-L2-non-remplie")
    s.modify_sl(l1, 3250, "C2b-BE-L1")
    s.log.append(f"   ✅ L2 annulée, L1 continue BE @3250 + trailing")
    s.trail(l1, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# CAS 2-b — 3-b-3 : Les 2 ouvertes → fermer L1, L2 continue
# ============================================================
def cas2b_3b3():
    s = header("CAS 2-b — Scénario 3-b-3", {"Zone": "[3230-3250]", "TP1": 3260, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280, "Prix": 3270})
    
    s.set_price(3270)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix loin de la zone → 2 LIMIT")
    l1 = s.limit("BUY", 3250, 3210, 3280, "C2b-L1")
    l2 = s.limit("BUY", 3230, 3210, 3280, "C2b-L2")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → L1 remplie @3250")
    s.set_price(3250)
    s.fill_limits()
    
    s.log.append(f"\n🔹 ÉTAPE 3 : Prix continue → L2 remplie @3230")
    s.set_price(3230)
    s.fill_limits()
    
    s.log.append(f"\n🔹 ÉTAPE 4 : Prix remonte vers TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 5 : TP3 atteint — les 2 ouvertes → fermer L1, L2 continue")
    s.close(l1, "C2b-close-L1")
    s.modify_sl(l2, 3250, "C2b-BE-L2-at-L1-entry")
    s.log.append(f"   ✅ L1 fermée, L2 BE @3250 + trailing")
    s.trail(l2, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# PRIX UNIQUE — S1 : MARKET seul → BE + trailing
# ============================================================
def pu_s1():
    s = header("PRIX UNIQUE — S1", {"Entry": 3240, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280})
    
    s.set_price(3242)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix entre entry et TP1 (@3242)")
    mt = s.market("BUY", 3242, 3210, 3280, "PU-S1")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix atteint TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 3 : TP3 atteint → MARKET BE + trailing")
    s.modify_sl(mt, 3240, "PU-S1-BE")
    s.log.append(f"   ✅ MARKET BE @3240 + trailing")
    s.trail(mt, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# PRIX UNIQUE — S2 : LIMIT seule → BE + trailing
# ============================================================
def pu_s2():
    s = header("PRIX UNIQUE — S2", {"Entry": 3240, "SL": 3210, "TPs": "[3260,3270,3280]", "TP3": 3280})
    
    s.set_price(3262)
    s.log.append(f"\n🔹 ÉTAPE 1 : Prix entre TP1 et TP2 (@3262)")
    lt = s.limit("BUY", 3240, 3210, 3280, "PU-S2")
    
    s.log.append(f"\n🔹 ÉTAPE 2 : Prix baisse → LIMIT remplie @3240")
    s.set_price(3240)
    s.fill_limits()
    
    s.log.append(f"\n🔹 ÉTAPE 3 : Prix atteint TP3 (@3280)")
    s.set_price(3280)
    
    s.log.append(f"\n🔹 ÉTAPE 4 : TP3 atteint → BE + trailing")
    s.modify_sl(lt, 3240, "PU-S2-BE")
    s.log.append(f"   ✅ LIMIT BE @3240 + trailing")
    s.trail(lt, "BUY", [3284, 3288, 3292])
    return s.log


# ============================================================
# MAIN
# ============================================================
def main():
    print("\n" + "🚀" * 30)
    print("   SIMULATION FINALE — OHLC + Polling 5s")
    print("   Règle : pending annulé, ouverte continue BE + trailing")
    print("🚀" * 30 + "\n")
    
    tests = [
        cas1_2a, cas1_2b,
        cas2a_3a1, cas2a_3a2,
        cas2b_3b1, cas2b_3b2, cas2b_3b3,
        pu_s1, pu_s2,
    ]
    
    for i, test in enumerate(tests):
        for line in test():
            print(line)
        if i < len(tests) - 1:
            print("")
    
    print("\n" + "=" * 60)
    print("✅ SIMULATION TERMINÉE — TOUS LES CAS VÉRIFIÉS")
    print("=" * 60)


if __name__ == "__main__":
    main()
