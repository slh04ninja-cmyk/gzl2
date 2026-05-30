# %% [markdown]
# # HTF Gold EA v4.3 — Backtest Python
# 
# Port complet de l'EA MQL5 en Python pour backtest sur Google Colab.
# 
# ## Stratégie
# - **Bias** : Double SuperTrend sur H1 (ST1: 7×0.4, ST2: 11×1.2)
# - **Entrée** : Velocity Spike (VSP) + Micro Structure Break (MSB) + Filtre Retournement
# - **Filtres** : ADX ≥ 23, Régime de marché, Circuit Breaker, Horaire London/NY
# - **SL/TP** : ATR dynamique (SL = ATR×1.5, TP = SL×2.1)
# - **Lot** : 1% risque + adaptatif
# - **Sorties** : Breakeven, fermeture temps (24 min), max 3 trades

# %%
# === INSTALLATION (Colab) ===
# !pip install pandas numpy yfinance matplotlib

import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import warnings
warnings.filterwarnings('ignore')

# %% [markdown]
# ## 1. Paramètres de l'EA

# %%
@dataclass
class EAParams:
    # Symbol
    symbol: str = "GC=F"  # Gold futures (yfinance)
    
    # Bias SuperTrend
    bias_tf_minutes: int = 60
    st1_period: int = 7
    st1_multiplier: float = 0.4
    st2_period: int = 11
    st2_multiplier: float = 1.2
    
    # ADX
    use_adx: bool = True
    adx_period: int = 12
    adx_min_level: float = 23.0
    
    # Velocity Spike (VSP)
    vsp_atr_period: int = 16
    vsp_spike_multi: float = 1.6
    vsp_need_confirm: bool = True
    vsp_lookback: int = 7
    use_cooldown: bool = True
    cooldown_bars: int = 5
    
    # Micro Structure Break (MSB)
    use_msb: bool = True
    msb_swing_bars: int = 16
    msb_break_type: int = 1  # 0=close, 1=touch, 2=body
    msb_timing: int = 0  # 0=before, 1=after
    
    # Retournement
    use_retournement: bool = True
    rt_swing_bars: int = 20
    rt_lookback: int = 30
    rt_buffer_atr: float = 0.3
    
    # Régime de marché
    use_regime: bool = True
    regime_adx_period: int = 14
    regime_atr_period: int = 14
    regime_atr_window: int = 20
    regime_adx_strong: float = 30.0
    regime_adx_weak: float = 23.0
    regime_adx_choppy: float = 18.0
    regime_vol_ratio_max: float = 2.0
    regime_efficiency_strong: float = 0.5
    regime_efficiency_weak: float = 0.3
    regime_efficiency_choppy: float = 0.2
    regime_eff_lookback: int = 20
    regime_weak_risk_factor: float = 0.5
    
    # Circuit Breaker
    use_circuit_breaker: bool = True
    cb_max_drawdown_pct: float = 5.0
    cb_cooldown_minutes: int = 120
    cb_loss_streak_limit: int = 6
    
    # SL/TP
    sltp_mode: int = 0  # 0=ATR, 1=points
    atr_sl_period: int = 15
    atr_sl_multi: float = 1.5
    rr_ratio: float = 2.1
    sl_points: int = 5000
    tp_points: int = 7500
    
    # Lot
    lot_mode: int = 1  # 0=fixed, 1=percent
    lot_size: float = 0.01
    risk_percent: float = 1.0
    lot_min: float = 0.01
    lot_max: float = 1.0
    
    # Adaptive Risk
    use_adaptive_risk: bool = True
    ar_loss_threshold: int = 4
    ar_reduce_factor: float = 0.25
    ar_max_boost: float = 3.0
    
    # Breakeven
    use_breakeven: bool = True
    be_trigger_rr: float = 1.4
    
    # Trade Management
    max_trades: int = 3
    slippage: int = 10
    
    # Time Close
    use_time_close: bool = True
    max_minutes: int = 24
    
    # Time Filter
    use_time_filter: bool = True
    w1_start: int = 7
    w1_end: int = 12
    w2_start: int = 14
    w2_end: int = 20
    session_cooldown_min: int = 15
    
    # Backtest
    initial_capital: float = 1000.0
    point_value: float = 0.1  # XAUUSDm

params = EAParams()
print("✅ Paramètres chargés")

# %% [markdown]
# ## 2. Indicateurs techniques

# %%
def calc_atr(high, low, close, period):
    """ATR classique"""
    tr = pd.DataFrame()
    tr['h-l'] = high - low
    tr['h-pc'] = (high - close.shift(1)).abs()
    tr['l-pc'] = (low - close.shift(1)).abs()
    tr['tr'] = tr[['h-l', 'h-pc', 'l-pc']].max(axis=1)
    return tr['tr'].rolling(window=period).mean()

def calc_adx(high, low, close, period):
    """ADX (Average Directional Index)"""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    
    # When +DM > -DM, -DM = 0 and vice versa
    mask = plus_dm > minus_dm
    minus_dm[mask] = 0
    plus_dm[~mask] = 0
    
    atr = calc_atr(high, low, close, period)
    
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.rolling(period).mean()
    
    return adx, plus_di, minus_di

def calc_supertrend(high, low, close, period, multiplier):
    """SuperTrend indicator"""
    atr = calc_atr(high, low, close, period)
    hl2 = (high + low) / 2
    
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr
    
    supertrend = pd.Series(0.0, index=close.index)
    direction = pd.Series(1, index=close.index)
    
    for i in range(1, len(close)):
        # Upper band
        if upper.iloc[i] < upper.iloc[i-1] or close.iloc[i-1] > upper.iloc[i-1]:
            pass
        else:
            upper.iloc[i] = upper.iloc[i-1]
        
        # Lower band
        if lower.iloc[i] > lower.iloc[i-1] or close.iloc[i-1] < lower.iloc[i-1]:
            pass
        else:
            lower.iloc[i] = lower.iloc[i-1]
        
        # Direction
        if close.iloc[i] > upper.iloc[i]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
        
        supertrend.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]
    
    return supertrend, direction

