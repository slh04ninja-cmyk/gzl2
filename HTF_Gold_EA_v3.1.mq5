//+------------------------------------------------------------------+
//|                                            HTF_Gold_EA_v3.4.mq5 |
//|  ██╗  ██╗████████╗███████╗     ██████╗  ██████╗ ██╗     ██████╗ |
//|  ██║  ██║╚══██╔══╝██╔════╝    ██╔════╝ ██╔═══██╗██║     ██╔══██╗|
//|  ███████║   ██║   █████╗      ██║  ███╗██║   ██║██║     ██║  ██║|
//|  ██╔══██║   ██║   ██╔══╝      ██║   ██║██║   ██║██║     ██║  ██║|
//|  ██║  ██║   ██║   ██║         ╚██████╔╝╚██████╔╝███████╗██████╔╝|
//|  ╚═╝  ╚═╝   ╚═╝   ╚═╝          ╚═════╝  ╚═════╝ ╚══════╝╚═════╝ |
//|                      E A   v 3 . 4   (MODIFIÉ)                   |
//+------------------------------------------------------------------+
//| Stratégie : MER (Market Entry Rules)                            |
//|   Couche 1 : Velocity Spike (VSP) + Spike Cooldown             |
//|   Couche 2 : Micro Structure Break (MSB)                       |
//|   Biais    : Double SuperTrend H1                              |
//|   SL/TP    : ATR dynamique  OU  Points fixes                   |
//|   Filtres  : ADX H1 · Spread min · Horaire London/NY           |
//|   Lot      : Fixe  OU  % Risque + Risque Adaptatif             |
//+------------------------------------------------------------------+
#property copyright "HTF Gold EA v3.4 - MER L1+L2 + AdaptRisk + SessionCooldown"
#property version   "3.40"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

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

//+------------------------------------------------------------------+
//| INPUTS                                                           |
//+------------------------------------------------------------------+
input group "=== SYMBOL ==="
input string            InpSymbol           = "";            // Symbol (vide = chart actuel)

input group "=== FILTRE SUPERTREND H1 ==="
input int               InpST1_Period       = 7;             // [ST1] Période ATR
input double            InpST1_Multiplier   = 0.4;           // [ST1] Multiplicateur
input int               InpST2_Period       = 11;            // [ST2] Période ATR
input double            InpST2_Multiplier   = 1.2;           // [ST2] Multiplicateur

input group "=== FILTRE ADX H1 ==="
input bool              InpUseADX           = true;          // Activer filtre ADX H1
input int               InpADX_Period       = 12;            // Période ADX H1
input double            InpADX_MinLevel     = 23.0;          // ADX minimum (tendance > range)

input group "=== SL MINIMUM SPREAD ==="
input bool              InpUseSpreadSL      = false;         // Activer SL minimum basé sur spread
input int               InpSpread_Buffer    = 50;            // Buffer sécurité en points (spread + X pts)

input group "=== COUCHE 1 : VELOCITY SPIKE (VSP) ==="
input int               InpVSP_ATR_Period   = 16;            // ATR Period pour spike
input double            InpVSP_Spike_Multi  = 1.6;           // Corps bougie > X * ATR = Spike
input bool              InpVSP_NeedConfirm  = true;          // Attendre bougie confirmation
input int               InpVSP_LookBack     = 7;             // Chercher spike dans X bougies passées
input bool              InpUseCooldown      = true;          // Activer filtre spike cooldown (intégré)
input int               InpCooldownBars     = 5;             // Bougies M1 mini entre 2 spikes (même direction)

input group "=== COUCHE 2 : MICRO STRUCTURE BREAK (MSB) ==="
input bool              InpUseMSB           = true;          // Activer Couche 2 MSB
input int               InpMSB_SwingBars    = 16;            // Bougies pour détecter swing high/low
input ENUM_MSB_BREAK    InpMSB_BreakType    = MSB_BREAK_TOUCH; // Condition de cassure
input ENUM_MSB_TIMING   InpMSB_Timing       = MSB_TIMING_BEFORE; // MSB avant ou après le spike

