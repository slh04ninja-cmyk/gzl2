//+------------------------------------------------------------------+
//|                                            HTF_Gold_EA_v2.1.mq5 |
//|                MÉTHODE MER — COUCHE 1 : Velocity Spike (VSP)    |
//|                H1 Bias : EMA OU Double SuperTrend               |
//|                SL/TP  : ATR OU Points (liste déroulante)        |
//+------------------------------------------------------------------+
#property copyright "HTF Gold EA v2.1 - MER Layer 1"
#property version   "2.10"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//+------------------------------------------------------------------+
//| ENUMS                                                            |
//+------------------------------------------------------------------+
enum ENUM_H1_FILTER
{
   H1_FILTER_EMA        = 0,  // EMAs (EMA Fast / EMA Slow)
   H1_FILTER_SUPERTREND = 1   // Double SuperTrend
};

enum ENUM_SLTP_MODE
{
   SLTP_MODE_ATR    = 0,  // ATR (dynamique)
   SLTP_MODE_POINTS = 1   // Points fixes (1000 pts = 10$ sur XAUUSDm 0.01)
};

//+------------------------------------------------------------------+
//| INPUTS                                                           |
//+------------------------------------------------------------------+
input group "=== SYMBOL ==="
input string            InpSymbol           = "";            // Symbol (vide = chart actuel)

input group "=== FILTRE H1 ==="
input ENUM_H1_FILTER    InpH1FilterType     = H1_FILTER_EMA; // Filtre H1
input int               InpH1_EMA_Fast      = 20;            // [EMA] Période Fast
input int               InpH1_EMA_Slow      = 100;           // [EMA] Période Slow
input int               InpST1_Period       = 10;            // [ST1] Période ATR
input double            InpST1_Multiplier   = 1.0;           // [ST1] Multiplicateur
input int               InpST2_Period       = 20;            // [ST2] Période ATR
input double            InpST2_Multiplier   = 2.0;           // [ST2] Multiplicateur

input group "=== COUCHE 1 : VELOCITY SPIKE (VSP) ==="
input int               InpVSP_ATR_Period   = 15;            // ATR Period pour spike
input double            InpVSP_Spike_Multi  = 1.6;           // Corps bougie > X * ATR = Spike
input bool              InpVSP_NeedConfirm  = true;          // Attendre bougie confirmation
input int               InpVSP_LookBack     = 6;             // Chercher spike dans X bougies passées

input group "=== MODE SL/TP ==="
input ENUM_SLTP_MODE    InpSLTP_Mode        = SLTP_MODE_ATR; // Mode calcul SL/TP

// --- Sous-groupe ATR
input int               InpATR_SL_Period    = 17;            // [ATR] Période ATR pour SL
input double            InpATR_SL_Multi     = 1.6;           // [ATR] Multiplicateur SL
input double            InpRR_Ratio         = 1.4;           // [ATR/PTS] Risk:Reward TP

// --- Sous-groupe Points
// XAUUSDm : 1 point = 0.01$ sur 0.01 lot → 1000 pts = 10$
input int               InpSL_Points        = 5000;          // [PTS] SL en points (5000 pts = 5$)
input int               InpTP_Points        = 7500;          // [PTS] TP en points (7500 pts = 7.5$)

input group "=== TRADE MANAGEMENT ==="
input double            InpLotSize          = 0.01;          // Lot fixe
input int               InpMaxTrades        = 3;             // Max trades simultanés
input int               InpMagicNumber      = 202602;        // Magic Number
input int               InpSlippage         = 10;            // Slippage (points)

input group "=== BREAKEVEN ==="
input bool              InpUseBreakeven     = false;         // Activer Breakeven
input double            InpBE_Trigger_RR    = 0.8;           // Déclencher BE à X * TP dist

input group "=== FERMETURE PAR TEMPS ==="
input bool              InpUseTimeClose     = false;         // Activer fermeture par temps
input int               InpMaxMinutes       = 30;            // Fermer après X minutes

input group "=== FILTRE HORAIRE ==="
input bool              InpUseTimeFilter    = true;          // Filtre horaire actif
input int               InpStartHour        = 7;             // Heure début
input int               InpEndHour          = 20;            // Heure fin

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

//+------------------------------------------------------------------+
//| GLOBALS                                                          |
//+------------------------------------------------------------------+
CTrade         trade;
CPositionInfo  posInfo;
string         symbol;
int            digits;
double         point;

int            h1_ema_fast_handle  = INVALID_HANDLE;
int            h1_ema_slow_handle  = INVALID_HANDLE;
int            m1_atr_sl_handle    = INVALID_HANDLE;
int            m1_atr_vsp_handle   = INVALID_HANDLE;

double         h1_ema_fast[];
double         h1_ema_slow[];
double         m1_atr_sl[];
double         m1_atr_vsp[];

