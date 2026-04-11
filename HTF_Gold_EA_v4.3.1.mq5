//+------------------------------------------------------------------+
//|                                          HTF_Gold_EA_v4.3.1.mq5 |
//|  ██╗  ██╗████████╗███████╗     ██████╗  ██████╗ ██╗     ██████╗ |
//|  ██║  ██║╚══██╔══╝██╔════╝    ██╔════╝ ██╔═══██╗██║     ██╔══██╗|
//|  ███████║   ██║   █████╗      ██║  ███╗██║   ██║██║     ██║  ██║|
//|  ██╔══██║   ██║   ██╔══╝      ██║   ██║██║   ██║██║     ██║  ██║|
//|  ██║  ██║   ██║   ██║         ╚██████╔╝╚██████╔╝███████╗██████╔╝|
//|  ╚═╝  ╚═╝   ╚═╝   ╚═╝          ╚═════╝  ╚═════╝ ╚══════╝╚═════╝ |
//+------------------------------------------------------------------+
//| Stratégie : MER (Market Entry Rules)                            |
//|   Couche 1 : Velocity Spike (VSP) + Spike Cooldown             |
//|   Couche 2 : Micro Structure Break (MSB)                       |
//|   Biais    : Double SuperTrend (TF variable, minutes)          |
//|   SL/TP    : ATR dynamique  OU  Points fixes                   |
//|   Filtres  : ADX (TF variable) · Spread min · Horaire London/NY|
//|              FILTRE RETOURNEMENT (HL/HH intact)                |
//|              FILTRE RÉGIME DE MARCHÉ (v4.3)                    |
//|              CIRCUIT BREAKER DRAWDOWN (v4.3)                   |
//|              SCALE-IN PROGRESSIF (v4.3.1)                      |
//|              TRAILING STOP ADAPTATIF CHANDELIER (v4.3.1)       |
//|   Lot      : Fixe  OU  % Risque + Risque Adaptatif             |
//+------------------------------------------------------------------+
#property copyright "HTF Gold EA v4.3.1 - ScaleIn + ChandelierTrail + Regime + CircuitBreaker"
#property version   "4.31"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

//+------------------------------------------------------------------+
//| ENUMS                                                            |
//+------------------------------------------------------------------+
enum ENUM_SLTP_MODE
{
   SLTP_MODE_ATR    = 0,  // ATR (dynamique)
   SLTP_MODE_POINTS = 1   // Points fixes (1000 pts = 10$ sur XAUUSDm 0.01)
};

enum ENUM_MSB_BREAK
{
   MSB_BREAK_CLOSE  = 0,  // Close au-delà du swing (strict)
   MSB_BREAK_TOUCH  = 1,  // High/Low au-delà du swing (toucher)
   MSB_BREAK_BODY   = 2   // Close au-delà du wick du swing (body+wick)
};

enum ENUM_MSB_TIMING
{
   MSB_TIMING_BEFORE = 0, // MSB avant le spike
   MSB_TIMING_AFTER  = 1  // MSB après le spike
};

enum ENUM_LOT_MODE
{
   LOT_MODE_FIXED   = 0,  // Lot fixe
   LOT_MODE_PERCENT = 1   // % du capital (risque par trade)
};

// --- v4.3 : Régime de marché
enum ENUM_MARKET_REGIME
{
   REGIME_STRONG_TREND = 0,  // Tendance forte   → trader normalement
   REGIME_WEAK_TREND   = 1,  // Tendance faible   → risque réduit
   REGIME_RANGE        = 2,  // Range             → pas de trade
   REGIME_VOLATILE     = 3,  // Volatilité extrême → pas de trade
   REGIME_CHOPPY       = 4   // Marché erratique  → pas de trade
};

//+------------------------------------------------------------------+
//| INPUTS                                                           |
//+------------------------------------------------------------------+
input group "=== SYMBOL ==="
input string            InpSymbol           = "";            // Symbol (vide = chart actuel)

input group "=== TIMEFRAME BIAIS (minutes) ==="
input int               InpBiasTFMinutes    = 60;            // Timeframe biais en minutes (1,5,15,30,60,240,1440,10080)

input group "=== FILTRE SUPERTREND BIAIS ==="
input int               InpST1_Period       = 7;             // [ST1] Période ATR
input double            InpST1_Multiplier   = 0.4;           // [ST1] Multiplicateur
input int               InpST2_Period       = 12;            // [ST2] Période ATR
input double            InpST2_Multiplier   = 1.2;           // [ST2] Multiplicateur

input group "=== FILTRE ADX BIAIS ==="
input bool              InpUseADX           = true;          // Activer filtre ADX (TF biais)
input int               InpADX_Period       = 12;            // Période ADX
input double            InpADX_MinLevel     = 23.0;          // ADX minimum (tendance > range)

input group "=== SL MINIMUM SPREAD ==="
input bool              InpUseSpreadSL      = false;         // Activer SL minimum basé sur spread
input int               InpSpread_Buffer    = 50;            // Buffer sécurité en points (spread + X pts)

input group "=== COUCHE 1 : VELOCITY SPIKE (VSP) ==="
input int               InpVSP_ATR_Period   = 15;            // ATR Period pour spike
input double            InpVSP_Spike_Multi  = 1.6;           // Corps bougie > X * ATR = Spike
input bool              InpVSP_NeedConfirm  = true;          // Attendre bougie confirmation
input int               InpVSP_LookBack     = 6;             // Chercher spike dans X bougies passées
input bool              InpUseCooldown      = true;          // Activer filtre spike cooldown (intégré)
input int               InpCooldownBars     = 4;             // Bougies M1 mini entre 2 spikes (même direction)

input group "=== COUCHE 2 : MICRO STRUCTURE BREAK (MSB) ==="
input bool              InpUseMSB           = true;          // Activer Couche 2 MSB
input int               InpMSB_SwingBars    = 16;            // Bougies pour détecter swing high/low
input ENUM_MSB_BREAK    InpMSB_BreakType    = MSB_BREAK_TOUCH; // Condition de cassure
input ENUM_MSB_TIMING   InpMSB_Timing       = MSB_TIMING_BEFORE; // MSB avant ou après le spike

input group "=== FILTRE RETOURNEMENT ==="
input bool              InpUseRetournement  = true;          // Activer filtre anti-retournement
input int               InpRT_SwingBars     = 13;            // Bougies pour détecter dernier swing HL/HH
input int               InpRT_LookBack      = 16;            // Fenêtre de recherche HL/HH (bougies avant le spike)
input double            InpRT_Buffer_ATR    = 0.5;           // Buffer en multiple de ATR (marge de sécurité)

input group "=== FILTRE RÉGIME DE MARCHÉ (v4.3) ==="
input bool              InpUseRegime        = false;          // Activer filtre régime de marché
input int               InpRegime_ADXPeriod = 14;            // [Regime] Période ADX pour détection
input int               InpRegime_ATRPeriod = 14;            // [Regime] Période ATR pour volatilité
input int               InpRegime_ATRWindow = 20;            // [Regime] Fenêtre moyenne ATR (bougies H1)
input double            InpRegime_ADXStrong = 30.0;          // [Regime] ADX seuil tendance forte
input double            InpRegime_ADXWeak   = 23.0;          // [Regime] ADX seuil tendance faible (en dessous = range)
input double            InpRegime_ADXChoppy = 18.0;          // [Regime] ADX seuil marché erratique
input double            InpRegime_VolRatioMax = 2.0;         // [Regime] Ratio ATR actuel/moyen max (au-delà = volatile)
input double            InpRegime_EfficiencyStrong = 0.5;    // [Regime] Efficiency ratio min pour tendance forte
input double            InpRegime_EfficiencyWeak   = 0.3;    // [Regime] Efficiency ratio min pour tendance faible
input double            InpRegime_EfficiencyChoppy = 0.2;    // [Regime] Efficiency ratio max pour erratique
input int               InpRegime_EffLookback = 20;          // [Regime] Bougies H1 pour calcul efficiency
input double            InpRegime_WeakRiskFactor = 0.5;      // [Regime] Facteur de réduction du risque en weak trend

