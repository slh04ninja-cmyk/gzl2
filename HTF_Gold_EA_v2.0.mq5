//+------------------------------------------------------------------+
//|                                            HTF_Gold_EA_v2.0.mq5 |
//|                MÉTHODE MER — COUCHE 1 : Velocity Spike (VSP)    |
//|                H1 Bias : EMA OU Double SuperTrend               |
//|                M1 Entry : Épuisement momentum (VSP)             |
//|                SL : ATR-based | TP : 1.5 RR | BE auto          |
//+------------------------------------------------------------------+
#property copyright "HTF Gold EA v2.0 - MER Layer 1"
#property version   "2.00"
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

//+------------------------------------------------------------------+
//| INPUTS                                                           |
//+------------------------------------------------------------------+
input group "=== SYMBOL ==="
input string            InpSymbol           = "";            // Symbol (vide = chart actuel)

input group "=== FILTRE H1 ==="
input ENUM_H1_FILTER    InpH1FilterType     = H1_FILTER_EMA; // Filtre H1
input int               InpH1_EMA_Fast      = 50;            // [EMA] Période Fast
input int               InpH1_EMA_Slow      = 200;           // [EMA] Période Slow
input int               InpST1_Period       = 10;            // [ST1] Période ATR
input double            InpST1_Multiplier   = 1.0;           // [ST1] Multiplicateur
input int               InpST2_Period       = 20;            // [ST2] Période ATR
input double            InpST2_Multiplier   = 2.0;           // [ST2] Multiplicateur

input group "=== COUCHE 1 : VELOCITY SPIKE (VSP) ==="
input int               InpVSP_ATR_Period   = 14;            // ATR Period pour spike
input double            InpVSP_Spike_Multi  = 2.0;           // Corps bougie > X * ATR = Spike
input bool              InpVSP_NeedConfirm  = true;          // Attendre bougie confirmation
input int               InpVSP_LookBack     = 3;             // Chercher spike dans X bougies passées

input group "=== ATR RISK ==="
input int               InpATR_SL_Period    = 14;            // ATR Period pour SL
input double            InpATR_SL_Multi     = 1.5;           // Multiplicateur SL
input double            InpRR_Ratio         = 1.5;           // Risk:Reward TP
input double            InpLotSize          = 0.01;          // Lot fixe

input group "=== TRADE MANAGEMENT ==="
input int               InpMaxTrades        = 3;             // Max trades simultanés
input int               InpMagicNumber      = 202602;        // Magic Number
input int               InpSlippage         = 10;            // Slippage (points)

input group "=== BREAKEVEN ==="
input bool              InpUseBreakeven     = true;          // Activer Breakeven
input double            InpBE_Trigger_RR    = 0.8;           // Déclencher BE à X * TP dist

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
   bool  detected;      // Spike détecté
   int   direction;     // Direction du spike : 1=haussier, -1=baissier
   int   barIndex;      // Index de la bougie spike
   double spikeSize;    // Taille du corps spike
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

   // M1 ATR (SL)
   m1_atr_sl_handle  = iATR(symbol, PERIOD_M1, InpATR_SL_Period);

   // M1 ATR (VSP detector)
   m1_atr_vsp_handle = iATR(symbol, PERIOD_M1, InpVSP_ATR_Period);

   if(h1_ema_fast_handle  == INVALID_HANDLE ||
      h1_ema_slow_handle  == INVALID_HANDLE ||
      m1_atr_sl_handle    == INVALID_HANDLE ||
      m1_atr_vsp_handle   == INVALID_HANDLE)
   {
      Print("ERREUR: Création handles échouée.");
      return INIT_FAILED;
   }

   ArraySetAsSeries(h1_ema_fast, true);
   ArraySetAsSeries(h1_ema_slow, true);
   ArraySetAsSeries(m1_atr_sl,   true);
   ArraySetAsSeries(m1_atr_vsp,  true);

   string filterName = (InpH1FilterType == H1_FILTER_EMA) ?
                       StringFormat("EMA(%d/%d)", InpH1_EMA_Fast, InpH1_EMA_Slow) :
                       StringFormat("DoubleST(%d×%.1f/%d×%.1f)",
                                    InpST1_Period, InpST1_Multiplier,
                                    InpST2_Period, InpST2_Multiplier);

   PrintFormat("HTF Gold EA v2.0 [MER-L1] | %s | H1: %s | VSP multi=%.1f",
               symbol, filterName, InpVSP_Spike_Multi);
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

   // Max trades
   if(CountOpenTrades() >= InpMaxTrades) return;

   // Copier ATR
   if(CopyBuffer(m1_atr_sl_handle,  0, 0, 5, m1_atr_sl)  < 5) return;
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, InpVSP_LookBack + 3, m1_atr_vsp) < InpVSP_LookBack + 3) return;

   // Biais H1
   int h1Bias = 0;
   if(InpH1FilterType == H1_FILTER_EMA)
      h1Bias = GetH1Bias_EMA();
   else
      h1Bias = GetH1Bias_SuperTrend();
   if(h1Bias == 0) return;

   // Détection VSP
   VSP_Result vsp = DetectVelocitySpike();
   if(!vsp.detected) return;

   // Le spike doit être OPPOSÉ au biais H1 (épuisement du mouvement contre-tendance)
   // Ex: H1 bullish + spike baissier = vendeurs épuisés = BUY
   if(vsp.direction == h1Bias) return;  // Spike dans même direction = pas d'épuisement

   // Signal final = direction du biais H1
   int signal = h1Bias;

   // Vérifier confirmation si activée
   if(InpVSP_NeedConfirm)
   {
      // La bougie de confirmation (index 1 = dernière fermée) doit clôturer
      // dans la direction du signal
      double closeConfirm = iClose(symbol, PERIOD_M1, 1);
      double openConfirm  = iOpen(symbol,  PERIOD_M1, 1);

      bool bullConfirm = (closeConfirm > openConfirm); // Bougie haussière
      bool bearConfirm = (closeConfirm < openConfirm); // Bougie baissière

      if(signal ==  1 && !bullConfirm) return;
      if(signal == -1 && !bearConfirm) return;
   }

   // Calcul SL/TP
   double atr    = m1_atr_sl[1];
   if(atr <= 0) return;

   double slDist = atr * InpATR_SL_Multi;
   double tpDist = slDist * InpRR_Ratio;
   double ask    = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid    = SymbolInfoDouble(symbol, SYMBOL_BID);

   if(signal == 1 && !HasOpenTrade(POSITION_TYPE_BUY))
   {
      double sl = NormalizeDouble(ask - slDist, digits);
      double tp = NormalizeDouble(ask + tpDist, digits);
      if(trade.Buy(InpLotSize, symbol, ask, sl, tp,
                   StringFormat("MER-L1|BUY|spike@bar%d|%.5f", vsp.barIndex, vsp.spikeSize)))
         PrintSignal("BUY", ask, sl, tp, atr, vsp);
   }
   else if(signal == -1 && !HasOpenTrade(POSITION_TYPE_SELL))
   {
      double sl = NormalizeDouble(bid + slDist, digits);
      double tp = NormalizeDouble(bid - tpDist, digits);
      if(trade.Sell(InpLotSize, symbol, bid, sl, tp,
                    StringFormat("MER-L1|SELL|spike@bar%d|%.5f", vsp.barIndex, vsp.spikeSize)))
         PrintSignal("SELL", bid, sl, tp, atr, vsp);
   }
}