input group "=== MODE SL/TP ==="
input ENUM_SLTP_MODE    InpSLTP_Mode        = SLTP_MODE_ATR; // Mode calcul SL/TP

// --- Sous-groupe ATR
input int               InpATR_SL_Period    = 15;            // [ATR] Période ATR pour SL
input double            InpATR_SL_Multi     = 1.5;           // [ATR] Multiplicateur SL
input double            InpRR_Ratio         = 2.1;           // [ATR/PTS] Risk:Reward TP

// --- Sous-groupe Points
input int               InpSL_Points        = 5000;          // [PTS] SL en points (5000 pts = 5$)
input int               InpTP_Points        = 7500;          // [PTS] TP en points (7500 pts = 7.5$)

input group "=== GESTION DU LOT ==="
input ENUM_LOT_MODE     InpLotMode          = LOT_MODE_PERCENT; // Mode lot (Fixe / % Risque)
input double            InpLotSize          = 0.01;          // [FIXE] Lot fixe
input double            InpRiskPercent      = 1.0;           // [%] Risque par trade (% capital)
input double            InpLotMin           = 0.01;          // [%] Lot minimum autorisé
input double            InpLotMax           = 1.00;          // [%] Lot maximum autorisé

input group "=== RISQUE ADAPTATIF (Idée 5) ==="
input bool              InpUseAdaptiveRisk  = true;          // Activer gestion risque adaptative
input int               InpAR_LossThreshold = 4;             // Nb pertes consécutives → réduction
input double            InpAR_ReduceFactor  = 0.25;          // Facteur réduction (ex: 0.5 = moitié)
input double            InpAR_MaxBoost      = 3.0;           // Boost max après gains (ex: 1.25 = +25%)

input group "=== BREAKEVEN ==="
input bool              InpUseBreakeven     = true;          // Activer Breakeven
input double            InpBE_Trigger_RR    = 1.4;           // Déclencher BE à X * TP dist

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
   bool   detected;   // true si MSB confirmé
   double swingLevel; // niveau du swing cassé
   int    barIndex;   // index de la bougie qui a cassé
};

//+------------------------------------------------------------------+
//| GLOBALS                                                          |
//+------------------------------------------------------------------+
CTrade         trade;
CPositionInfo  posInfo;
string         g_symbol;
int            g_digits;
double         g_point;

// Spike Cooldown : timestamp du dernier spike par direction
// [0] = BUY (+1)  |  [1] = SELL (-1)
datetime       g_lastSpikeTime[2];

// Risque adaptatif : compteurs de séries
int            g_consecutiveLosses = 0;
int            g_consecutiveWins   = 0;
double         g_currentRiskMult   = 1.0; // multiplicateur actuel du risque