def calc_efficiency_ratio(close, lookback):
    """Kaufman Efficiency Ratio"""
    net_move = (close - close.shift(lookback)).abs()
    total_move = close.diff().abs().rolling(lookback).sum()
    return (net_move / total_move).fillna(0)

print("✅ Fonctions indicateurs définies")

# %% [markdown]
# ## 3. Régime de marché

# %%
REGIME_STRONG_TREND = 0
REGIME_WEAK_TREND = 1
REGIME_RANGE = 2
REGIME_VOLATILE = 3
REGIME_CHOPPY = 4

def detect_regime(adx_val, volatility_ratio, efficiency_ratio, p):
    """Détecte le régime de marché"""
    if volatility_ratio > p.regime_vol_ratio_max:
        return REGIME_VOLATILE, "VOLATILE"
    
    if adx_val < p.regime_adx_choppy and efficiency_ratio < p.regime_efficiency_choppy:
        return REGIME_CHOPPY, "CHOPPY"
    
    if adx_val < p.regime_adx_weak and efficiency_ratio < p.regime_efficiency_weak:
        return REGIME_RANGE, "RANGE"
    
    if adx_val >= p.regime_adx_strong and efficiency_ratio >= p.regime_efficiency_strong:
        return REGIME_STRONG_TREND, "STRONG_TREND"
    
    return REGIME_WEAK_TREND, "WEAK_TREND"

print("✅ Fonction régime définie")

# %% [markdown]
# ## 4. Données XAUUSD

# %%
def fetch_gold_data(period_days=30, interval="1m"):
    """Télécharge les données XAUUSD via yfinance (batch 8 jours pour M1)"""
    ticker = yf.Ticker("GC=F")
    
    # yfinance limit: 8 days of M1 data per request
    if interval == "1m":
        batch_days = 7
    else:
        batch_days = period_days
    
    all_dfs = []
    end = datetime.now()
    start = end - timedelta(days=period_days)
    
    current = start
    while current < end:
        batch_end = min(current + timedelta(days=batch_days), end)
        try:
            df_batch = ticker.history(start=current, end=batch_end, interval=interval)
            if not df_batch.empty:
                all_dfs.append(df_batch)
        except Exception as e:
            print(f"⚠️ Batch {current.date()} → {batch_end.date()}: {e}")
        current = batch_end
    
    if not all_dfs:
        print(f"⚠️ Pas de données M1, essai 5m...")
        return fetch_gold_data(period_days, "5m")
    
    df = pd.concat(all_dfs)
    df = df[~df.index.duplicated(keep='first')]
    df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close', 'volume']].copy()
    df = df.dropna()
    df = df.sort_index()
    
    print(f"✅ Données: {len(df)} barres | {df.index[0]} → {df.index[-1]}")
    return df

# %% [markdown]
# ## 5. Moteur de backtest

# %%
@dataclass
class Trade:
    entry_time: datetime
    entry_price: float
    direction: int  # 1=BUY, -1=SELL
    lot: float
    sl: float
    tp: float
    sl_dist: float
    tp_dist: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    profit: float = 0.0
    be_active: bool = False
    entry_bar: int = 0

@dataclass
class BacktestState:
    capital: float
    peak_equity: float
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    risk_mult: float = 1.0
    cb_active: bool = False
    cb_time: Optional[datetime] = None
    cb_reason: str = ""
    cb_consec_losses: int = 0
    last_spike_time: dict = field(default_factory=lambda: {1: None, -1: None})
    open_trades: List[Trade] = field(default_factory=list)
    closed_trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