//+------------------------------------------------------------------+
//| DÉTECTION VELOCITY SPIKE                                         |
//| Cherche dans les X dernières bougies M1 un corps anormalement   |
//| grand (> InpVSP_Spike_Multi × ATR) = épuisement momentum        |
//+------------------------------------------------------------------+
VSP_Result DetectVelocitySpike()
{
   VSP_Result result;
   result.detected  = false;
   result.direction = 0;
   result.barIndex  = 0;
   result.spikeSize = 0;

   int barsToCheck = InpVSP_LookBack + 2;
   double open[], close[], high[], low[];
   ArraySetAsSeries(open,  true);
   ArraySetAsSeries(close, true);
   ArraySetAsSeries(high,  true);
   ArraySetAsSeries(low,   true);

   if(CopyOpen(symbol,  PERIOD_M1, 0, barsToCheck, open)  < barsToCheck) return result;
   if(CopyClose(symbol, PERIOD_M1, 0, barsToCheck, close) < barsToCheck) return result;
   if(CopyHigh(symbol,  PERIOD_M1, 0, barsToCheck, high)  < barsToCheck) return result;
   if(CopyLow(symbol,   PERIOD_M1, 0, barsToCheck, low)   < barsToCheck) return result;

   // Chercher spike dans les bougies passées (index 2 à LookBack+1)
   // Index 0 = bougie en cours (non fermée), index 1 = dernière confirmée
   // On cherche le spike dans les bougies 2..LookBack+1
   for(int i = 2; i <= InpVSP_LookBack + 1; i++)
   {
      double bodySize  = MathAbs(close[i] - open[i]);
      double atrValue  = m1_atr_vsp[i];
      double threshold = atrValue * InpVSP_Spike_Multi;

      if(bodySize >= threshold)
      {
         // Spike détecté
         result.detected  = true;
         result.barIndex  = i;
         result.spikeSize = bodySize;

         // Direction du spike
         result.direction = (close[i] > open[i]) ? 1 : -1;

         // On garde le spike le plus récent (premier trouvé)
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
   if(st1.direction == 1  && st2.direction == 1)  return  1;
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
      {
         upperBand[i] = bu; lowerBand[i] = bl; dir[i] = 1;
      }
      else
      {
         upperBand[i] = (bu < upperBand[i+1] || closeBuf[i+1] > upperBand[i+1]) ? bu : upperBand[i+1];
         lowerBand[i] = (bl > lowerBand[i+1] || closeBuf[i+1] < lowerBand[i+1]) ? bl : lowerBand[i+1];
         if(closeBuf[i] > upperBand[i])       dir[i] =  1;
         else if(closeBuf[i] < lowerBand[i])  dir[i] = -1;
         else                                  dir[i] =  dir[i+1];
      }
   }

   result.direction = dir[1];
   result.upper     = upperBand[1];
   result.lower     = lowerBand[1];
   return result;
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
         {
            trade.PositionModify(posInfo.Ticket(), open, tp);
            PrintFormat("[BE] BUY #%d → %.5f", posInfo.Ticket(), open);
         }
      }
      else if(posInfo.PositionType() == POSITION_TYPE_SELL)
      {
         double trigger = open - (open - tp) * InpBE_Trigger_RR;
         if(current <= trigger && sl > open)
         {
            trade.PositionModify(posInfo.Ticket(), open, tp);
            PrintFormat("[BE] SELL #%d → %.5f", posInfo.Ticket(), open);
         }
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

void PrintSignal(string dir, double price, double sl, double tp, double atr, VSP_Result &vsp)
{
   double slPts = MathAbs(price - sl) / point;
   double tpPts = MathAbs(price - tp) / point;
   PrintFormat("[MER-L1][VSP] %s @ %.5f | SL=%.5f(%.0fpts) | TP=%.5f(%.0fpts) | ATR=%.5f | SpikeBar=%d SpikeSize=%.5f",
               dir, price, sl, slPts, tp, tpPts, atr, vsp.barIndex, vsp.spikeSize);
}
//+------------------------------------------------------------------+