input group "=== CIRCUIT BREAKER DRAWDOWN (v4.3) ==="
input bool              InpUseCircuitBreaker = false;          // Activer circuit breaker
input double            InpCB_MaxDrawdownPct = 5.0;           // [CB] Drawdown max en % depuis le pic d'équité
input int               InpCB_CooldownMinutes = 120;          // [CB] Minutes de pause après déclenchement
input int               InpCB_LossStreakLimit = 6;            // [CB] Nombre max de pertes consécutives
input bool              InpCB_AlertOnly      = false;         // [CB] Mode alerte seulement (ne bloque pas)

input group "=== MODE SL/TP ==="
input ENUM_SLTP_MODE    InpSLTP_Mode        = SLTP_MODE_ATR; // Mode calcul SL/TP
input int               InpATR_SL_Period    = 13;            // [ATR] Période ATR pour SL
input double            InpATR_SL_Multi     = 1.5;           // [ATR] Multiplicateur SL
input double            InpRR_Ratio         = 2.1;           // [ATR/PTS] Risk:Reward TP
input int               InpSL_Points        = 5000;          // [PTS] SL en points (5000 pts = 5$)
input int               InpTP_Points        = 7500;          // [PTS] TP en points (7500 pts = 7.5$)

input group "=== GESTION DU LOT ==="
input ENUM_LOT_MODE     InpLotMode          = LOT_MODE_PERCENT; // Mode lot (Fixe / % Risque)
input double            InpLotSize          = 0.01;          // [FIXE] Lot fixe
input double            InpRiskPercent      = 1.0;           // [%] Risque par trade (% capital)
input double            InpLotMin           = 0.01;          // [%] Lot minimum autorisé
input double            InpLotMax           = 1.00;          // [%] Lot maximum autorisé

input group "=== RISQUE ADAPTATIF ==="
input bool              InpUseAdaptiveRisk  = true;          // Activer gestion risque adaptative
input int               InpAR_LossThreshold = 3;             // Nb pertes consécutives → réduction
input double            InpAR_ReduceFactor  = 0.5;          // Facteur réduction (ex: 0.5 = moitié)
input double            InpAR_MaxBoost      = 2.5;           // Boost max après gains (ex: 1.25 = +25%)

input group "=== BREAKEVEN ==="
input bool              InpUseBreakeven     = true;          // Activer Breakeven
input double            InpBE_Trigger_RR    = 0.7;           // Déclencher BE à X * TP dist

input group "=== SCALE-IN PROGRESSIF (v4.3.1) ==="
input bool              InpUseScaleIn       = false;          // Activer scale-in progressif
input int               InpSIN_Levels       = 3;             // Nombre de niveaux de scale-in (max 5)
input double            InpSIN_SpacingATR   = 0.8;           // Espacement en multiple d'ATR entre niveaux
input double            InpSIN_LotPct       = 50.0;          // % du lot initial pour chaque niveau (ex: 50 = moitié)
input int               InpSIN_ATRPeriod    = 14;            // Période ATR pour calcul espacement
input int               InpSIN_MinBars      = 5;             // Bougies M1 minimum entre chaque niveau

input group "=== TRAILING STOP ADAPTATIF (v4.3.1) ==="
input bool              InpUseTrailing      = false;          // Activer trailing stop adaptatif
input int               InpTrail_ATRPeriod  = 14;            // Période ATR pour trailing
input double            InpTrail_Multi      = 3.0;           // Multiplicateur ATR (Chandelier Exit)
input int               InpTrail_Lookback   = 10;            // Bougies M1 pour le high/low le plus extrême
input double            InpTrail_TightenMult = 0.7;          // Multiplicateur serré après BE (ex: 3.0×0.7=2.1)
input double            InpTrail_MinProfitATR = 0.5;         // Profit minimum en ATR avant activation trail

input group "=== TRADE MANAGEMENT ==="
input int               InpMaxTrades        = 3;             // Max trades simultanés
input int               InpMagicNumber      = 202602;        // Magic Number
input int               InpSlippage         = 10;            // Slippage (points)

input group "=== FERMETURE PAR TEMPS ==="
input bool              InpUseTimeClose     = true;          // Activer fermeture par temps
input int               InpMaxMinutes       = 24;            // Fermer après X minutes

input group "=== FILTRE HORAIRE ==="
input bool              InpUseTimeFilter    = true;          // Filtre horaire actif
input bool              InpUseWindow1       = true;          // Fenêtre 1 active (London)
input int               InpW1_Start         = 7;             // [W1] Heure début London
input int               InpW1_End           = 12;            // [W1] Heure fin London (exclu)
input bool              InpUseWindow2       = true;          // Fenêtre 2 active (NY)
input int               InpW2_Start         = 14;            // [W2] Heure début NY
input int               InpW2_End           = 20;            // [W2] Heure fin NY (exclu)
input int               InpSessionCooldownMin = 15;          // Minutes à ignorer après ouverture session

//+------------------------------------------------------------------+
//| STRUCTURES                                                       |
//+------------------------------------------------------------------+
struct SuperTrendData
{
   double upper;
   double lower;
   int    direction;
};

struct VSP_Result
{
   bool   detected;
   int    direction;
   int    barIndex;
   double spikeSize;
};

struct MSB_Result
{
   bool   detected;
   double swingLevel;
   int    barIndex;
};

struct RegimeResult
{
   ENUM_MARKET_REGIME regime;
   double adxValue;
   double volatilityRatio;
   double efficiencyRatio;
   string description;
};

//+------------------------------------------------------------------+
//| GLOBALS                                                          |
//+------------------------------------------------------------------+
CTrade         trade;
CPositionInfo  posInfo;
COrderInfo     orderInfo;
string         g_symbol;
int            g_digits;
double         g_point;
ENUM_TIMEFRAMES g_biasTF;

// Spike Cooldown
datetime       g_lastSpikeTime[2];

// Risque adaptatif
int            g_consecutiveLosses = 0;
int            g_consecutiveWins   = 0;
double         g_currentRiskMult   = 1.0;

// Circuit Breaker
double         g_peakEquity          = 0;
datetime       g_circuitBreakerTime  = 0;
int            g_cbConsecLosses      = 0;
bool           g_cbActive            = false;
string         g_cbReason            = "";

// Régime de marché
ENUM_MARKET_REGIME g_currentRegime   = REGIME_STRONG_TREND;
string         g_regimeDescription   = "";
double         g_regimeRiskFactor    = 1.0;

// Scale-In Progressif
ulong          g_parentTicket        = 0;
double         g_parentLot           = 0;
ulong          g_scaleInTickets[5];
int            g_scaleInCount        = 0;
double         g_scaleInEntryPrice   = 0;
int            g_scaleInDirection    = 0;
datetime       g_lastScaleInTime     = 0;

// Handles indicateurs
int            m1_atr_sl_handle    = INVALID_HANDLE;
int            m1_atr_vsp_handle   = INVALID_HANDLE;
int            bias_adx_handle     = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| CONVERTIR MINUTES → ENUM_TIMEFRAMES                              |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES MinutesToTimeframe(int minutes)
{
   switch(minutes)
   {
      case 1:     return PERIOD_M1;
      case 2:     return PERIOD_M2;
      case 3:     return PERIOD_M3;
      case 4:     return PERIOD_M4;
      case 5:     return PERIOD_M5;
      case 6:     return PERIOD_M6;
      case 10:    return PERIOD_M10;
      case 12:    return PERIOD_M12;
      case 15:    return PERIOD_M15;
      case 20:    return PERIOD_M20;
      case 30:    return PERIOD_M30;
      case 60:    return PERIOD_H1;
      case 120:   return PERIOD_H2;
      case 180:   return PERIOD_H3;
      case 240:   return PERIOD_H4;
      case 360:   return PERIOD_H6;
      case 480:   return PERIOD_H8;
      case 720:   return PERIOD_H12;
      case 1440:  return PERIOD_D1;
      case 10080: return PERIOD_W1;
      case 43200: return PERIOD_MN1;
      default:
         PrintFormat("[ERREUR] Timeframe %d minutes non supporté — fallback H1", minutes);
         return PERIOD_H1;
   }
}