// Handles indicateurs
int            m1_atr_sl_handle    = INVALID_HANDLE;
int            m1_atr_vsp_handle   = INVALID_HANDLE;
int            h1_adx_handle       = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| INIT                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   g_digits = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   g_point  = SymbolInfoDouble(g_symbol, SYMBOL_POINT);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   // Initialiser les timestamps cooldown
   g_lastSpikeTime[0] = 0; // BUY
   g_lastSpikeTime[1] = 0; // SELL

   // Initialiser risque adaptatif
   g_consecutiveLosses = 0;
   g_consecutiveWins   = 0;
   g_currentRiskMult   = 1.0;

   // Handles M1 ATR
   m1_atr_sl_handle  = iATR(g_symbol, PERIOD_M1, InpATR_SL_Period);
   m1_atr_vsp_handle = iATR(g_symbol, PERIOD_M1, InpVSP_ATR_Period);

   // ADX H1
   h1_adx_handle = iADX(g_symbol, PERIOD_H1, InpADX_Period);

   if(m1_atr_sl_handle   == INVALID_HANDLE || m1_atr_vsp_handle  == INVALID_HANDLE ||
      h1_adx_handle      == INVALID_HANDLE)
   {
      Print("ERREUR: Création handles échouée.");
      return INIT_FAILED;
   }

   // Log configuration
   string sltpName = (InpSLTP_Mode == SLTP_MODE_ATR) ?
                     StringFormat("ATR(%d)×%.1f | RR=%.1f", InpATR_SL_Period, InpATR_SL_Multi, InpRR_Ratio) :
                     StringFormat("Points | SL=%d pts (%.2f$) | TP=%d pts (%.2f$)",
                                  InpSL_Points, InpSL_Points * InpLotSize * 0.1,
                                  InpTP_Points, InpTP_Points * InpLotSize * 0.1);

   PrintFormat("HTF Gold EA v3.4 (modifié) | %s | H1: DoubleST(%d×%.1f / %d×%.1f) | SL/TP: %s | MSB: %s | Cooldown intégré: %d bougies | AdaptRisk: %s | SessCooldown: %dmin",
               g_symbol,
               InpST1_Period, InpST1_Multiplier,
               InpST2_Period, InpST2_Multiplier,
               sltpName,
               InpUseMSB ? StringFormat("ON SwingBars=%d Break=%d Timing=%d", InpMSB_SwingBars, InpMSB_BreakType, InpMSB_Timing) : "OFF",
               InpUseCooldown ? InpCooldownBars : 0,
               InpUseAdaptiveRisk ? StringFormat("ON seuil=%d fact=%.2f boost=%.2f", InpAR_LossThreshold, InpAR_ReduceFactor, InpAR_MaxBoost) : "OFF",
               InpSessionCooldownMin);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| DEINIT                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(m1_atr_sl_handle);
   IndicatorRelease(m1_atr_vsp_handle);
   IndicatorRelease(h1_adx_handle);
}