//+------------------------------------------------------------------+
//| INIT                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   point  = SymbolInfoDouble(symbol, SYMBOL_POINT);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   // H1 EMA
   h1_ema_fast_handle = iMA(symbol, PERIOD_H1, InpH1_EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h1_ema_slow_handle = iMA(symbol, PERIOD_H1, InpH1_EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   // M1 ATR handles
   m1_atr_sl_handle  = iATR(symbol, PERIOD_M1, InpATR_SL_Period);
   m1_atr_vsp_handle = iATR(symbol, PERIOD_M1, InpVSP_ATR_Period);

   if(h1_ema_fast_handle == INVALID_HANDLE || h1_ema_slow_handle == INVALID_HANDLE ||
      m1_atr_sl_handle   == INVALID_HANDLE || m1_atr_vsp_handle  == INVALID_HANDLE)
   {
      Print("ERREUR: Création handles échouée.");
      return INIT_FAILED;
   }

   ArraySetAsSeries(h1_ema_fast, true);
   ArraySetAsSeries(h1_ema_slow, true);
   ArraySetAsSeries(m1_atr_sl,   true);
   ArraySetAsSeries(m1_atr_vsp,  true);

   // Log configuration
   string filterName = (InpH1FilterType == H1_FILTER_EMA) ?
                       StringFormat("EMA(%d/%d)", InpH1_EMA_Fast, InpH1_EMA_Slow) :
                       StringFormat("DoubleST(%d×%.1f / %d×%.1f)",
                                    InpST1_Period, InpST1_Multiplier,
                                    InpST2_Period, InpST2_Multiplier);

   string sltpName = (InpSLTP_Mode == SLTP_MODE_ATR) ?
                     StringFormat("ATR(%d)×%.1f | RR=%.1f", InpATR_SL_Period, InpATR_SL_Multi, InpRR_Ratio) :
                     StringFormat("Points | SL=%d pts (%.2f$) | TP=%d pts (%.2f$)",
                                  InpSL_Points, InpSL_Points * InpLotSize * 0.1,
                                  InpTP_Points, InpTP_Points * InpLotSize * 0.1);

   PrintFormat("HTF Gold EA v2.1 [MER-L1] | %s | H1: %s | SL/TP: %s",
               symbol, filterName, sltpName);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| DEINIT                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(h1_ema_fast_handle);
   IndicatorRelease(h1_ema_slow_handle);
   IndicatorRelease(m1_atr_sl_handle);
   IndicatorRelease(m1_atr_vsp_handle);
}

//+------------------------------------------------------------------+
//| TICK                                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Nouvelle bougie M1 seulement
   static datetime lastBar = 0;
   datetime currentBar = iTime(symbol, PERIOD_M1, 0);
   if(currentBar == lastBar) return;
   lastBar = currentBar;

   // Filtre horaire
   if(InpUseTimeFilter && !IsTradeHour()) return;

   // Breakeven
   if(InpUseBreakeven) CheckBreakeven();

   // Fermeture par temps
   if(InpUseTimeClose) CheckTimeClose();

   // Max trades
   if(CountOpenTrades() >= InpMaxTrades) return;

   // Copier ATR buffers
   if(CopyBuffer(m1_atr_sl_handle,  0, 0, 5, m1_atr_sl)                    < 5) return;
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, InpVSP_LookBack + 3, m1_atr_vsp) < InpVSP_LookBack + 3) return;

   // Biais H1
   int h1Bias = (InpH1FilterType == H1_FILTER_EMA) ? GetH1Bias_EMA() : GetH1Bias_SuperTrend();
   if(h1Bias == 0) return;

   // Détection VSP
   VSP_Result vsp = DetectVelocitySpike();
   if(!vsp.detected) return;

   // RÈGLE ABSOLUE : spike DOIT être strictement opposé au biais H1
   // H1 Bullish (+1) → spike baissier (-1) uniquement
   // H1 Bearish (-1) → spike haussier (+1) uniquement
   // Tout autre cas = trade contre biais interdit
   if(vsp.direction != -h1Bias)
   {
      PrintFormat("[VSP BLOQUÉ] spike=%d biais H1=%d → contre-biais interdit",
                  vsp.direction, h1Bias);
      return;
   }

   // Signal = toujours dans la direction du biais H1
   int signal = h1Bias;

   // Confirmation bougie DOIT être dans la direction du biais H1
   // H1 Bullish → confirmation haussière (close > open)
   // H1 Bearish → confirmation baissière (close < open)
   if(InpVSP_NeedConfirm)
   {
      double closeC = iClose(symbol, PERIOD_M1, 1);
      double openC  = iOpen(symbol,  PERIOD_M1, 1);
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
      // XAUUSDm : 1 point = symbole point
      // 1000 pts × 0.01 lot = 10$  →  1 pt = 0.001$ / lot
      slDist = InpSL_Points * point;
      tpDist = InpTP_Points * point;
   }

   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);

   if(signal == 1 && !HasOpenTrade(POSITION_TYPE_BUY))
   {
      double sl = NormalizeDouble(ask - slDist, digits);
      double tp = NormalizeDouble(ask + tpDist, digits);
      if(trade.Buy(InpLotSize, symbol, ask, sl, tp,
                   StringFormat("MER-L1|BUY|%s|spike@%d",
                                (InpSLTP_Mode==SLTP_MODE_ATR?"ATR":"PTS"), vsp.barIndex)))
         PrintSignal("BUY", ask, sl, tp, slDist, tpDist, vsp);
   }
   else if(signal == -1 && !HasOpenTrade(POSITION_TYPE_SELL))
   {
      double sl = NormalizeDouble(bid + slDist, digits);
      double tp = NormalizeDouble(bid - tpDist, digits);
      if(trade.Sell(InpLotSize, symbol, bid, sl, tp,
                    StringFormat("MER-L1|SELL|%s|spike@%d",
                                 (InpSLTP_Mode==SLTP_MODE_ATR?"ATR":"PTS"), vsp.barIndex)))
         PrintSignal("SELL", bid, sl, tp, slDist, tpDist, vsp);
   }
}