//+------------------------------------------------------------------+
//| INIT                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   g_digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   g_point  = SymbolInfoDouble(g_symbol, SYMBOL_POINT);
   g_biasTF = MinutesToTimeframe(InpBiasTFMinutes);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   g_lastSpikeTime[0] = 0;
   g_lastSpikeTime[1] = 0;

   g_consecutiveLosses = 0;
   g_consecutiveWins   = 0;
   g_currentRiskMult   = 1.0;

   g_peakEquity         = AccountInfoDouble(ACCOUNT_BALANCE);
   g_circuitBreakerTime = 0;
   g_cbConsecLosses     = 0;
   g_cbActive           = false;
   g_cbReason           = "";

   g_currentRegime      = REGIME_STRONG_TREND;
   g_regimeDescription  = "";
   g_regimeRiskFactor   = 1.0;

   g_parentTicket    = 0;
   g_parentLot       = 0;
   g_scaleInCount    = 0;
   g_scaleInEntryPrice = 0;
   g_scaleInDirection  = 0;
   g_lastScaleInTime   = 0;
   for(int i = 0; i < 5; i++) g_scaleInTickets[i] = 0;

   m1_atr_sl_handle  = iATR(g_symbol, PERIOD_M1, InpATR_SL_Period);
   m1_atr_vsp_handle = iATR(g_symbol, PERIOD_M1, InpVSP_ATR_Period);
   bias_adx_handle   = iADX(g_symbol, g_biasTF, InpADX_Period);

   if(m1_atr_sl_handle == INVALID_HANDLE || m1_atr_vsp_handle == INVALID_HANDLE ||
      bias_adx_handle == INVALID_HANDLE)
   {
      Print("ERREUR: Création handles échouée.");
      return INIT_FAILED;
   }

   string sltpName = (InpSLTP_Mode == SLTP_MODE_ATR) ?
                     StringFormat("ATR(%d)×%.1f | RR=%.1f", InpATR_SL_Period, InpATR_SL_Multi, InpRR_Ratio) :
                     StringFormat("Points | SL=%d pts (%.2f$) | TP=%d pts (%.2f$)",
                                  InpSL_Points, InpSL_Points * InpLotSize * 0.1,
                                  InpTP_Points, InpTP_Points * InpLotSize * 0.1);

   PrintFormat("═══════════════════════════════════════════════════════════════");
   PrintFormat("HTF Gold EA v4.3.1 | %s | Bias TF=%dmin (%s)", g_symbol, InpBiasTFMinutes, EnumToString(g_biasTF));
   PrintFormat("DoubleST(%d×%.1f / %d×%.1f) | SL/TP: %s",
               InpST1_Period, InpST1_Multiplier, InpST2_Period, InpST2_Multiplier, sltpName);
   PrintFormat("MSB: %s | Cooldown: %d | AdaptRisk: %s | SessCooldown: %dmin",
               InpUseMSB ? StringFormat("ON SwingBars=%d Break=%d Timing=%d", InpMSB_SwingBars, InpMSB_BreakType, InpMSB_Timing) : "OFF",
               InpUseCooldown ? InpCooldownBars : 0,
               InpUseAdaptiveRisk ? StringFormat("ON seuil=%d fact=%.2f boost=%.2f", InpAR_LossThreshold, InpAR_ReduceFactor, InpAR_MaxBoost) : "OFF",
               InpSessionCooldownMin);
   PrintFormat("Retournement: %s", InpUseRetournement ? StringFormat("ON SwingBars=%d LookBack=%d Buffer=%.1f×ATR", InpRT_SwingBars, InpRT_LookBack, InpRT_Buffer_ATR) : "OFF");
   PrintFormat("[v4.3] Régime: %s | ADX=%.0f/%.0f/%.0f | VolRatioMax=%.1f | Eff=%.2f/%.2f/%.2f | WeakRisk=%.0f%%",
               InpUseRegime ? "ON" : "OFF",
               InpRegime_ADXStrong, InpRegime_ADXWeak, InpRegime_ADXChoppy,
               InpRegime_VolRatioMax,
               InpRegime_EfficiencyStrong, InpRegime_EfficiencyWeak, InpRegime_EfficiencyChoppy,
               InpRegime_WeakRiskFactor * 100);
   PrintFormat("[v4.3] CircuitBreaker: %s | MaxDD=%.1f%% | Cooldown=%dmin | LossStreak=%d | AlertOnly=%s",
               InpUseCircuitBreaker ? "ON" : "OFF",
               InpCB_MaxDrawdownPct, InpCB_CooldownMinutes, InpCB_LossStreakLimit,
               InpCB_AlertOnly ? "OUI" : "NON");
   PrintFormat("[v4.3.1] ScaleIn: %s | Niveaux=%d | Espacement=%.1f×ATR | Lot=%.0f%% | MinBars=%d",
               InpUseScaleIn ? "ON" : "OFF",
               InpSIN_Levels, InpSIN_SpacingATR, InpSIN_LotPct, InpSIN_MinBars);
   PrintFormat("[v4.3.1] Trailing: %s | ATR(%d)×%.1f | Lookback=%d | Tighten=%.1f× | MinProfit=%.1f×ATR",
               InpUseTrailing ? "ON" : "OFF",
               InpTrail_ATRPeriod, InpTrail_Multi, InpTrail_Lookback,
               InpTrail_TightenMult, InpTrail_MinProfitATR);
   PrintFormat("═══════════════════════════════════════════════════════════════");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| DEINIT                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(m1_atr_sl_handle);
   IndicatorRelease(m1_atr_vsp_handle);
   IndicatorRelease(bias_adx_handle);
}