def run_backtest(df, params):
    """Exécute le backtest complet"""
    p = params
    state = BacktestState(capital=p.initial_capital, peak_equity=p.initial_capital)
    
    # === Pré-calcul indicateurs sur H1 ===
    # Resample M1 → H1 pour le biais
    df_h1 = df.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    
    # SuperTrend H1
    _, st1_dir = calc_supertrend(df_h1['high'], df_h1['low'], df_h1['close'], p.st1_period, p.st1_multiplier)
    _, st2_dir = calc_supertrend(df_h1['high'], df_h1['low'], df_h1['close'], p.st2_period, p.st2_multiplier)
    
    # Bias: +1 = bullish, -1 = bearish, 0 = neutral
    bias_h1 = pd.Series(0, index=df_h1.index)
    bias_h1[(st1_dir == 1) & (st2_dir == 1)] = 1
    bias_h1[(st1_dir == -1) & (st2_dir == -1)] = -1
    
    # ADX H1
    adx_h1, _, _ = calc_adx(df_h1['high'], df_h1['low'], df_h1['close'], p.adx_period)
    
    # Regime indicators
    adx_regime, _, _ = calc_adx(df_h1['high'], df_h1['low'], df_h1['close'], p.regime_adx_period)
    atr_h1 = calc_atr(df_h1['high'], df_h1['low'], df_h1['close'], p.regime_atr_period)
    atr_avg_h1 = atr_h1.rolling(p.regime_atr_window).mean()
    vol_ratio_h1 = (atr_h1 / atr_avg_h1).fillna(1.0)
    eff_ratio_h1 = calc_efficiency_ratio(df_h1['close'], p.regime_eff_lookback)
    
    # === Pré-calcul indicateurs sur M1 ===
    atr_m1_sl = calc_atr(df['high'], df['low'], df['close'], p.atr_sl_period)
    atr_m1_vsp = calc_atr(df['high'], df['low'], df['close'], p.vsp_atr_period)
    
    # Tracking
    signals_log = []
    regime_log = []
    
    print(f"\n🚀 Backtest démarré | Capital: {p.initial_capital}$ | Barres M1: {len(df)}")
    print(f"   Bias TF: H1 | ST1: {p.st1_period}×{p.st1_multiplier} | ST2: {p.st2_period}×{p.st2_multiplier}")
    print(f"   SL/TP: ATR({p.atr_sl_period})×{p.atr_sl_multi} | RR: {p.rr_ratio}")
    print(f"   Max trades: {p.max_trades} | Time close: {p.max_minutes}min")
    print("-" * 80)
    
    # === Boucle principale (sur chaque barre M1) ===
    for i in range(max(p.vsp_lookback + 5, p.rt_lookback + p.rt_swing_bars + 5, 100), len(df)):
        bar_time = df.index[i]
        
        # Trouver la bougie H1 correspondante
        h1_time = df_h1.index[df_h1.index <= bar_time]
        if len(h1_time) == 0:
            state.equity_curve.append(state.capital)
            continue
        h1_time = h1_time[-1]
        
        # Track equity (realized + unrealized P&L)
        current_equity = state.capital
        for t in state.open_trades:
            current_price = df['close'].iloc[i]
            if t.direction == 1:
                current_equity += (current_price - t.entry_price) * t.lot  # XAUUSDm: 1 lot = 1$/point
            else:
                current_equity += (t.entry_price - current_price) * t.lot
        state.equity_curve.append(current_equity)
        
        # Update peak equity for circuit breaker
        if current_equity > state.peak_equity:
            state.peak_equity = current_equity
        
        # === Gestion des trades ouverts (BE, Time Close, SL/TP) ===
        trades_to_close = []
        for t in state.open_trades:
            current = df['close'].iloc[i]
            high_bar = df['high'].iloc[i]
            low_bar = df['low'].iloc[i]
            minutes_open = (bar_time - t.entry_time).total_seconds() / 60
            
            # Check SL hit
            if t.direction == 1 and low_bar <= t.sl:
                trades_to_close.append((t, t.sl, "SL"))
                continue
            elif t.direction == -1 and high_bar >= t.sl:
                trades_to_close.append((t, t.sl, "SL"))
                continue
            
            # Check TP hit
            if t.direction == 1 and high_bar >= t.tp:
                trades_to_close.append((t, t.tp, "TP"))
                continue
            elif t.direction == -1 and low_bar <= t.tp:
                trades_to_close.append((t, t.tp, "TP"))
                continue
            
            # Breakeven
            if p.use_breakeven and not t.be_active:
                if t.direction == 1:
                    trigger = t.entry_price + (t.tp - t.entry_price) * p.be_trigger_rr
                    if current >= trigger:
                        t.sl = t.entry_price
                        t.be_active = True
                else:
                    trigger = t.entry_price - (t.entry_price - t.tp) * p.be_trigger_rr
                    if current <= trigger:
                        t.sl = t.entry_price
                        t.be_active = True
            
            # Time close
            if p.use_time_close and minutes_open >= p.max_minutes:
                trades_to_close.append((t, current, "TIME"))
        
        # Close trades
        for t, exit_price, reason in trades_to_close:
            # XAUUSDm: 1 lot = 1$ per 1$ price move
            if t.direction == 1:
                pnl = (exit_price - t.entry_price) * t.lot
            else:
                pnl = (t.entry_price - exit_price) * t.lot
            
            t.exit_time = bar_time
            t.exit_price = exit_price
            t.exit_reason = reason
            t.profit = pnl
            state.capital += pnl
            state.closed_trades.append(t)
            state.open_trades.remove(t)
            
            # Update adaptive risk
            if pnl < 0:
                state.consecutive_losses += 1
                state.consecutive_wins = 0
                state.cb_consec_losses += 1
            elif pnl > 0:
                state.consecutive_wins += 1
                state.consecutive_losses = 0
                state.cb_consec_losses = 0
            
            # Update peak equity
            if state.capital > state.peak_equity:
                state.peak_equity = state.capital
        
        # === Circuit Breaker ===
        if p.use_circuit_breaker:
            if state.cb_active:
                elapsed = (bar_time - state.cb_time).total_seconds() / 60 if state.cb_time else 999
                if elapsed < p.cb_cooldown_minutes:
                    continue
                else:
                    state.cb_active = False
                    state.peak_equity = current_equity  # Reset to current equity
                    state.cb_consec_losses = 0
            
            # Check drawdown using current equity (including unrealized P&L)
            dd_pct = 0
            if state.peak_equity > 0:
                dd_pct = ((state.peak_equity - current_equity) / state.peak_equity) * 100
            
            if dd_pct >= p.cb_max_drawdown_pct:
                state.cb_active = True
                state.cb_time = bar_time
                state.cb_reason = f"DD {dd_pct:.1f}%"
                continue
            
            if state.cb_consec_losses >= p.cb_loss_streak_limit:
                state.cb_active = True
                state.cb_time = bar_time
                state.cb_reason = f"Loss streak {state.cb_consec_losses}"
                continue
        
        # === Time Filter ===
        if p.use_time_filter:
            hour = bar_time.hour
            in_w1 = p.w1_start <= hour < p.w1_end
            in_w2 = p.w2_start <= hour < p.w2_end
            if not in_w1 and not in_w2:
                continue
            
            # Session cooldown
            minute = bar_time.minute
            if p.session_cooldown_min > 0:
                if (in_w1 and hour == p.w1_start and minute < p.session_cooldown_min):
                    continue
                if (in_w2 and hour == p.w2_start and minute < p.session_cooldown_min):
                    continue
        
        # === Max trades ===
        if len(state.open_trades) >= p.max_trades:
            continue
        
        # === Biais H1 ===
        if h1_time not in bias_h1.index:
            continue
        bias = bias_h1.loc[h1_time]
        if bias == 0:
            continue
        
        # === ADX filtre ===
        if p.use_adx and h1_time in adx_h1.index:
            adx_val = adx_h1.loc[h1_time]
            if pd.notna(adx_val) and adx_val < p.adx_min_level:
                continue
        
        # === Régime de marché ===
        effective_risk = 1.0
        if p.use_regime and h1_time in adx_regime.index:
            adx_r = adx_regime.loc[h1_time] if pd.notna(adx_regime.loc[h1_time]) else 0
            vr = vol_ratio_h1.loc[h1_time] if h1_time in vol_ratio_h1.index and pd.notna(vol_ratio_h1.loc[h1_time]) else 1.0
            er = eff_ratio_h1.loc[h1_time] if h1_time in eff_ratio_h1.index and pd.notna(eff_ratio_h1.loc[h1_time]) else 0.5
            
            regime, regime_name = detect_regime(adx_r, vr, er, p)
            
            if regime in (REGIME_RANGE, REGIME_CHOPPY, REGIME_VOLATILE):
                continue
            if regime == REGIME_WEAK_TREND:
                effective_risk = p.regime_weak_risk_factor
        
        # === Couche 1: Velocity Spike ===
        if i < p.vsp_lookback + 2:
            continue
        
        spike_detected = False
        spike_dir = 0
        spike_bar = 0
        
        for j in range(2, min(p.vsp_lookback + 2, i)):
            idx = i - j
            body = abs(df['close'].iloc[idx] - df['open'].iloc[idx])
            atr_v = atr_m1_vsp.iloc[idx]
            
            if pd.isna(atr_v) or atr_v <= 0:
                continue
            
            if body >= atr_v * p.vsp_spike_multi:
                direction = 1 if df['close'].iloc[idx] > df['open'].iloc[idx] else -1
                
                # Cooldown
                if p.use_cooldown:
                    last = state.last_spike_time.get(direction)
                    if last is not None:
                        bars_elapsed = idx - last
                        if bars_elapsed < p.cooldown_bars:
                            continue
                
                spike_detected = True
                spike_dir = direction
                spike_bar = idx
                break
        
        if not spike_detected:
            continue
        
        # Spike must be OPPOSITE to bias
        if spike_dir != -bias:
            continue
        
        # Confirmation candle
        if p.vsp_need_confirm:
            conf_idx = i - 1
            if conf_idx >= 0:
                if bias == 1 and df['close'].iloc[conf_idx] <= df['open'].iloc[conf_idx]:
                    continue
                if bias == -1 and df['close'].iloc[conf_idx] >= df['open'].iloc[conf_idx]:
                    continue
        
        # Update spike time
        state.last_spike_time[spike_dir] = spike_bar
        
        # === Couche 2: MSB ===
        signal = bias
        
        if p.use_msb:
            msb_detected = False
            if p.msb_timing == 0:  # Before spike
                ref_start = spike_bar + 1
                ref_end = spike_bar + p.msb_swing_bars
                zone_start = 2
                zone_end = spike_bar - 1
                
                if zone_start > zone_end or ref_end >= len(df):
                    continue
                
                if signal == 1:
                    swing_low = df['low'].iloc[ref_start:ref_end+1].min()
                    for k in range(zone_end, zone_start - 1, -1):
                        if p.msb_break_type == 1:  # Touch
                            if df['high'].iloc[k] > swing_low:
                                msb_detected = True
                                break
                        elif p.msb_break_type == 0:  # Close
                            if df['close'].iloc[k] > swing_low:
                                msb_detected = True
                                break
                else:
                    swing_high = df['high'].iloc[ref_start:ref_end+1].max()
                    for k in range(zone_end, zone_start - 1, -1):
                        if p.msb_break_type == 1:
                            if df['low'].iloc[k] < swing_high:
                                msb_detected = True
                                break
                        elif p.msb_break_type == 0:
                            if df['close'].iloc[k] < swing_high:
                                msb_detected = True
                                break
            
            if not msb_detected:
                continue
        
        # === Filtre Retournement ===
        if p.use_retournement:
            ref_start = spike_bar + 1
            ref_end = spike_bar + p.rt_swing_bars
            if ref_end >= len(df):
                continue
            
            atr_at_spike = atr_m1_vsp.iloc[spike_bar] if pd.notna(atr_m1_vsp.iloc[spike_bar]) else 0
            buffer = atr_at_spike * p.rt_buffer_atr
            
            retournement = False
            if signal == 1:
                swing_low = df['low'].iloc[ref_start:ref_end+1].min()
                spike_low = df['low'].iloc[spike_bar]
                if spike_low < swing_low - buffer:
                    retournement = True
                # Confirm candle
                if df['low'].iloc[i-1] < swing_low - buffer:
                    retournement = True
            else:
                swing_high = df['high'].iloc[ref_start:ref_end+1].max()
                spike_high = df['high'].iloc[spike_bar]
                if spike_high > swing_high + buffer:
                    retournement = True
                if df['high'].iloc[i-1] > swing_high + buffer:
                    retournement = True
            
            if retournement:
                continue
        
        # === Calcul SL/TP ===
        if p.sltp_mode == 0:  # ATR
            atr_sl = atr_m1_sl.iloc[i]
            if pd.isna(atr_sl) or atr_sl <= 0:
                continue
            sl_dist = atr_sl * p.atr_sl_multi
            # Minimum SL: 3$ for gold (avoid noise stops)
            min_sl = 3.0
            if sl_dist < min_sl:
                sl_dist = min_sl
            tp_dist = sl_dist * p.rr_ratio
        else:  # Points
            sl_dist = p.sl_points * df['close'].iloc[i] * 0.0001  # approx
            tp_dist = p.tp_points * df['close'].iloc[i] * 0.0001
        
        # === Calcul lot ===
        # XAUUSDm: 1 lot = 1$ per 1$ price move
        if p.lot_mode == 0:
            lot = p.lot_size
        else:
            adaptive_mult = state.risk_mult if p.use_adaptive_risk else 1.0
            effective_risk_pct = p.risk_percent * adaptive_mult * effective_risk
            risk_money = state.capital * (effective_risk_pct / 100.0)
            if sl_dist <= 0:
                continue
            # lot = risk_money / sl_dist (for XAUUSDm where 1 lot = 1$/point)
            lot = risk_money / sl_dist
            lot = max(p.lot_min, min(p.lot_max, round(lot, 2)))
        
        # === Mise à jour risque adaptatif ===
        if p.use_adaptive_risk:
            new_mult = 1.0
            if state.consecutive_losses >= p.ar_loss_threshold * 2:
                new_mult = p.ar_reduce_factor ** 2
            elif state.consecutive_losses >= p.ar_loss_threshold:
                new_mult = p.ar_reduce_factor
            elif state.consecutive_wins >= 2:
                new_mult = min(p.ar_max_boost, 1.0 + (state.consecutive_wins - 1) * 0.1)
            state.risk_mult = max(0.25, min(p.ar_max_boost, new_mult))
        
        # === Exécution ===
        current = df['close'].iloc[i]
        
        # Check no duplicate direction
        has_same = any(t.direction == signal for t in state.open_trades)
        if has_same:
            continue
        
        if signal == 1:
            sl = current - sl_dist
            tp = current + tp_dist
        else:
            sl = current + sl_dist
            tp = current - tp_dist
        
        trade = Trade(
            entry_time=bar_time,
            entry_price=current,
            direction=signal,
            lot=lot,
            sl=sl,
            tp=tp,
            sl_dist=sl_dist,
            tp_dist=tp_dist,
            entry_bar=i
        )
        state.open_trades.append(trade)
        signals_log.append({
            'time': bar_time, 'dir': 'BUY' if signal == 1 else 'SELL',
            'price': current, 'sl': sl, 'tp': tp, 'lot': lot
        })
    
    # === Fermer les trades restants ===
    for t in state.open_trades:
        exit_price = df['close'].iloc[-1]
        if t.direction == 1:
            pnl = (exit_price - t.entry_price) * t.lot
        else:
            pnl = (t.entry_price - exit_price) * t.lot
        t.exit_time = df.index[-1]
        t.exit_price = exit_price
        t.exit_reason = "END"
        t.profit = pnl
        state.capital += pnl
        state.closed_trades.append(t)
    state.open_trades.clear()
    
    return state, signals_log