//+------------------------------------------------------------------+
//| TICK                                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Nouvelle bougie M1 seulement
   static datetime lastBar = 0;
   datetime currentBar = iTime(g_symbol, PERIOD_M1, 0);
   if(currentBar == lastBar) return;
   lastBar = currentBar;

   // Mise à jour risque adaptatif (analyse dernier trade clôturé)
   UpdateAdaptiveRisk();

   // Filtre horaire
   if(InpUseTimeFilter && !IsTradeHour()) return;

   // Breakeven
   if(InpUseBreakeven) CheckBreakeven();

   // Fermeture par temps
   if(InpUseTimeClose) CheckTimeClose();

   // Max trades
   if(CountOpenTrades() >= InpMaxTrades) return;

   // Copier ATR buffers
   double m1_atr_sl[], m1_atr_vsp[];
   ArraySetAsSeries(m1_atr_sl,  true);
   ArraySetAsSeries(m1_atr_vsp, true);
   if(CopyBuffer(m1_atr_sl_handle,  0, 0, 5, m1_atr_sl)                    < 5) return;
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, InpVSP_LookBack + 3, m1_atr_vsp) < InpVSP_LookBack + 3) return;

   // Biais H1 (uniquement SuperTrend)
   int h1Bias = GetH1Bias_SuperTrend();
   if(h1Bias == 0) return;

   // Filtre ADX H1
   if(InpUseADX)
   {
      double adxBuf[];
      ArraySetAsSeries(adxBuf, true);
      if(CopyBuffer(h1_adx_handle, 0, 0, 3, adxBuf) < 3) return;
      double adxVal = adxBuf[1]; // barre H1 fermée
      if(adxVal < InpADX_MinLevel)
      {
         PrintFormat("[ADX BLOQUÉ] ADX H1=%.2f < seuil=%.1f → range détecté → pas de trade",
                     adxVal, InpADX_MinLevel);
         return;
      }
   }

   // Détection VSP (avec cooldown intégré)
   VSP_Result vsp = DetectVelocitySpike();
   if(!vsp.detected) return;

   // RÈGLE ABSOLUE : spike DOIT être strictement opposé au biais H1
   if(vsp.direction != -h1Bias)
   {
      PrintFormat("[VSP BLOQUÉ] spike=%d biais H1=%d → contre-biais interdit",
                  vsp.direction, h1Bias);
      return;
   }

   // Signal = toujours dans la direction du biais H1
   int signal = h1Bias;

   // Confirmation bougie DOIT être dans la direction du biais H1
   if(InpVSP_NeedConfirm)
   {
      double closeC = iClose(g_symbol, PERIOD_M1, 1);
      double openC  = iOpen(g_symbol,  PERIOD_M1, 1);
      if(signal ==  1 && closeC <= openC)
      {
         PrintFormat("[CONFIRM BLOQUÉ] H1=BULL confirmation baissière → BUY annulé");
         return;
      }
      if(signal == -1 && closeC >= openC)
      {
         PrintFormat("[CONFIRM BLOQUÉ] H1=BEAR confirmation haussière → SELL annulé");
         return;
      }
   }

   // Couche 2 : MSB
   if(InpUseMSB)
   {
      MSB_Result msb = DetectMSB(signal, vsp.barIndex);
      if(!msb.detected)
      {
         PrintFormat("[MSB BLOQUÉ] Pas de structure M1 cassée dans direction %d", signal);
         return;
      }
      PrintFormat("[MSB OK] Swing=%.3f cassé à bar[%d]", msb.swingLevel, msb.barIndex);
   }

   // Calcul SL/TP selon mode
   double slDist = 0, tpDist = 0;

   if(InpSLTP_Mode == SLTP_MODE_ATR)
   {
      double atr = m1_atr_sl[1];
      if(atr <= 0) return;
      slDist = atr * InpATR_SL_Multi;
      tpDist = slDist * InpRR_Ratio;
   }
   else // SLTP_MODE_POINTS
   {
      slDist = InpSL_Points * g_point;
      tpDist = InpTP_Points * g_point;
   }

   double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);

   // Calculer le lot selon le mode choisi
   double lotSize = CalcLotSize(slDist);

   // Filtre SL minimum basé sur spread
   if(InpUseSpreadSL)
   {
      double spreadPoints = (double)SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
      double slMinDist    = (spreadPoints + InpSpread_Buffer) * g_point;
      if(slDist < slMinDist)
      {
         PrintFormat("[SPREAD SL BLOQUÉ] SL=%.1f pts < min=%.1f pts (spread=%.1f + buffer=%d) → trade rejeté",
                     slDist/g_point, slMinDist/g_point, spreadPoints, InpSpread_Buffer);
         return;
      }
   }

   if(signal == 1 && !HasOpenTrade(POSITION_TYPE_BUY))
   {
      double sl = NormalizeDouble(ask - slDist, g_digits);
      double tp = NormalizeDouble(ask + tpDist, g_digits);

      if(trade.Buy(lotSize, g_symbol, ask, sl, tp,
                   StringFormat("HTF-v3|BUY|%s|lot=%.2f|spike@%d",
                                (InpSLTP_Mode==SLTP_MODE_ATR?"ATR":"PTS"),
                                lotSize, vsp.barIndex)))
      {
         PrintSignal("BUY", ask, sl, tp, MathAbs(ask-sl), MathAbs(tp-ask), vsp);
      }
   }
   else if(signal == -1 && !HasOpenTrade(POSITION_TYPE_SELL))
   {
      double sl = NormalizeDouble(bid + slDist, g_digits);
      double tp = NormalizeDouble(bid - tpDist, g_digits);

      if(trade.Sell(lotSize, g_symbol, bid, sl, tp,
                    StringFormat("HTF-v3|SELL|%s|lot=%.2f|spike@%d",
                                 (InpSLTP_Mode==SLTP_MODE_ATR?"ATR":"PTS"),
                                 lotSize, vsp.barIndex)))
      {
         PrintSignal("SELL", bid, sl, tp, MathAbs(bid-sl), MathAbs(bid-tp), vsp);
      }
   }
}