//+------------------------------------------------------------------+
//| TICK                                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // --- Trailing Stop : vérifier TOUS les ticks (pas seulement nouvelle bougie) ---
   if(InpUseTrailing)
      CheckTrailingStop();

   // --- Scale-In : vérifier positions ouvertes et nettoyer si parent fermé ---
   if(InpUseScaleIn)
      CheckScaleInCleanup();

   // Si scale-in désactivé mais ordres en attente → annuler
   if(!InpUseScaleIn && g_parentTicket > 0)
   {
      CancelAllScaleInOrders();
      g_parentTicket = 0;
      g_scaleInCount = 0;
   }

   // --- Nouvelle bougie M1 seulement pour les signaux ---
   static datetime lastBar = 0;
   datetime currentBar = iTime(g_symbol, PERIOD_M1, 0);
   if(currentBar == lastBar) return;
   lastBar = currentBar;

   UpdateAdaptiveRisk();

   // Circuit Breaker
   if(InpUseCircuitBreaker)
   {
      UpdateCircuitBreaker();
      if(IsCircuitBreakerActive())
      {
         if(!InpCB_AlertOnly) return;
      }
   }

   if(InpUseTimeFilter && !IsTradeHour()) return;
   if(InpUseBreakeven) CheckBreakeven();
   if(InpUseTimeClose) CheckTimeClose();
   if(CountOpenTrades() >= InpMaxTrades) return;

   double m1_atr_sl[], m1_atr_vsp[];
   ArraySetAsSeries(m1_atr_sl,  true);
   ArraySetAsSeries(m1_atr_vsp, true);
   if(CopyBuffer(m1_atr_sl_handle,  0, 0, 5, m1_atr_sl)                    < 5) return;
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, InpVSP_LookBack + 3, m1_atr_vsp) < InpVSP_LookBack + 3) return;

   int bias = GetBias_SuperTrend();
   if(bias == 0) return;

   if(InpUseADX)
   {
      double adxBuf[];
      ArraySetAsSeries(adxBuf, true);
      if(CopyBuffer(bias_adx_handle, 0, 0, 3, adxBuf) < 3) return;
      if(adxBuf[1] < InpADX_MinLevel) return;
   }

   double effectiveRiskFactor = 1.0;
   if(InpUseRegime)
   {
      RegimeResult regime = DetectMarketRegime();
      g_currentRegime     = regime.regime;
      g_regimeDescription = regime.description;

      if(regime.regime == REGIME_RANGE || regime.regime == REGIME_CHOPPY || regime.regime == REGIME_VOLATILE)
      {
         PrintFormat("[RÉGIME BLOQUÉ] %s → pas de trade", regime.description);
         return;
      }
      if(regime.regime == REGIME_WEAK_TREND)
         effectiveRiskFactor = InpRegime_WeakRiskFactor;
   }

   VSP_Result vsp = DetectVelocitySpike();
   if(!vsp.detected) return;

   if(vsp.direction != -bias)
   {
      PrintFormat("[VSP BLOQUÉ] spike=%d biais=%d → contre-biais interdit", vsp.direction, bias);
      return;
   }

   int signal = bias;

   if(InpVSP_NeedConfirm)
   {
      double closeC = iClose(g_symbol, PERIOD_M1, 1);
      double openC  = iOpen(g_symbol,  PERIOD_M1, 1);
      if(signal ==  1 && closeC <= openC) return;
      if(signal == -1 && closeC >= openC) return;
   }

   if(InpUseMSB)
   {
      MSB_Result msb = DetectMSB(signal, vsp.barIndex);
      if(!msb.detected) return;
   }

   if(InpUseRetournement)
   {
      if(!IsPullbackStructure(signal, vsp.barIndex, m1_atr_vsp)) return;
   }

   double slDist = 0, tpDist = 0;
   if(InpSLTP_Mode == SLTP_MODE_ATR)
   {
      double atr = m1_atr_sl[1];
      if(atr <= 0) return;
      slDist = atr * InpATR_SL_Multi;
      tpDist = slDist * InpRR_Ratio;
   }
   else
   {
      slDist = InpSL_Points * g_point;
      tpDist = InpTP_Points * g_point;
   }

   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);

   double lotSize = CalcLotSize(slDist, effectiveRiskFactor);

   if(InpUseSpreadSL)
   {
      double spreadPoints = (double)SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
      double slMinDist    = (spreadPoints + InpSpread_Buffer) * g_point;
      if(slDist < slMinDist) return;
   }

   // --- Exécution BUY ---
   if(signal == 1 && !HasOpenTrade(POSITION_TYPE_BUY))
   {
      double sl = NormalizeDouble(ask - slDist, g_digits);
      double tp = NormalizeDouble(ask + tpDist, g_digits);

      if(trade.Buy(lotSize, g_symbol, ask, sl, tp,
                   StringFormat("HTF-v4.3.1|BUY|%s|lot=%.2f|spike@%d",
                                (InpSLTP_Mode==SLTP_MODE_ATR?"ATR":"PTS"), lotSize, vsp.barIndex)))
      {
         ulong ticket = trade.ResultOrder();
         PrintSignal("BUY", ask, sl, tp, MathAbs(ask-sl), MathAbs(tp-ask), vsp, lotSize);

         if(InpUseScaleIn && ticket > 0)
            SetupScaleIn(ticket, 1, ask, lotSize, m1_atr_sl[1]);
      }
   }
   // --- Exécution SELL ---
   else if(signal == -1 && !HasOpenTrade(POSITION_TYPE_SELL))
   {
      double sl = NormalizeDouble(bid + slDist, g_digits);
      double tp = NormalizeDouble(bid - tpDist, g_digits);

      if(trade.Sell(lotSize, g_symbol, bid, sl, tp,
                    StringFormat("HTF-v4.3.1|SELL|%s|lot=%.2f|spike@%d",
                                 (InpSLTP_Mode==SLTP_MODE_ATR?"ATR":"PTS"), lotSize, vsp.barIndex)))
      {
         ulong ticket = trade.ResultOrder();
         PrintSignal("SELL", bid, sl, tp, MathAbs(bid-sl), MathAbs(bid-tp), vsp, lotSize);

         if(InpUseScaleIn && ticket > 0)
            SetupScaleIn(ticket, -1, bid, lotSize, m1_atr_sl[1]);
      }
   }
}

//+------------------------------------------------------------------+
//| DÉTECTION RÉGIME DE MARCHÉ                                      |
//+------------------------------------------------------------------+
RegimeResult DetectMarketRegime()
{
   RegimeResult result;
   result.regime = REGIME_STRONG_TREND;
   result.adxValue = 0; result.volatilityRatio = 1.0;
   result.efficiencyRatio = 0.5; result.description = "";

   double adxBuf[];
   ArraySetAsSeries(adxBuf, true);
   if(CopyBuffer(bias_adx_handle, 0, 0, 3, adxBuf) < 3)
   { result.description = "ADX indisponible → fallback STRONG_TREND"; return result; }
   result.adxValue = adxBuf[1];

   int atrH1Handle = iATR(g_symbol, PERIOD_H1, InpRegime_ATRPeriod);
   if(atrH1Handle == INVALID_HANDLE)
   { result.description = "ATR H1 indisponible → fallback STRONG_TREND"; return result; }

   double atrH1Buf[];
   ArraySetAsSeries(atrH1Buf, true);
   int atrCopied = CopyBuffer(atrH1Handle, 0, 0, InpRegime_ATRWindow + 2, atrH1Buf);
   IndicatorRelease(atrH1Handle);
   if(atrCopied < InpRegime_ATRWindow + 2)
   { result.description = "ATR H1 insuffisant → fallback STRONG_TREND"; return result; }

   double currentATR = atrH1Buf[1];
   double sumATR = 0;
   for(int i = 1; i <= InpRegime_ATRWindow; i++) sumATR += atrH1Buf[i];
   double avgATR = sumATR / InpRegime_ATRWindow;
   result.volatilityRatio = (avgATR > 0) ? currentATR / avgATR : 1.0;

   double closeH1[];
   ArraySetAsSeries(closeH1, true);
   if(CopyClose(g_symbol, PERIOD_H1, 0, InpRegime_EffLookback + 2, closeH1) < InpRegime_EffLookback + 2)
   { result.description = "Close H1 insuffisant → fallback STRONG_TREND"; return result; }

   double netMove = MathAbs(closeH1[1] - closeH1[InpRegime_EffLookback + 1]);
   double totalMove = 0;
   for(int i = 1; i <= InpRegime_EffLookback; i++)
      totalMove += MathAbs(closeH1[i] - closeH1[i + 1]);
   result.efficiencyRatio = (totalMove > 0) ? netMove / totalMove : 0;

   if(result.volatilityRatio > InpRegime_VolRatioMax)
   {
      result.regime = REGIME_VOLATILE;
      result.description = StringFormat("VOLATILE | ADX=%.1f Eff=%.2f VolR=%.2f",
                                        result.adxValue, result.efficiencyRatio, result.volatilityRatio);
      return result;
   }

   if(result.adxValue < InpRegime_ADXChoppy && result.efficiencyRatio < InpRegime_EfficiencyChoppy)
   {
      result.regime = REGIME_CHOPPY;
      result.description = StringFormat("CHOPPY | ADX=%.1f (<%.0f) + Eff=%.2f (<%.2f) → erratique",
                                        result.adxValue, InpRegime_ADXChoppy,
                                        result.efficiencyRatio, InpRegime_EfficiencyChoppy);
      return result;
   }

   if(result.adxValue < InpRegime_ADXWeak && result.efficiencyRatio < InpRegime_EfficiencyWeak)
   {
      result.regime = REGIME_RANGE;
      result.description = StringFormat("RANGE | ADX=%.1f (<%.0f) + Eff=%.2f (<%.2f) → range confirmé",
                                        result.adxValue, InpRegime_ADXWeak,
                                        result.efficiencyRatio, InpRegime_EfficiencyWeak);
      return result;
   }

   if(result.adxValue >= InpRegime_ADXStrong && result.efficiencyRatio >= InpRegime_EfficiencyStrong)
   {
      result.regime = REGIME_STRONG_TREND;
      result.description = StringFormat("STRONG_TREND | ADX=%.1f (≥%.0f) + Eff=%.2f (≥%.2f)",
                                        result.adxValue, InpRegime_ADXStrong,
                                        result.efficiencyRatio, InpRegime_EfficiencyStrong);
      return result;
   }

   result.regime = REGIME_WEAK_TREND;
   result.description = StringFormat("WEAK_TREND | ADX=%.1f | Eff=%.2f | VolR=%.2f",
                                     result.adxValue, result.efficiencyRatio, result.volatilityRatio);
   return result;
}

