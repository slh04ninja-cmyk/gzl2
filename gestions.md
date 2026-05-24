

## 📊 CAS DE PRISE DE POSITION

### 1. Signal à Prix Unique (zone ≤ 1.0)
Quand le signal donne un seul prix (ENTRY: 3240, @ 3240, BUY 3240), le bot crée une mini-zone ±0.5 :

| Scénario | Condition (BUY) | Condition (SELL) | Action |
|----------|-----------------|------------------|--------|
| **S1**   | `entry ≤ prix ≤ TP1` | `TP1 ≤ prix ≤ entry` | **MARKET** @ prix actuel, lot 0.01, TP = tp_final |
| **S2**   | `TP1 < prix ≤ TP2`   | `TP2 ≤ prix < TP1`   | **LIMIT** @ prix du signal, lot 0.01, TP = tp_final |
| **S3**   | Sinon | Sinon | **Annulé** |

Commentaire MT5 : `CHn-Cm-S1` ou `CHn-Cm-S2`

---

### 2. CAS 1 — Prix DANS la zone d'entrée
`zone_low ≤ prix ≤ zone_high`

Deux ordres simultanés (lot split 50/50) :

| Ordre | Type | Prix | Lot | TP | Rôle |
|-------|------|------|-----|----|------|
| **1** | MARKET | Prix actuel    | 50% LOT_SIZE | tp_final | `market_tp3` |
| **2** | LIMIT  | Milieu SL↔zone | 50% LOT_SIZE | tp_final | `limit_catch` |

**Logique :**
- Le MARKET est exécuté immédiatement
- Le LIMIT attend entre le SL et la zone (rattraper un pullback)

Commentaire MT5 : `CHn-C1`

---

### 3. CAS 2 — Prix HORS zone

#### 3a. Prix entre la zone et TP1
`zone_high < prix < TP1` (BUY) ou `TP1 < prix < zone_low` (SELL)

| Ordre | Type | Prix | Lot | TP | Rôle |
|-------|------|------|-----|----|------|
| **1** | MARKET | Prix actuel          | 50% LOT_SIZE | tp_final | `market_cas2` |
| **2** | LIMIT  | Autre limite de zone | 50% LOT_SIZE | tp_final | `limit_cas2`  |

#### 3b. Prix loin de la zone (au-delà de TP1)

| Ordre | Type | Prix | Lot | TP | Rôle |
|-------|------|------|-----|----|------|
| **1** | LIMIT_1 | Bord de zone (proche du prix) | 50% LOT_SIZE | tp_final | `limit_1` |
| **2** | LIMIT_2 | Côté opposé de zone           | 50% LOT_SIZE | tp_final | `limit_2` |

Commentaire MT5 : `CHn-C2`

---

## 🔄 GESTION DES TRADES

### Général — Boucle de monitoring (toutes les 10s)
Le `TradeManager` vérifie en continu :
1. Les ordres LIMIT pending → résolus si exécutés
2. Les positions ouvertes → SL/TP touchés
3. Les expirations → annulation des LIMIT non exécutés après 240 min

---

### CAS 1 : Gestion après TP_TRIGGER (défaut TP3)

Quand le MARKET est fermé (TP atteint par MT5) :

| Scénario | Situation | Action |
|----------|-----------|--------|
| **A** | LIMIT non exécuté | **Annuler** le LIMIT |
| **B** | LIMIT exécuté     | **SL du LIMIT → entrée MARKET** + trailing activé |

**Exemple concret (BUY) :**
- MARKET ouvert @ 3245, LIMIT @ 3240
- Prix atteint TP3 → MARKET fermé automatiquement
- Si LIMIT rempli @ 3240 → SL de cette position = 3245 (entrée MARKET), sécurisé

---

### CAS 2 : Gestion après TP3 atteint

| Scénario | Situation | Action |
|----------|-----------|--------|
| **A** | Aucune LIMIT remplie      | **Annuler** les 2 LIMIT |
| **B** | Seulement LIMIT_1 remplie | **Annuler** LIMIT_2, **SL LIMIT_1 = entrée LIMIT_1** + trailing |
| **C** | Les 2 LIMIT remplies      | **Fermer** LIMIT_1 manuellement, **SL LIMIT_2 = entrée LIMIT_1** + trailing |

---

### Trailing SL (Ratio 1:2)

```
TRAIL_POINTS = 200 (2$)
trigger_step = 400 (4$)

Pour chaque 4$ de mouvement de prix → SL avance de 2$
```

| Direction | Trigger | Action SL |
|-----------|---------|-----------|
| BUY   | Prix monte de 4$  | SL = SL_actuel + 2$ |
| SELL | Prix descend de 4$ | SL = SL_actuel - 2$ |

Le trailing ne s'active qu'**après TP_TRIGGER atteint**.

---

### Conflit de direction

Si un signal SELL arrive alors qu'un BUY est ouvert sur le même symbole :
1. Annule tous les ordres pending du trade existant
2. Ferme toutes les positions du symbole
3. Exécute le nouveau signal

---

### Filtres de sécurité

| Filtre | Paramètre | Valeur |
|--------|-----------|--------|
| Max positions    | `MAX_POSITIONS`     | 6 |
| Max spread       | `MAX_SPREAD_POINTS` | 50 pts |
| News HIGH impact | `NEWS_ENABLED`      | Bloque 15 min avant, 15 min après |
| News fermeture   | `NEWS_CLOSE_MIN`    | Ferme les positions 5 min avant |
| Expiration LIMIT | `ORDER_EXPIRY_MIN`  |
...(truncated)...