//+------------------------------------------------------------------+
//| DÉTECTION VELOCITY SPIKE AVEC COOLDOWN INTÉGRÉ                  |
//+------------------------------------------------------------------+
VSP_Result DetectVelocitySpike()
{
   VSP_Result result;
   result.detected  = false;
   result.direction = 0;
   result.barIndex  = 0;
   result.spikeSize = 0;

   int barsToCheck = InpVSP_LookBack + 2;
   double open[], close[];
   ArraySetAsSeries(open,  true);
   ArraySetAsSeries(close, true);

   if(CopyOpen(g_symbol,  PERIOD_M1, 0, barsToCheck, open)  < barsToCheck) return result;
   if(CopyClose(g_symbol, PERIOD_M1, 0, barsToCheck, close) < barsToCheck) return result;

   double m1_atr_vsp[];
   ArraySetAsSeries(m1_atr_vsp, true);
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, barsToCheck, m1_atr_vsp) < barsToCheck) return result;

   for(int i = 2; i <= InpVSP_LookBack + 1; i++)
   {
      double bodySize  = MathAbs(close[i] - open[i]);
      double threshold = m1_atr_vsp[i] * InpVSP_Spike_Multi;

      if(bodySize >= threshold)
      {
         int dir = (close[i] > open[i]) ? 1 : -1;

         // Vérification cooldown intégrée
         if(InpUseCooldown)
         {
            int dirIdx = (dir == 1) ? 0 : 1;
            int barsElapsed = (int)((TimeCurrent() - g_lastSpikeTime[dirIdx]) / 60);
            if(g_lastSpikeTime[dirIdx] > 0 && barsElapsed < InpCooldownBars)
            {
               PrintFormat("[COOLDOWN] Spike %s bar[%d] bloqué — dernier spike il y a %d bougie(s) < seuil %d",
                           (dir == 1 ? "BUY" : "SELL"), i, barsElapsed, InpCooldownBars);
               continue; // spike rejeté
            }
         }

         result.detected  = true;
         result.barIndex  = i;
         result.spikeSize = bodySize;
         result.direction = dir;
         break;
      }
   }
   return result;
}