//+------------------------------------------------------------------+
//| CIRCUIT BREAKER — Mise à jour                                    |
//+------------------------------------------------------------------+
void UpdateCircuitBreaker()
{
   if(!InpUseCircuitBreaker) return;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity > g_peakEquity) g_peakEquity = equity;
   if(g_peakEquity == 0) g_peakEquity = AccountInfoDouble(ACCOUNT_BALANCE);

   if(!HistorySelect(TimeCurrent() - 7 * 24 * 3600, TimeCurrent())) return;

   int totalDeals = HistoryDealsTotal();
   if(totalDeals == 0) return;

   for(int i = totalDeals - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != g_symbol) continue;
      if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);

      static datetime g_lastCBDealTime = 0;
      datetime dealTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      if(dealTime == g_lastCBDealTime) break;
      g_lastCBDealTime = dealTime;

      if(profit < 0) { g_cbConsecLosses++; }
      else if(profit > 0) { g_cbConsecLosses = 0; }
      break;
   }
}

//+------------------------------------------------------------------+
//| CIRCUIT BREAKER — Vérification                                   |
//+------------------------------------------------------------------+
bool IsCircuitBreakerActive()
{
   if(!InpUseCircuitBreaker) return false;

   if(g_circuitBreakerTime > 0)
   {
      int elapsedMin = (int)((TimeCurrent() - g_circuitBreakerTime) / 60);
      if(elapsedMin < InpCB_CooldownMinutes)
      {
         if(!g_cbActive)
         { PrintFormat("[CB ACTIF] %s | Reprise dans %d min", g_cbReason, InpCB_CooldownMinutes - elapsedMin);
           g_cbActive = true; }
         return true;
      }
      else
      {
         PrintFormat("[CB] Cooldown terminé → reprise du trading | PeakEquity reset");
         g_circuitBreakerTime = 0; g_cbActive = false; g_cbReason = "";
         g_peakEquity = AccountInfoDouble(ACCOUNT_EQUITY); g_cbConsecLosses = 0;
      }
   }

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double ddPct = (g_peakEquity > 0) ? ((g_peakEquity - equity) / g_peakEquity) * 100.0 : 0;

   if(ddPct >= InpCB_MaxDrawdownPct)
   {
      g_circuitBreakerTime = TimeCurrent();
      g_cbReason = StringFormat("DRAWDOWN %.2f%% ≥ %.2f%% (pic=%.2f$ → equity=%.2f$)",
                                ddPct, InpCB_MaxDrawdownPct, g_peakEquity, equity);
      g_cbActive = true;
      PrintFormat("[CB DÉCLENCHÉ] %s → pause %d min", g_cbReason, InpCB_CooldownMinutes);
      return true;
   }

   if(g_cbConsecLosses >= InpCB_LossStreakLimit)
   {
      g_circuitBreakerTime = TimeCurrent();
      g_cbReason = StringFormat("Pertes consécutives: %d ≥ limite: %d", g_cbConsecLosses, InpCB_LossStreakLimit);
      g_cbActive = true;
      PrintFormat("[CB DÉCLENCHÉ] %s → pause %d min", g_cbReason, InpCB_CooldownMinutes);
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| VELOCITY SPIKE AVEC COOLDOWN                                     |
//+------------------------------------------------------------------+
VSP_Result DetectVelocitySpike()
{
   VSP_Result result;
   result.detected = false; result.direction = 0;
   result.barIndex = 0; result.spikeSize = 0;

   int barsToCheck = InpVSP_LookBack + 2;
   double open[], close[];
   ArraySetAsSeries(open, true); ArraySetAsSeries(close, true);

   if(CopyOpen(g_symbol, PERIOD_M1, 0, barsToCheck, open) < barsToCheck) return result;
   if(CopyClose(g_symbol, PERIOD_M1, 0, barsToCheck, close) < barsToCheck) return result;

   double m1_atr_vsp[];
   ArraySetAsSeries(m1_atr_vsp, true);
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, barsToCheck, m1_atr_vsp) < barsToCheck) return result;

   for(int i = 2; i <= InpVSP_LookBack + 1; i++)
   {
      double bodySize = MathAbs(close[i] - open[i]);
      double threshold = m1_atr_vsp[i] * InpVSP_Spike_Multi;

      if(bodySize >= threshold)
      {
         int dir = (close[i] > open[i]) ? 1 : -1;

         if(InpUseCooldown)
         {
            int dirIdx = (dir == 1) ? 0 : 1;
            int barsElapsed = (int)((TimeCurrent() - g_lastSpikeTime[dirIdx]) / 60);
            if(g_lastSpikeTime[dirIdx] > 0 && barsElapsed < InpCooldownBars)
            {
               PrintFormat("[COOLDOWN] Spike %s bar[%d] bloqué — %d bougie(s) < seuil %d",
                           (dir == 1 ? "BUY" : "SELL"), i, barsElapsed, InpCooldownBars);
               continue;
            }
         }

         result.detected = true; result.barIndex = i;
         result.spikeSize = bodySize; result.direction = dir;

         // Mettre à jour le timestamp du dernier spike
         int dirIdx = (dir == 1) ? 0 : 1;
         g_lastSpikeTime[dirIdx] = TimeCurrent();

         break;
      }
   }
   return result;
}