print("✅ Moteur de backtest défini")

# %% [markdown]
# ## 6. Lancer le backtest

# %%
# Télécharger les données (30 jours de M1 par défaut)
df = fetch_gold_data(period_days=30, interval="1m")

# Lancer le backtest
state, signals = run_backtest(df, params)

# %% [markdown]
# ## 7. Résultats

# %%
def print_results(state, params):
    """Affiche les statistiques du backtest"""
    trades = state.closed_trades
    if not trades:
        print("❌ Aucun trade exécuté")
        return
    
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit < 0]
    breakevens = [t for t in trades if t.profit == 0]
    
    total_pnl = sum(t.profit for t in trades)
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    
    avg_win = np.mean([t.profit for t in wins]) if wins else 0
    avg_loss = np.mean([t.profit for t in losses]) if losses else 0
    
    profit_factor = abs(sum(t.profit for t in wins) / sum(t.profit for t in losses)) if losses and sum(t.profit for t in losses) != 0 else float('inf')
    
    # Max drawdown
    equity = state.equity_curve
    peak = equity[0]
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    # Trade duration
    durations = [(t.exit_time - t.entry_time).total_seconds() / 60 for t in trades if t.exit_time]
    avg_duration = np.mean(durations) if durations else 0
    
    # Exit reasons
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    
    print("=" * 60)
    print("📊 RÉSULTATS DU BACKTEST — HTF Gold EA v4.3")
    print("=" * 60)
    print(f"  Capital initial:    {params.initial_capital:.2f}$")
    print(f"  Capital final:      {state.capital:.2f}$")
    print(f"  P&L total:          {total_pnl:.2f}$ ({total_pnl/params.initial_capital*100:.1f}%)")
    print(f"  Max drawdown:       {max_dd:.2f}%")
    print()
    print(f"  Trades total:       {len(trades)}")
    print(f"  Gagnants:           {len(wins)} ({win_rate:.1f}%)")
    print(f"  Perdants:           {len(losses)}")
    print(f"  Break-even:         {len(breakevens)}")
    print()
    print(f"  Gain moyen:         {avg_win:.2f}$")
    print(f"  Perte moyenne:      {avg_loss:.2f}$")
    print(f"  Profit factor:      {profit_factor:.2f}")
    print(f"  Durée moyenne:      {avg_duration:.1f} min")
    print()
    print(f"  Sorties: {reasons}")
    print()
    
    # By direction
    buys = [t for t in trades if t.direction == 1]
    sells = [t for t in trades if t.direction == -1]
    print(f"  BUY:  {len(buys)} trades | P&L: {sum(t.profit for t in buys):.2f}$")
    print(f"  SELL: {len(sells)} trades | P&L: {sum(t.profit for t in sells):.2f}$")
    print("=" * 60)
    
    return trades