//+------------------------------------------------------------------+
//| COUCHE 2 — MICRO STRUCTURE BREAK (MSB)                         |
//+------------------------------------------------------------------+
MSB_Result DetectMSB(int signal, int spikeBarIndex)
{
   MSB_Result result;
   result.detected   = false;
   result.swingLevel = 0.0;
   result.barIndex   = 0;

   int totalBars = InpMSB_SwingBars + spikeBarIndex + 3;

   double highBuf[], lowBuf[], closeBuf[], openBuf[];
   ArraySetAsSeries(highBuf,  true);
   ArraySetAsSeries(lowBuf,   true);
   ArraySetAsSeries(closeBuf, true);
   ArraySetAsSeries(openBuf,  true);

   if(CopyHigh (g_symbol, PERIOD_M1, 0, totalBars, highBuf)  < totalBars) return result;
   if(CopyLow  (g_symbol, PERIOD_M1, 0, totalBars, lowBuf)   < totalBars) return result;
   if(CopyClose(g_symbol, PERIOD_M1, 0, totalBars, closeBuf) < totalBars) return result;
   if(CopyOpen (g_symbol, PERIOD_M1, 0, totalBars, openBuf)  < totalBars) return result;

   if(InpMSB_Timing == MSB_TIMING_AFTER)
   {
      int zoneStart = spikeBarIndex + 1;
      int zoneEnd   = spikeBarIndex + InpMSB_SwingBars;
      if(zoneEnd >= totalBars) return result;

      if(signal == 1)
      {
         double swingLow = lowBuf[zoneStart];
         for(int i = zoneStart + 1; i <= zoneEnd; i++)
            if(lowBuf[i] < swingLow) swingLow = lowBuf[i];

         bool broken = false;
         switch(InpMSB_BreakType)
         {
            case MSB_BREAK_CLOSE: broken = (closeBuf[1] > swingLow); break;
            case MSB_BREAK_TOUCH: broken = (highBuf[1]  > swingLow); break;
            case MSB_BREAK_BODY:  broken = (closeBuf[1] > swingLow && openBuf[1] > swingLow); break;
         }
         if(broken)
         {
            result.detected   = true;
            result.swingLevel = swingLow;
            result.barIndex   = 1;
         }
      }
      else if(signal == -1)
      {
         double swingHigh = highBuf[zoneStart];
         for(int i = zoneStart + 1; i <= zoneEnd; i++)
            if(highBuf[i] > swingHigh) swingHigh = highBuf[i];

         bool broken = false;
         switch(InpMSB_BreakType)
         {
            case MSB_BREAK_CLOSE: broken = (closeBuf[1] < swingHigh); break;
            case MSB_BREAK_TOUCH: broken = (lowBuf[1]   < swingHigh); break;
            case MSB_BREAK_BODY:  broken = (closeBuf[1] < swingHigh && openBuf[1] < swingHigh); break;
         }
         if(broken)
         {
            result.detected   = true;
            result.swingLevel = swingHigh;
            result.barIndex   = 1;
         }
      }
   }
   else // MSB_TIMING_BEFORE
   {
      if(spikeBarIndex < 2) return result;

      int zoneStart = 2;
      int zoneEnd   = spikeBarIndex - 1;
      if(zoneStart > zoneEnd) return result;

      int refStart = spikeBarIndex + 1;
      int refEnd   = spikeBarIndex + InpMSB_SwingBars;
      if(refEnd >= totalBars) return result;

      if(signal == 1)
      {
         double swingLow = lowBuf[refStart];
         for(int i = refStart + 1; i <= refEnd; i++)
            if(lowBuf[i] < swingLow) swingLow = lowBuf[i];

         for(int i = zoneEnd; i >= zoneStart; i--)
         {
            bool broken = false;
            switch(InpMSB_BreakType)
            {
               case MSB_BREAK_CLOSE: broken = (closeBuf[i] > swingLow); break;
               case MSB_BREAK_TOUCH: broken = (highBuf[i]  > swingLow); break;
               case MSB_BREAK_BODY:  broken = (closeBuf[i] > swingLow && openBuf[i] > swingLow); break;
            }
            if(broken)
            {
               result.detected   = true;
               result.swingLevel = swingLow;
               result.barIndex   = i;
               break;
            }
         }
      }
      else if(signal == -1)
      {
         double swingHigh = highBuf[refStart];
         for(int i = refStart + 1; i <= refEnd; i++)
            if(highBuf[i] > swingHigh) swingHigh = highBuf[i];

         for(int i = zoneEnd; i >= zoneStart; i--)
         {
            bool broken = false;
            switch(InpMSB_BreakType)
            {
               case MSB_BREAK_CLOSE: broken = (closeBuf[i] < swingHigh); break;
               case MSB_BREAK_TOUCH: broken = (lowBuf[i]   < swingHigh); break;
               case MSB_BREAK_BODY:  broken = (closeBuf[i] < swingHigh && openBuf[i] < swingHigh); break;
            }
            if(broken)
            {
               result.detected   = true;
               result.swingLevel = swingHigh;
               result.barIndex   = i;
               break;
            }
         }
      }
   }

   return result;
}