//+------------------------------------------------------------------+
//| MICRO STRUCTURE BREAK (MSB)                                      |
//+------------------------------------------------------------------+
MSB_Result DetectMSB(int signal, int spikeBarIndex)
{
   MSB_Result result;
   result.detected = false; result.swingLevel = 0.0; result.barIndex = 0;

   int totalBars = InpMSB_SwingBars + spikeBarIndex + 3;
   double highBuf[], lowBuf[], closeBuf[], openBuf[];
   ArraySetAsSeries(highBuf, true); ArraySetAsSeries(lowBuf, true);
   ArraySetAsSeries(closeBuf, true); ArraySetAsSeries(openBuf, true);

   if(CopyHigh(g_symbol, PERIOD_M1, 0, totalBars, highBuf) < totalBars) return result;
   if(CopyLow(g_symbol, PERIOD_M1, 0, totalBars, lowBuf) < totalBars) return result;
   if(CopyClose(g_symbol, PERIOD_M1, 0, totalBars, closeBuf) < totalBars) return result;
   if(CopyOpen(g_symbol, PERIOD_M1, 0, totalBars, openBuf) < totalBars) return result;

   if(InpMSB_Timing == MSB_TIMING_AFTER)
   {
      int zoneStart = spikeBarIndex + 1;
      int zoneEnd = spikeBarIndex + InpMSB_SwingBars;
      if(zoneEnd >= totalBars) return result;

      if(signal == 1)
      {
         double swingLow = lowBuf[zoneStart];
         for(int i = zoneStart + 1; i <= zoneEnd; i++) if(lowBuf[i] < swingLow) swingLow = lowBuf[i];
         bool broken = false;
         switch(InpMSB_BreakType)
         {
            case MSB_BREAK_CLOSE: broken = (closeBuf[1] > swingLow); break;
            case MSB_BREAK_TOUCH: broken = (highBuf[1] > swingLow); break;
            case MSB_BREAK_BODY: broken = (closeBuf[1] > swingLow && openBuf[1] > swingLow); break;
         }
         if(broken) { result.detected = true; result.swingLevel = swingLow; result.barIndex = 1; }
      }
      else if(signal == -1)
      {
         double swingHigh = highBuf[zoneStart];
         for(int i = zoneStart + 1; i <= zoneEnd; i++) if(highBuf[i] > swingHigh) swingHigh = highBuf[i];
         bool broken = false;
         switch(InpMSB_BreakType)
         {
            case MSB_BREAK_CLOSE: broken = (closeBuf[1] < swingHigh); break;
            case MSB_BREAK_TOUCH: broken = (lowBuf[1] < swingHigh); break;
            case MSB_BREAK_BODY: broken = (closeBuf[1] < swingHigh && openBuf[1] < swingHigh); break;
         }
         if(broken) { result.detected = true; result.swingLevel = swingHigh; result.barIndex = 1; }
      }
   }
   else // MSB_TIMING_BEFORE
   {
      if(spikeBarIndex < 2) return result;
      int zoneStart = 2, zoneEnd = spikeBarIndex - 1;
      if(zoneStart > zoneEnd) return result;
      int refStart = spikeBarIndex + 1, refEnd = spikeBarIndex + InpMSB_SwingBars;
      if(refEnd >= totalBars) return result;

      if(signal == 1)
      {
         double swingLow = lowBuf[refStart];
         for(int i = refStart + 1; i <= refEnd; i++) if(lowBuf[i] < swingLow) swingLow = lowBuf[i];
         for(int i = zoneEnd; i >= zoneStart; i--)
         {
            bool broken = false;
            switch(InpMSB_BreakType)
            {
               case MSB_BREAK_CLOSE: broken = (closeBuf[i] > swingLow); break;
               case MSB_BREAK_TOUCH: broken = (highBuf[i] > swingLow); break;
               case MSB_BREAK_BODY: broken = (closeBuf[i] > swingLow && openBuf[i] > swingLow); break;
            }
            if(broken) { result.detected = true; result.swingLevel = swingLow; result.barIndex = i; break; }
         }
      }
      else if(signal == -1)
      {
         double swingHigh = highBuf[refStart];
         for(int i = refStart + 1; i <= refEnd; i++) if(highBuf[i] > swingHigh) swingHigh = highBuf[i];
         for(int i = zoneEnd; i >= zoneStart; i--)
         {
            bool broken = false;
            switch(InpMSB_BreakType)
            {
               case MSB_BREAK_CLOSE: broken = (closeBuf[i] < swingHigh); break;
               case MSB_BREAK_TOUCH: broken = (lowBuf[i] < swingHigh); break;
               case MSB_BREAK_BODY: broken = (closeBuf[i] < swingHigh && openBuf[i] < swingHigh); break;
            }
            if(broken) { result.detected = true; result.swingLevel = swingHigh; result.barIndex = i; break; }
         }
      }
   }
   return result;
}

//+------------------------------------------------------------------+
//| BIAIS — DOUBLE SUPERTREND                                       |
//+------------------------------------------------------------------+
int GetBias_SuperTrend()
{
   int barsNeeded = MathMax(InpST1_Period, InpST2_Period) * 3 + 10;
   SuperTrendData st1 = CalcSuperTrend(g_biasTF, InpST1_Period, InpST1_Multiplier, barsNeeded);
   SuperTrendData st2 = CalcSuperTrend(g_biasTF, InpST2_Period, InpST2_Multiplier, barsNeeded);
   if(st1.direction ==  1 && st2.direction ==  1) return  1;
   if(st1.direction == -1 && st2.direction == -1) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| FILTRE RETOURNEMENT                                              |
//+------------------------------------------------------------------+
bool IsPullbackStructure(int signal, int spikeBarIndex, double &atrBuf[])
{
   int totalBars = InpRT_LookBack + spikeBarIndex + 3;
   double highBuf[], lowBuf[], closeBuf[], openBuf[];
   ArraySetAsSeries(highBuf, true); ArraySetAsSeries(lowBuf, true);
   ArraySetAsSeries(closeBuf, true); ArraySetAsSeries(openBuf, true);

   if(CopyHigh(g_symbol, PERIOD_M1, 0, totalBars, highBuf) < totalBars) return false;
   if(CopyLow(g_symbol, PERIOD_M1, 0, totalBars, lowBuf) < totalBars) return false;
   if(CopyClose(g_symbol, PERIOD_M1, 0, totalBars, closeBuf) < totalBars) return false;
   if(CopyOpen(g_symbol, PERIOD_M1, 0, totalBars, openBuf) < totalBars) return false;

   double buffer = atrBuf[spikeBarIndex] * InpRT_Buffer_ATR;

   if(signal == 1)
   {
      int refStart = spikeBarIndex + 1;
      int refEnd = spikeBarIndex + InpRT_SwingBars;
      if(refEnd >= totalBars) refEnd = totalBars - 1;
      double swingLow = lowBuf[refStart];
      for(int i = refStart + 1; i <= refEnd; i++) if(lowBuf[i] < swingLow) swingLow = lowBuf[i];
      if(lowBuf[spikeBarIndex] < swingLow - buffer) return false;
      if(lowBuf[1] < swingLow - buffer) return false;
      PrintFormat("[RETOURNEMENT OK] Swing low=%.5f intact (spike low=%.5f, buffer=%.5f)",
                  swingLow, lowBuf[spikeBarIndex], buffer);
      return true;
   }
   else if(signal == -1)
   {
      int refStart = spikeBarIndex + 1;
      int refEnd = spikeBarIndex + InpRT_SwingBars;
      if(refEnd >= totalBars) refEnd = totalBars - 1;
      double swingHigh = highBuf[refStart];
      for(int i = refStart + 1; i <= refEnd; i++) if(highBuf[i] > swingHigh) swingHigh = highBuf[i];
      if(highBuf[spikeBarIndex] > swingHigh + buffer) return false;
      if(highBuf[1] > swingHigh + buffer) return false;
      PrintFormat("[RETOURNEMENT OK] Swing high=%.5f intact (spike high=%.5f, buffer=%.5f)",
                  swingHigh, highBuf[spikeBarIndex], buffer);
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| CALCUL SUPERTREND NATIF                                          |
//+------------------------------------------------------------------+
SuperTrendData CalcSuperTrend(ENUM_TIMEFRAMES tf, int period, double multiplier, int bars)
{
   SuperTrendData result;
   result.direction = 0; result.upper = 0; result.lower = 0;

   int atrH = iATR(g_symbol, tf, period);
   if(atrH == INVALID_HANDLE) return result;

   double atrBuf[], highBuf[], lowBuf[], closeBuf[];
   ArraySetAsSeries(atrBuf, true); ArraySetAsSeries(highBuf, true);
   ArraySetAsSeries(lowBuf, true); ArraySetAsSeries(closeBuf, true);

   if(CopyBuffer(atrH, 0, 0, bars, atrBuf) < bars ||
      CopyHigh(g_symbol, tf, 0, bars, highBuf) < bars ||
      CopyLow(g_symbol, tf, 0, bars, lowBuf) < bars ||
      CopyClose(g_symbol, tf, 0, bars, closeBuf) < bars)
   { IndicatorRelease(atrH); return result; }
   IndicatorRelease(atrH);

   double upperBand[], lowerBand[];
   int dir[];
   ArrayResize(upperBand, bars); ArrayResize(lowerBand, bars); ArrayResize(dir, bars);

   for(int i = bars - 1; i >= 0; i--)
   {
      double hl2 = (highBuf[i] + lowBuf[i]) / 2.0;
      double bu = hl2 + multiplier * atrBuf[i];
      double bl = hl2 - multiplier * atrBuf[i];

      if(i == bars - 1) { upperBand[i] = bu; lowerBand[i] = bl; dir[i] = 1; }
      else
      {
         upperBand[i] = (bu < upperBand[i+1] || closeBuf[i+1] > upperBand[i+1]) ? bu : upperBand[i+1];
         lowerBand[i] = (bl > lowerBand[i+1] || closeBuf[i+1] < lowerBand[i+1]) ? bl : lowerBand[i+1];
         if(closeBuf[i] > upperBand[i]) dir[i] = 1;
         else if(closeBuf[i] < lowerBand[i]) dir[i] = -1;
         else dir[i] = dir[i+1];
      }
   }
   result.direction = dir[1]; result.upper = upperBand[1]; result.lower = lowerBand[1];
   return result;
}

//+------------------------------------------------------------------+
//| FERMETURE PAR TEMPS                                              |
//+------------------------------------------------------------------+
void CheckTimeClose()
{
   datetime now = TimeCurrent();
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != g_symbol || posInfo.Magic() != InpMagicNumber) continue;
      int minutesOpen = (int)((now - posInfo.Time()) / 60);
      if(minutesOpen >= InpMaxMinutes)
      {
         if(trade.PositionClose(posInfo.Ticket(), InpSlippage))
            PrintFormat("[TIME CLOSE] #%d après %d min | P&L=%.2f$", posInfo.Ticket(), minutesOpen, posInfo.Profit());
      }
   }
}

//+------------------------------------------------------------------+
//| BREAKEVEN                                                        |
//+------------------------------------------------------------------+
void CheckBreakeven()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != g_symbol || posInfo.Magic() != InpMagicNumber) continue;

      double open = posInfo.PriceOpen(), sl = posInfo.StopLoss();
      double tp = posInfo.TakeProfit(), current = posInfo.PriceCurrent();

      if(posInfo.PositionType() == POSITION_TYPE_BUY)
      {
         double trigger = open + (tp - open) * InpBE_Trigger_RR;
         if(current >= trigger && sl < open)
         { trade.PositionModify(posInfo.Ticket(), open, tp);
           PrintFormat("[BE] BUY #%d → BE @ %.5f", posInfo.Ticket(), open); }
      }
      else if(posInfo.PositionType() == POSITION_TYPE_SELL)
      {
         double trigger = open - (open - tp) * InpBE_Trigger_RR;
         if(current <= trigger && sl > open)
         { trade.PositionModify(posInfo.Ticket(), open, tp);
           PrintFormat("[BE] SELL #%d → BE @ %.5f", posInfo.Ticket(), open); }
      }
   }
}