trades = print_results(state, params)

# %% [markdown]
# ## 8. Graphiques

# %%
def plot_results(state, df, trades):
    """Graphiques des résultats"""
    fig, axes = plt.subplots(3, 1, figsize=(16, 12), gridspec_kw={'height_ratios': [3, 1, 1]})
    
    # 1. Equity curve
    ax1 = axes[0]
    ax1.plot(state.equity_curve, color='#2196F3', linewidth=1.5, label='Equity')
    ax1.axhline(y=params.initial_capital, color='gray', linestyle='--', alpha=0.5)
    ax1.set_title('Courbe d\'équité — HTF Gold EA v4.3', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Capital ($)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # 2. Trades sur le prix
    ax2 = axes[1]
    ax2.plot(df['close'].values, color='gray', linewidth=0.5, alpha=0.7, label='XAUUSD')
    for t in trades:
        color = '#4CAF50' if t.profit > 0 else '#F44336'
        marker = '^' if t.direction == 1 else 'v'
        idx = df.index.get_loc(t.entry_time) if t.entry_time in df.index else None
        if idx:
            ax2.scatter(idx, t.entry_price, color=color, marker=marker, s=60, zorder=5)
    ax2.set_title('Signaux sur XAUUSD', fontsize=12)
    ax2.set_ylabel('Prix')
    ax2.grid(True, alpha=0.3)
    
    # 3. P&L par trade
    ax3 = axes[2]
    pnls = [t.profit for t in trades]
    colors = ['#4CAF50' if p > 0 else '#F44336' for p in pnls]
    ax3.bar(range(len(pnls)), pnls, color=colors, alpha=0.7)
    ax3.axhline(y=0, color='gray', linestyle='-', alpha=0.5)
    ax3.set_title('P&L par trade', fontsize=12)
    ax3.set_xlabel('Trade #')
    ax3.set_ylabel('P&L ($)')
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('htf_gold_backtest_results.png', dpi=150, bbox_inches='tight')
    plt.show()
    print("✅ Graphique sauvegardé: htf_gold_backtest_results.png")

if trades:
    plot_results(state, df, trades)

# %% [markdown]
# ## 9. Détail des trades

# %%
def trades_table(trades):
    """Tableau détaillé des trades"""
    rows = []
    for i, t in enumerate(trades):
        rows.append({
            '#': i + 1,
            'Direction': 'BUY' if t.direction == 1 else 'SELL',
            'Entrée': f"{t.entry_price:.2f}",
            'Sortie': f"{t.exit_price:.2f}" if t.exit_price else '-',
            'Lot': f"{t.lot:.2f}",
            'P&L': f"{t.profit:.2f}$",
            'Raison': t.exit_reason,
            'Durée': f"{(t.exit_time - t.entry_time).total_seconds()/60:.0f}min" if t.exit_time else '-',
            'BE': '✓' if t.be_active else ''
        })
    return pd.DataFrame(rows)

if trades:
    df_trades = trades_table(trades)
    print(df_trades.to_string(index=False))
    
    # Export CSV
    df_trades.to_csv('htf_gold_trades.csv', index=False)
    print("\n✅ Export: htf_gold_trades.csv")

# %% [markdown]
# ## Notes
# - Les données M1 de yfinance sont limitées (~30 jours pour GC=F)
# - Le spread et slippage ne sont pas simulés (ajouter un buffer si nécessaire)
# - Le point_value (0.1) est pour XAUUSDm — ajuster pour ton broker
# - Pour plus de données, utiliser un feed MT5 ou un autre fournisseur


# %% [markdown]
# ## 10. Optimisation automatique
# 
# Teste chaque paramètre avec 5 valeurs (-2 à +2 autour du défaut)
# et garde celle qui donne le plus de gain.

# %%
from copy import deepcopy

def get_param_value(p, name):
    """Récupère la valeur d'un paramètre"""
    return getattr(p, name)

def set_param_value(p, name, value):
    """Définit la valeur d'un paramètre"""
    setattr(p, name, value)

def generate_values(default, is_float=False, is_int=False):
    """Génère 5 valeurs autour du défaut (-2, -1, 0, +1, +2)"""
    if is_int:
        step = 1
        values = [default - 2, default - 1, default, default + 1, default + 2]
        return [max(1, int(v)) for v in values]  # Minimum 1
    else:
        step = 0.1
        values = [default - 0.2, default - 0.1, default, default + 0.1, default + 0.2]
        return [max(0.01, round(v, 2)) for v in values]  # Minimum 0.01

# Paramètres à optimiser (nom, type, borne min, borne max)
OPTIMIZE_PARAMS = [
    # SuperTrend
    ("st1_period", "int", 3, 20),
    ("st1_multiplier", "float", 0.1, 2.0),
    ("st2_period", "int", 5, 30),
    ("st2_multiplier", "float", 0.5, 3.0),
    
    # ADX
    ("adx_period", "int", 5, 30),
    ("adx_min_level", "float", 10.0, 40.0),
    
    # VSP
    ("vsp_atr_period", "int", 5, 30),
    ("vsp_spike_multi", "float", 0.5, 3.0),
    ("vsp_lookback", "int", 3, 20),
    ("cooldown_bars", "int", 1, 20),
    
    # MSB
    ("msb_swing_bars", "int", 5, 30),
    
    # Retournement
    ("rt_swing_bars", "int", 5, 40),
    ("rt_lookback", "int", 10, 50),
    ("rt_buffer_atr", "float", 0.1, 1.0),
    
    # SL/TP
    ("atr_sl_period", "int", 5, 30),
    ("atr_sl_multi", "float", 0.5, 3.0),
    ("rr_ratio", "float", 1.0, 4.0),
    
    # Lot
    ("risk_percent", "float", 0.5, 5.0),
    
    # Breakeven
    ("be_trigger_rr", "float", 0.5, 2.0),
    
    # Time
    ("max_minutes", "int", 5, 60),
    ("session_cooldown_min", "int", 0, 30),
    
    # Regime
    ("regime_adx_strong", "float", 20.0, 50.0),
    ("regime_adx_weak", "float", 15.0, 35.0),
    ("regime_weak_risk_factor", "float", 0.2, 1.0),
    
    # Circuit Breaker
    ("cb_max_drawdown_pct", "float", 2.0, 10.0),
    ("cb_loss_streak_limit", "int", 3, 15),
]

def run_optimization(df, base_params, optimize_params):
    """Exécute l'optimisation paramètre par paramètre"""
    print("=" * 70)
    print("🔬 OPTIMISATION AUTOMATIQUE — HTF Gold EA v4.3")
    print("=" * 70)
    print(f"  Paramètres à optimiser: {len(optimize_params)}")
    print(f"  Valeurs testées par param: 5 (-2 à +2)")
    print(f"  Total backtests: {len(optimize_params) * 5}")
    print("=" * 70)
    
    best_params = deepcopy(base_params)
    results = []
    
    for idx, (param_name, param_type, min_val, max_val) in enumerate(optimize_params):
        default_val = get_param_value(best_params, param_name)
        
        # Générer les valeurs à tester
        if param_type == "int":
            values = [default_val - 2, default_val - 1, default_val, default_val + 1, default_val + 2]
            values = [max(min_val, min(max_val, int(v))) for v in values]
        else:
            values = [default_val - 0.2, default_val - 0.1, default_val, default_val + 0.1, default_val + 0.2]
            values = [max(min_val, min(max_val, round(v, 2))) for v in values]
        
        # Dédupliquer et trier
        values = sorted(set(values))
        
        print(f"\n[{idx+1}/{len(optimize_params)}] {param_name} (défaut={default_val})")
        print(f"  Test: {values}")
        
        best_val = default_val
        best_pnl = -float('inf')
        best_trades = 0
        
        for val in values:
            # Créer une copie avec le paramètre modifié
            test_params = deepcopy(best_params)
            set_param_value(test_params, param_name, val)
            
            try:
                # Exécuter le backtest
                state, signals = run_backtest(df, test_params)
                
                # Calculer le P&L total
                total_pnl = sum(t.profit for t in state.closed_trades)
                num_trades = len(state.closed_trades)
                
                # Critère: P&L total (avec bonus pour plus de trades)
                score = total_pnl
                
                status = "✓" if score > best_pnl else " "
                print(f"    {status} {param_name}={val} → P&L={total_pnl:+.2f}$ | Trades={num_trades}")
                
                if score > best_pnl:
                    best_pnl = score
                    best_val = val
                    best_trades = num_trades
                    
            except Exception as e:
                print(f"    ✗ {param_name}={val} → Erreur: {str(e)[:50]}")
        
        # Appliquer la meilleure valeur
        if best_val != default_val:
            improvement = best_pnl - sum(t.profit for t in run_backtest(df, best_params)[0].closed_trades) if best_pnl > -float('inf') else 0
            set_param_value(best_params, param_name, best_val)
            print(f"  → OPTIMISÉ: {param_name}={default_val} → {best_val} (P&L: {best_pnl:+.2f}$)")
            results.append({
                'param': param_name,
                'old': default_val,
                'new': best_val,
                'pnl': best_pnl,
                'trades': best_trades,
                'improved': True
            })
        else:
            print(f"  → Inchangé: {param_name}={default_val}")
            results.append({
                'param': param_name,
                'old': default_val,
                'new': best_val,
                'pnl': best_pnl,
                'trades': best_trades,
                'improved': False
            })
    
    return best_params, results

# %% [markdown]
# ## 11. Lancer l'optimisation

# %%
# Lancer l'optimisation
best_params, opt_results = run_optimization(df, params, OPTIMIZE_PARAMS)

# %% [markdown]
# ## 12. Résultats de l'optimisation

# %%
def print_optimization_results(results, best_params):
    """Affiche les résultats de l'optimisation"""
    improved = [r for r in results if r['improved']]
    
    print("=" * 70)
    print("📊 RÉSULTATS DE L'OPTIMISATION")
    print("=" * 70)
    
    if improved:
        print(f"\n✅ {len(improved)} paramètres optimisés:\n")
        for r in improved:
            print(f"  • {r['param']}: {r['old']} → {r['new']} (P&L: {r['pnl']:+.2f}$)")
    else:
        print("\n⚠️ Aucun paramètre amélioré")
    
    print("\n" + "=" * 70)
    print("📋 PARAMÈTRES OPTIMISÉS FINAUX:")
    print("=" * 70)
    
    # Afficher les paramètres modifiés
    for r in results:
        if r['improved']:
            print(f"  {r['param']} = {r['new']}")
    
    # Sauvegarder les paramètres optimisés
    import json
    optimized_dict = {}
    for r in results:
        optimized_dict[r['param']] = r['new']
    
    with open('optimized_params.json', 'w') as f:
        json.dump(optimized_dict, f, indent=2)
    
    print("\n✅ Paramètres sauvegardés: optimized_params.json")
    print("=" * 70)
    
    return optimized_dict

opt_dict = print_optimization_results(opt_results, best_params)

# %% [markdown]
# ## 13. Backtest final avec paramètres optimisés

# %%
# Exécuter le backtest final avec les paramètres optimisés
print("\n🚀 BACKTEST FINAL avec paramètres optimisés...")
state_opt, signals_opt = run_backtest(df, best_params)

# Afficher les résultats
trades_opt = print_results(state_opt, best_params)

if trades_opt:
    plot_results(state_opt, df, trades_opt)
    
    # Comparaison
    print("\n" + "=" * 70)
    print("📈 COMPARAISON AVANT / APRÈS OPTIMISATION")
    print("=" * 70)
    
    # Backtest original
    state_orig, _ = run_backtest(df, params)
    orig_pnl = sum(t.profit for t in state_orig.closed_trades)
    opt_pnl = sum(t.profit for t in state_opt.closed_trades)
    
    print(f"  P&L original:    {orig_pnl:+.2f}$")
    print(f"  P&L optimisé:    {opt_pnl:+.2f}$")
    print(f"  Amélioration:    {opt_pnl - orig_pnl:+.2f}$ ({(opt_pnl - orig_pnl)/abs(orig_pnl)*100:+.1f}%)" if orig_pnl != 0 else "")
    print("=" * 70)

# %% [markdown]
# ## 14. Export des paramètres optimisés

# %%
# Générer le fichier .set pour MT5
def generate_set_file(best_params, opt_results):
    """Génère un fichier .set MT5 avec les paramètres optimisés"""
    lines = []
    lines.append("; HTF_Gold_EA_v4.3 — Paramètres optimisés par backtest Python")
    lines.append(f"; Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(";")
    
    # Mapping Python → MQL5
    param_mapping = {
        "st1_period": "InpST1_Period",
        "st1_multiplier": "InpST1_Multiplier",
        "st2_period": "InpST2_Period",
        "st2_multiplier": "InpST2_Multiplier",
        "adx_period": "InpADX_Period",
        "adx_min_level": "InpADX_MinLevel",
        "vsp_atr_period": "InpVSP_ATR_Period",
        "vsp_spike_multi": "InpVSP_Spike_Multi",
        "vsp_lookback": "InpVSP_LookBack",
        "cooldown_bars": "InpCooldownBars",
        "msb_swing_bars": "InpMSB_SwingBars",
        "rt_swing_bars": "InpRT_SwingBars",
        "rt_lookback": "InpRT_LookBack",
        "rt_buffer_atr": "InpRT_Buffer_ATR",
        "atr_sl_period": "InpATR_SL_Period",
        "atr_sl_multi": "InpATR_SL_Multi",
        "rr_ratio": "InpRR_Ratio",
        "risk_percent": "InpRiskPercent",
        "be_trigger_rr": "InpBE_Trigger_RR",
        "max_minutes": "InpMaxMinutes",
        "session_cooldown_min": "InpSessionCooldownMin",
        "regime_adx_strong": "InpRegime_ADXStrong",
        "regime_adx_weak": "InpRegime_ADXWeak",
        "regime_weak_risk_factor": "InpRegime_WeakRiskFactor",
        "cb_max_drawdown_pct": "InpCB_MaxDrawdownPct",
        "cb_loss_streak_limit": "InpCB_LossStreakLimit",
    }
    
    for py_name, mql_name in param_mapping.items():
        val = getattr(best_params, py_name)
        # Check if optimized
        was_opt = any(r['param'] == py_name and r['improved'] for r in opt_results)
        marker = " ; OPTIMIZED" if was_opt else ""
        lines.append(f"{mql_name}={val}{marker}")
    
    # Add non-optimized params with defaults
    lines.append("\n; === Non optimisés (valeurs par défaut) ===")
    lines.append("InpSymbol=")
    lines.append("InpBiasTFMinutes=60")
    lines.append("InpUseADX=true")
    lines.append("InpUseCooldown=true")
    lines.append("InpUseMSB=true")
    lines.append("InpMSB_BreakType=1")
    lines.append("InpMSB_Timing=0")
    lines.append("InpUseRetournement=true")
    lines.append("InpUseRegime=true")
    lines.append("InpUseCircuitBreaker=true")
    lines.append("InpSLTP_Mode=0")
    lines.append("InpSL_Points=5000")
    lines.append("InpTP_Points=7500")
    lines.append("InpLotMode=1")
    lines.append("InpLotSize=0.01")
    lines.append("InpLotMin=0.01")
    lines.append("InpLotMax=1.00")
    lines.append("InpUseAdaptiveRisk=true")
    lines.append("InpAR_LossThreshold=4")
    lines.append("InpAR_ReduceFactor=0.25")
    lines.append("InpAR_MaxBoost=3.0")
    lines.append("InpUseBreakeven=true")
    lines.append("InpMaxTrades=3")
    lines.append("InpMagicNumber=202602")
    lines.append("InpSlippage=10")
    lines.append("InpUseTimeClose=true")
    lines.append("InpUseTimeFilter=true")
    lines.append("InpUseWindow1=true")
    lines.append("InpW1_Start=7")
    lines.append("InpW1_End=12")
    lines.append("InpUseWindow2=true")
    lines.append("InpW2_Start=14")
    lines.append("InpW2_End=20")
    
    return "\n".join(lines)

set_content = generate_set_file(best_params, opt_results)

with open('HTF_Gold_EA_v4.3_optimized.set', 'w') as f:
    f.write(set_content)

print("✅ Fichier .set optimisé sauvegardé: HTF_Gold_EA_v4.3_optimized.set")
print("\nPour utiliser dans MT5:")
print("1. Copie le fichier dans MQL5/Presets/")
print("2. Strategy Tester → Inputs → Load → HTF_Gold_EA_v4.3_optimized.set")