//+------------------------------------------------------------------+
//| DÉTECTION VELOCITY SPIKE                                         |
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

   if(CopyOpen(symbol,  PERIOD_M1, 0, barsToCheck, open)  < barsToCheck) return result;
   if(CopyClose(symbol, PERIOD_M1, 0, barsToCheck, close) < barsToCheck) return result;

   for(int i = 2; i <= InpVSP_LookBack + 1; i++)
   {
      double bodySize  = MathAbs(close[i] - open[i]);
      double threshold = m1_atr_vsp[i] * InpVSP_Spike_Multi;

      if(bodySize >= threshold)
      {
         result.detected  = true;
         result.barIndex  = i;
         result.spikeSize = bodySize;
         result.direction = (close[i] > open[i]) ? 1 : -1;
         break;
      }
   }
   return result;
}

//+------------------------------------------------------------------+
//| BIAIS H1 — EMA                                                  |
//+------------------------------------------------------------------+
int GetH1Bias_EMA()
{
   if(CopyBuffer(h1_ema_fast_handle, 0, 0, 3, h1_ema_fast) < 3) return 0;
   if(CopyBuffer(h1_ema_slow_handle, 0, 0, 3, h1_ema_slow) < 3) return 0;
   if(h1_ema_fast[0] > h1_ema_slow[0]) return  1;
   if(h1_ema_fast[0] < h1_ema_slow[0]) return -1;
   return 0;
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

   int atrH = iATR(symbol, tf, period);
   if(atrH == INVALID_HANDLE) return result;

   double atrBuf[], highBuf[], lowBuf[], closeBuf[];
   ArraySetAsSeries(atrBuf,   true);
   ArraySetAsSeries(highBuf,  true);
   ArraySetAsSeries(lowBuf,   true);
   ArraySetAsSeries(closeBuf, true);

   if(CopyBuffer(atrH,  0, 0, bars, atrBuf)   < bars ||
      CopyHigh(symbol,  tf, 0, bars, highBuf)  < bars ||
      CopyLow(symbol,   tf, 0, bars, lowBuf)   < bars ||
      CopyClose(symbol, tf, 0, bars, closeBuf) < bars)
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
//| Ferme tout trade ouvert depuis plus de InpMaxMinutes minutes    |
//+------------------------------------------------------------------+
void CheckTimeClose()
{
   datetime now = TimeCurrent();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != symbol || posInfo.Magic() != InpMagicNumber) continue;

      datetime openTime = posInfo.Time();
      int minutesOpen   = (int)((now - openTime) / 60);

      if(minutesOpen >= InpMaxMinutes)
      {
         double closePrice = (posInfo.PositionType() == POSITION_TYPE_BUY)
                             ? SymbolInfoDouble(symbol, SYMBOL_BID)
                             : SymbolInfoDouble(symbol, SYMBOL_ASK);

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
      if(posInfo.Symbol() != symbol || posInfo.Magic() != InpMagicNumber) continue;

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
      if(posInfo.Symbol() == symbol && posInfo.Magic() == InpMagicNumber) count++;
   }
   return count;
}

bool HasOpenTrade(ENUM_POSITION_TYPE posType)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != symbol || posInfo.Magic() != InpMagicNumber) continue;
      if(posInfo.PositionType() == posType) return true;
   }
   return false;
}

bool IsTradeHour()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= InpStartHour && dt.hour < InpEndHour);
}

void PrintSignal(string dir, double price, double sl, double tp,
                 double slDist, double tpDist, VSP_Result &vsp)
{
   double slPts   = slDist / point;
   double tpPts   = tpDist / point;
   // Calcul $ pour XAUUSDm : 1000 pts × 0.01 lot = 10$
   // Formule correcte XAUUSDm : Profit = Lots × 100 × Points × 0.001 = Lots × Points × 0.1
   double slUSD   = slPts * InpLotSize * 0.1;
   double tpUSD   = tpPts * InpLotSize * 0.1;
   string mode    = (InpSLTP_Mode == SLTP_MODE_ATR) ? "ATR" : "PTS";

   PrintFormat("[MER-L1][%s] %s @ %.5f | SL=%.5f (%.0f pts / %.2f$) | TP=%.5f (%.0f pts / %.2f$) | SpikeBar=%d",
               mode, dir, price, sl, slPts, slUSD, tp, tpPts, tpUSD, vsp.barIndex);
}
//+------------------------------------------------------------------+