//+------------------------------------------------------------------+
//| v4.3.1 : TRAILING STOP ADAPTATIF (Chandelier Exit)              |
//+------------------------------------------------------------------+
void CheckTrailingStop()
{
   int trailAtrHandle = iATR(g_symbol, PERIOD_M1, InpTrail_ATRPeriod);
   if(trailAtrHandle == INVALID_HANDLE) return;

   double trailATR[];
   ArraySetAsSeries(trailATR, true);
   if(CopyBuffer(trailAtrHandle, 0, 0, 3, trailATR) < 3)
   { IndicatorRelease(trailAtrHandle); return; }
   IndicatorRelease(trailAtrHandle);

   double currentATR = trailATR[1];
   if(currentATR <= 0) return;

   double highBuf[], lowBuf[];
   ArraySetAsSeries(highBuf, true); ArraySetAsSeries(lowBuf, true);
   int barsNeeded = InpTrail_Lookback + 2;
   if(CopyHigh(g_symbol, PERIOD_M1, 0, barsNeeded, highBuf) < barsNeeded) return;
   if(CopyLow(g_symbol, PERIOD_M1, 0, barsNeeded, lowBuf) < barsNeeded) return;

   double highestHigh = highBuf[1];
   double lowestLow   = lowBuf[1];
   for(int i = 2; i <= InpTrail_Lookback; i++)
   {
      if(highBuf[i] > highestHigh) highestHigh = highBuf[i];
      if(lowBuf[i] < lowestLow)   lowestLow = lowBuf[i];
   }

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != g_symbol || posInfo.Magic() != InpMagicNumber) continue;

      double openPrice = posInfo.PriceOpen();
      double currentSL = posInfo.StopLoss();
      double currentTP = posInfo.TakeProfit();
      double current   = posInfo.PriceCurrent();

      bool beTriggered = false;
      double multiplier = InpTrail_Multi;

      if(posInfo.PositionType() == POSITION_TYPE_BUY)
      {
         beTriggered = (currentSL >= openPrice);
         if(beTriggered) multiplier = InpTrail_Multi * InpTrail_TightenMult;

         double trailLevel = NormalizeDouble(highestHigh - multiplier * currentATR, g_digits);

         double profitATR = (current - openPrice) / currentATR;
         if(profitATR < InpTrail_MinProfitATR) continue;

         if(trailLevel > currentSL && trailLevel < current)
         {
            if(trade.PositionModify(posInfo.Ticket(), trailLevel, currentTP))
            {
               PrintFormat("[TRAIL] BUY #%d | SL %.5f → %.5f | Mult=%.1f%s | HH=%.5f ATR=%.2f",
                           posInfo.Ticket(), currentSL, trailLevel,
                           multiplier, beTriggered ? "(BE tight)" : "",
                           highestHigh, currentATR);
            }
         }
      }
      else if(posInfo.PositionType() == POSITION_TYPE_SELL)
      {
         beTriggered = (currentSL <= openPrice && currentSL > 0);
         if(beTriggered) multiplier = InpTrail_Multi * InpTrail_TightenMult;

         double trailLevel = NormalizeDouble(lowestLow + multiplier * currentATR, g_digits);

         double profitATR = (openPrice - current) / currentATR;
         if(profitATR < InpTrail_MinProfitATR) continue;

         if((trailLevel < currentSL || currentSL == 0) && trailLevel > current)
         {
            if(trade.PositionModify(posInfo.Ticket(), trailLevel, currentTP))
            {
               PrintFormat("[TRAIL] SELL #%d | SL %.5f → %.5f | Mult=%.1f%s | LL=%.5f ATR=%.2f",
                           posInfo.Ticket(), currentSL, trailLevel,
                           multiplier, beTriggered ? "(BE tight)" : "",
                           lowestLow, currentATR);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| v4.3.1 : SCALE-IN — Configuration des ordres limit              |
//+------------------------------------------------------------------+
void SetupScaleIn(ulong parentTicket, int direction, double entryPrice, double parentLot, double currentATR)
{
   CancelAllScaleInOrders();

   g_parentTicket      = parentTicket;
   g_parentLot         = parentLot;
   g_scaleInEntryPrice = entryPrice;
   g_scaleInDirection  = direction;
   g_scaleInCount      = 0;
   g_lastScaleInTime   = 0;

   int levels = MathMin(InpSIN_Levels, 5);
   double scaleLot = NormalizeDouble(parentLot * InpSIN_LotPct / 100.0, 2);
   double minLot = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_MIN);
   if(scaleLot < minLot) scaleLot = minLot;

   double parentSL = 0, parentTP = 0;
   if(posInfo.SelectByTicket(parentTicket))
   {
      parentSL = posInfo.StopLoss();
      parentTP = posInfo.TakeProfit();
   }

   double spacing = currentATR * InpSIN_SpacingATR;

   for(int i = 0; i < levels; i++)
   {
      g_scaleInTickets[i] = 0;

      double limitPrice;
      ulong orderTicket = 0;

      if(direction == 1) // BUY : limit en dessous
      {
         limitPrice = NormalizeDouble(entryPrice - (i + 1) * spacing, g_digits);
         if(parentSL > 0 && limitPrice <= parentSL) continue;

         if(trade.BuyLimit(scaleLot, limitPrice, g_symbol, parentSL, parentTP,
                           ORDER_TIME_GTC, 0,
                           StringFormat("SIN|L%d|%.2f lot", i + 1, scaleLot)))
         {
            orderTicket = trade.ResultOrder();
         }
      }
      else // SELL : limit au dessus
      {
         limitPrice = NormalizeDouble(entryPrice + (i + 1) * spacing, g_digits);
         if(parentSL > 0 && limitPrice >= parentSL) continue;

         if(trade.SellLimit(scaleLot, limitPrice, g_symbol, parentSL, parentTP,
                            ORDER_TIME_GTC, 0,
                            StringFormat("SIN|L%d|%.2f lot", i + 1, scaleLot)))
         {
            orderTicket = trade.ResultOrder();
         }
      }

      if(orderTicket > 0)
      {
         g_scaleInTickets[i] = orderTicket;
         g_scaleInCount++;
         PrintFormat("[SCALE-IN] Niveau %d/%d | Ticket #%d | Limit @ %.5f | Lot=%.2f (%.0f%% de %.2f)",
                     i + 1, levels, orderTicket, limitPrice, scaleLot, InpSIN_LotPct, parentLot);
      }
      else
      {
         PrintFormat("[SCALE-IN] Erreur niveau %d : %s", i + 1, trade.ResultRetcodeDescription());
      }
   }

   PrintFormat("[SCALE-IN] %d/%d ordres placés | Parent #%d | Entry=%.5f | Spacing=%.2f (%.1f×ATR)",
               g_scaleInCount, levels, parentTicket, entryPrice, spacing, InpSIN_SpacingATR);
}

//+------------------------------------------------------------------+
//| v4.3.1 : SCALE-IN — Vérification et nettoyage                   |
//+------------------------------------------------------------------+
void CheckScaleInCleanup()
{
   if(g_parentTicket > 0)
   {
      bool parentExists = posInfo.SelectByTicket(g_parentTicket);

      if(!parentExists)
      {
         bool found = false;
         for(int i = PositionsTotal() - 1; i >= 0; i--)
         {
            if(!posInfo.SelectByIndex(i)) continue;
            if(posInfo.Ticket() == g_parentTicket) { found = true; break; }
         }

         if(!found)
         {
            PrintFormat("[SCALE-IN] Parent #%d fermé → annulation des ordres en attente", g_parentTicket);
            CancelAllScaleInOrders();
            g_parentTicket = 0;
            g_scaleInCount = 0;
         }
      }
   }

   // Vérifier les tickets individuels
   if(g_parentTicket > 0 && g_scaleInCount > 0)
   {
      for(int i = 0; i < g_scaleInCount; i++)
      {
         if(g_scaleInTickets[i] == 0) continue;

         bool orderPending = false;
         for(int j = OrdersTotal() - 1; j >= 0; j--)
         {
            if(!orderInfo.SelectByIndex(j)) continue;
            if(orderInfo.Ticket() == g_scaleInTickets[i]) { orderPending = true; break; }
         }

         if(!orderPending)
         {
            // Vérifier si l'ordre a été exécuté (position existe)
            bool positionExists = false;
            for(int j = PositionsTotal() - 1; j >= 0; j--)
            {
               if(!posInfo.SelectByIndex(j)) continue;
               if(posInfo.Magic() == InpMagicNumber && posInfo.Symbol() == g_symbol)
               {
                  if(StringFind(posInfo.Comment(), "SIN") >= 0)
                     positionExists = true;
               }
            }
            if(!positionExists) g_scaleInTickets[i] = 0;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| v4.3.1 : SCALE-IN — Annuler tous les ordres en attente          |
//+------------------------------------------------------------------+
void CancelAllScaleInOrders()
{
   int cancelled = 0;
   for(int i = 0; i < 5; i++)
   {
      if(g_scaleInTickets[i] == 0) continue;

      if(trade.OrderDelete(g_scaleInTickets[i]))
      {
         PrintFormat("[SCALE-IN] Ordre #%d annulé", g_scaleInTickets[i]);
         cancelled++;
      }
      g_scaleInTickets[i] = 0;
   }

   if(cancelled > 0)
      PrintFormat("[SCALE-IN] %d ordre(s) annulé(s)", cancelled);
}

//+------------------------------------------------------------------+
//| UTILITAIRES                                                      |
//+------------------------------------------------------------------+
int CountOpenTrades()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() == g_symbol && posInfo.Magic() == InpMagicNumber) count++;
   }
   return count;
}

bool HasOpenTrade(ENUM_POSITION_TYPE posType)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != g_symbol || posInfo.Magic() != InpMagicNumber) continue;
      if(posInfo.PositionType() == posType) return true;
   }
   return false;
}