//+------------------------------------------------------------------+
//| BIAIS H1 — DOUBLE SUPERTREND                                    |
//+------------------------------------------------------------------+
int GetH1Bias_SuperTrend()
{
   int barsNeeded = MathMax(InpST1_Period, InpST2_Period) * 3 + 10;
   SuperTrendData st1 = CalcSuperTrend(PERIOD_H1, InpST1_Period, InpST1_Multiplier, barsNeeded);
   SuperTrendData st2 = CalcSuperTrend(PERIOD_H1, InpST2_Period, InpST2_Multiplier, barsNeeded);
   if(st1.direction ==  1 && st2.direction ==  1) return  1;
   if(st1.direction == -1 && st2.direction == -1) return -1;
   return 0;
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
   ArraySetAsSeries(atrBuf,   true);
   ArraySetAsSeries(highBuf,  true);
   ArraySetAsSeries(lowBuf,   true);
   ArraySetAsSeries(closeBuf, true);

   if(CopyBuffer(atrH,  0, 0, bars, atrBuf)   < bars ||
      CopyHigh(g_symbol,  tf, 0, bars, highBuf)  < bars ||
      CopyLow(g_symbol,   tf, 0, bars, lowBuf)   < bars ||
      CopyClose(g_symbol, tf, 0, bars, closeBuf) < bars)
   { IndicatorRelease(atrH); return result; }

   IndicatorRelease(atrH);

   double upperBand[], lowerBand[];
   int    dir[];
   ArrayResize(upperBand, bars);
   ArrayResize(lowerBand, bars);
   ArrayResize(dir, bars);

   for(int i = bars - 1; i >= 0; i--)
   {
      double hl2 = (highBuf[i] + lowBuf[i]) / 2.0;
      double bu  = hl2 + multiplier * atrBuf[i];
      double bl  = hl2 - multiplier * atrBuf[i];

      if(i == bars - 1)
      { upperBand[i] = bu; lowerBand[i] = bl; dir[i] = 1; }
      else
      {
         upperBand[i] = (bu < upperBand[i+1] || closeBuf[i+1] > upperBand[i+1]) ? bu : upperBand[i+1];
         lowerBand[i] = (bl > lowerBand[i+1] || closeBuf[i+1] < lowerBand[i+1]) ? bl : lowerBand[i+1];
         if     (closeBuf[i] > upperBand[i]) dir[i] =  1;
         else if(closeBuf[i] < lowerBand[i]) dir[i] = -1;
         else                                dir[i] =  dir[i+1];
      }
   }

   result.direction = dir[1];
   result.upper     = upperBand[1];
   result.lower     = lowerBand[1];
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

      datetime openTime = posInfo.Time();
      int minutesOpen   = (int)((now - openTime) / 60);

      if(minutesOpen >= InpMaxMinutes)
      {
         double closePrice = (posInfo.PositionType() == POSITION_TYPE_BUY)
                             ? SymbolInfoDouble(g_symbol, SYMBOL_BID)
                             : SymbolInfoDouble(g_symbol, SYMBOL_ASK);

         if(trade.PositionClose(posInfo.Ticket(), InpSlippage))
            PrintFormat("[TIME CLOSE] #%d fermé après %d min | P&L=%.2f$",
                        posInfo.Ticket(), minutesOpen, posInfo.Profit());
         else
            PrintFormat("[TIME CLOSE] Erreur fermeture #%d : %s",
                        posInfo.Ticket(), trade.ResultRetcodeDescription());
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

      double open    = posInfo.PriceOpen();
      double sl      = posInfo.StopLoss();
      double tp      = posInfo.TakeProfit();
      double current = posInfo.PriceCurrent();

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
   int h = dt.hour;
   int m = dt.min;

   bool inW1 = InpUseWindow1 && (h >= InpW1_Start && h < InpW1_End);
   bool inW2 = InpUseWindow2 && (h >= InpW2_Start && h < InpW2_End);

   if(!inW1 && !inW2) return false;

   if(InpSessionCooldownMin > 0)
   {
      bool openingW1 = InpUseWindow1 && (h == InpW1_Start) && (m < InpSessionCooldownMin);
      bool openingW2 = InpUseWindow2 && (h == InpW2_Start) && (m < InpSessionCooldownMin);
      if(openingW1 || openingW2)
      {
         PrintFormat("[SESSION COOLDOWN] Ouverture session → attente %d min (actuellement %02d:%02d)",
                     InpSessionCooldownMin, h, m);
         return false;
      }
   }

   return true;
}

void PrintSignal(string dir, double price, double sl, double tp,
                 double slDist, double tpDist, VSP_Result &vsp)
{
   double slPts   = slDist / g_point;
   double tpPts   = tpDist / g_point;
   double lotUsed = (InpLotMode == LOT_MODE_FIXED) ? InpLotSize : CalcLotSize(slDist);
   double slUSD   = slPts * lotUsed * 0.1;
   double tpUSD   = tpPts * lotUsed * 0.1;
   string mode    = (InpSLTP_Mode == SLTP_MODE_ATR) ? "ATR" : "PTS";
   string lotMode = (InpLotMode == LOT_MODE_FIXED) ? "FIXE" : StringFormat("%.1f%%", InpRiskPercent);

   PrintFormat("[MER-L1][%s][LOT:%s=%.2f] %s @ %.5f | SL=%.5f(%.0fpts/%.2f$) | TP=%.5f(%.0fpts/%.2f$) | Spike@%d",
               mode, lotMode, lotUsed, dir, price,
               sl, slPts, slUSD, tp, tpPts, tpUSD, vsp.barIndex);
}

//+------------------------------------------------------------------+
//| GESTION RISQUE ADAPTATIVE (Idée 5)                              |
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
      if(HistoryDealGetString(ticket, DEAL_SYMBOL)  != g_symbol)   continue;
      if((long)HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);

      if(profit < 0)
      {
         g_consecutiveLosses++;
         g_consecutiveWins = 0;
      }
      else if(profit > 0)
      {
         g_consecutiveWins++;
         g_consecutiveLosses = 0;
      }
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
      PrintFormat("[ADAPT RISK] Mult: %.2f → %.2f | Pertes consec: %d | Gains consec: %d",
                  g_currentRiskMult, newMult, g_consecutiveLosses, g_consecutiveWins);
      g_currentRiskMult = newMult;
   }
}

//+------------------------------------------------------------------+
//| CALCUL LOT SELON MODE                                            |
//+------------------------------------------------------------------+
double CalcLotSize(double slDist)
{
   if(InpLotMode == LOT_MODE_FIXED)
      return InpLotSize;

   double capital      = AccountInfoDouble(ACCOUNT_BALANCE);
   double effectiveRisk = InpRiskPercent * (InpUseAdaptiveRisk ? g_currentRiskMult : 1.0);
   double riskMoney    = capital * (effectiveRisk / 100.0);

   double slPoints  = slDist / g_point;
   if(slPoints <= 0) return InpLotMin;

   double pointValue = 0.1; // XAUUSDm
   double lot = riskMoney / (slPoints * pointValue);

   double lotStep = SymbolInfoDouble(g_symbol, SYMBOL_VOLUME_STEP);
   lot = MathFloor(lot / lotStep) * lotStep;

   lot = MathMax(lot, InpLotMin);
   lot = MathMin(lot, InpLotMax);

   PrintFormat("[LOT%%] Capital=%.2f$ Risk=%.1f%%×%.2f=%.2f$ SL=%.0fpts → Lot=%.2f",
               capital, InpRiskPercent, (InpUseAdaptiveRisk ? g_currentRiskMult : 1.0),
               riskMoney, slPoints, lot);

   return lot;
}
//+------------------------------------------------------------------+