bool IsTradeHour()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour, m = dt.min;

   bool inW1 = InpUseWindow1 && (h >= InpW1_Start && h < InpW1_End);
   bool inW2 = InpUseWindow2 && (h >= InpW2_Start && h < InpW2_End);

   if(!inW1 && !inW2) return false;

   if(InpSessionCooldownMin > 0)
   {
      bool openingW1 = InpUseWindow1 && (h == InpW1_Start) && (m < InpSessionCooldownMin);
      bool openingW2 = InpUseWindow2 && (h == InpW2_Start) && (m < InpSessionCooldownMin);
      if(openingW1 || openingW2) return false;
   }
   return true;
}

void PrintSignal(string dir, double price, double sl, double tp,
                 double slDist, double tpDist, VSP_Result &vsp, double lotUsed)
{
   double slPts = slDist / g_point, tpPts = tpDist / g_point;
   double slUSD = slPts * lotUsed * 0.1, tpUSD = tpPts * lotUsed * 0.1;
   string mode = (InpSLTP_Mode == SLTP_MODE_ATR) ? "ATR" : "PTS";

   PrintFormat("[MER][%s][LOT=%.2f][REGIME:%s] %s @ %.5f | SL=%.5f(%.0fpts/$%.2f) | TP=%.5f(%.0fpts/$%.2f) | Spike@%d",
               mode, lotUsed, g_regimeDescription, dir, price,
               sl, slPts, slUSD, tp, tpPts, tpUSD, vsp.barIndex);
}

//+------------------------------------------------------------------+
//| GESTION RISQUE ADAPTATIF                                         |
//+------------------------------------------------------------------+
void UpdateAdaptiveRisk()
{
   if(!InpUseAdaptiveRisk) return;
   if(InpLotMode == LOT_MODE_FIXED) return;

   if(!HistorySelect(TimeCurrent() - 7*24*3600, TimeCurrent())) return;

   int totalDeals = HistoryDealsTotal();
   for(int i = totalDeals - 1; i >= 0; i--)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != g_symbol) continue;
      if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);

      if(profit < 0) { g_consecutiveLosses++; g_consecutiveWins = 0; }
      else if(profit > 0) { g_consecutiveWins++; g_consecutiveLosses = 0; }
      break;
   }

   double newMult = 1.0;
   if(g_consecutiveLosses >= InpAR_LossThreshold * 2)
      newMult = InpAR_ReduceFactor * InpAR_ReduceFactor;
   else if(g_consecutiveLosses >= InpAR_LossThreshold)
      newMult = InpAR_ReduceFactor;
   else if(g_consecutiveWins >= 2)
      newMult = MathMin(InpAR_MaxBoost, 1.0 + (g_consecutiveWins - 1) * 0.1);

   newMult = MathMax(0.25, MathMin(InpAR_MaxBoost, newMult));

   if(MathAbs(newMult - g_currentRiskMult) > 0.01)
   {
      PrintFormat("[ADAPT RISK] Mult: %.2f → %.2f", g_currentRiskMult, newMult);
      g_currentRiskMult = newMult;
   }
}

//+------------------------------------------------------------------+
//| CALCUL LOT                                                       |
//+------------------------------------------------------------------+
double CalcLotSize(double slDist, double regimeRiskFactor = 1.0)
{
   if(InpLotMode == LOT_MODE_FIXED)
      return InpLotSize;

   double capital      = AccountInfoDouble(ACCOUNT_BALANCE);
   double adaptiveMult = InpUseAdaptiveRisk ? g_currentRiskMult : 1.0;
   double effectiveRisk = InpRiskPercent * adaptiveMult * regimeRiskFactor;
   double riskMoney    = capital * (effectiveRisk / 100.0);

   double slPoints = slDist / g_point;
   if(slPoints <= 0) return InpLotMin;

   double pointValue = 0.1;
   double lot = riskMoney / (slPoints * pointValue);

   double lotStep = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   lot = MathFloor(lot / lotStep) * lotStep;
   lot = MathMax(lot, InpLotMin);
   lot = MathMin(lot, InpLotMax);

   PrintFormat("[LOT%%] Cap=%.0f$ Risk=%.1f%%×%.2f×%.2f=%.2f$ SL=%.0fpts → Lot=%.2f",
               capital, InpRiskPercent, adaptiveMult, regimeRiskFactor, effectiveRisk, slPoints, lot);

   return lot;
}
//+------------------------------------------------------------------+
