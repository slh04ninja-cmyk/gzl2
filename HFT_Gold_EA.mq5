//+------------------------------------------------------------------+
//|                                           HFT_Gold_EA.mq5        |
//|                    Expert Advisor HFT — Version 2.2              |
//|                                                                   |
//|  SIGNAL : Bid/Ask Pressure Index                                  |
//|    Live    : poids spread sur 100 ticks                          |
//|    Backtest: direction bougie M1 + confirmation + amplitude      |
//|  SL/TP    : PIPS FIXES (adaptes HFT M1)                         |
//|    SL = 80 pips  = 0.80$  (serré)                               |
//|    TP = 160 pips = 1.60$  (ratio 1:2)                           |
//|  Pas de filtre session ni jour                                   |
//+------------------------------------------------------------------+
#property copyright "HFT_Gold_EA v2.2"
#property version   "2.20"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//  INPUTS
//+------------------------------------------------------------------+
input group "=== Pressure Index ==="
input int    InpTickCount      = 100;   // Ticks analyses
input double InpPressureMin    = 0.30;  // Seuil signal normal
input double InpPressureStrong = 0.60;  // Seuil signal fort (lot x2)

input group "=== Gestion du Risque ==="
input double InpLotSize        = 0.01;  // Lot de base
input int    InpSL_Pips        = 80;    // Stop Loss en pips  (80 pips = 0.80$)
input int    InpTP_Pips        = 160;   // Take Profit en pips (160 pips = 1.60$, ratio 1:2)
// XAUUSDm Exness : 1 pip = 0.01$
// SL  80 pips = 0.80$ | TP 160 pips = 1.60$ → perte max 0.80$ par trade
// SL 100 pips = 1.00$ | TP 200 pips = 2.00$ → version plus large
// SL 150 pips = 1.50$ | TP 300 pips = 3.00$ → version swing M1
input int    InpCooldown       = 3;     // Barres M1 min entre signaux
input bool   InpOneTradeOnly   = true;  // Une seule position a la fois

input group "=== Magic Number ==="
input int    InpMagic          = 303010;

//+------------------------------------------------------------------+
//  VARIABLES GLOBALES
//+------------------------------------------------------------------+
double   g_tick_bid[];
double   g_tick_spread[];
int      g_tick_head  = 0;
int      g_tick_count = 0;
double   g_pressure   = 0;
datetime g_lastTime   = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   // Tableaux ticks
   ArrayResize(g_tick_bid,    InpTickCount);
   ArrayResize(g_tick_spread, InpTickCount);
   ArrayInitialize(g_tick_bid,    0);
   ArrayInitialize(g_tick_spread, 0);

   // Filling mode auto
   ENUM_ORDER_TYPE_FILLING fill = ORDER_FILLING_FOK;
   uint fm = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fm & SYMBOL_FILLING_IOC) != 0)      fill = ORDER_FILLING_IOC;
   else if((fm & SYMBOL_FILLING_BOC) != 0) fill = ORDER_FILLING_BOC;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(fill);

   Print("=====================================================");
   Print("HFT Gold EA v2.2 | ", _Symbol, " | M1");
   Print("Fill:", EnumToString(fill));
   Print(StringFormat("SL:%d pips (%.2f$) | TP:%d pips (%.2f$) | Ratio 1:%.0f",
         InpSL_Pips, InpSL_Pips*0.01,
         InpTP_Pips, InpTP_Pips*0.01,
         (double)InpTP_Pips/InpSL_Pips));
   Print("PI seuil:", InpPressureMin, " fort:", InpPressureStrong,
         " | Cooldown:", InpCooldown, " barres");
   Print("=====================================================");
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   // Enregistrer chaque tick pour Pressure Index
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spread = ask - bid;
   g_tick_bid[g_tick_head]    = bid;
   g_tick_spread[g_tick_head] = spread;
   g_tick_head = (g_tick_head + 1) % InpTickCount;
   if(g_tick_count < InpTickCount) g_tick_count++;

   // Nouvelle bougie seulement
   static datetime lastBar = 0;
   datetime curBar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(curBar == lastBar || curBar == 0) return;
   lastBar = curBar;

   // Calculer Pressure Index a l ouverture de chaque bougie
   g_pressure = CalculerPI();

   // Cooldown
   datetime barTime  = iTime(_Symbol, PERIOD_CURRENT, 1);
   int      periodSec = PeriodSeconds(PERIOD_CURRENT);
   if(g_lastTime == 0)
      g_lastTime = barTime - (datetime)(InpCooldown * periodSec + 1);
   if((int)((barTime - g_lastTime) / periodSec) < InpCooldown) return;

   // Une seule position
   if(InpOneTradeOnly)
     {
      for(int p = 0; p < PositionsTotal(); p++)
        {
         ulong t = PositionGetTicket(p);
         if(!PositionSelectByTicket(t)) continue;
         if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return;
        }
     }

   // Donnees bougie
   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double open1  = iOpen(_Symbol,  PERIOD_CURRENT, 1);
   double close2 = iClose(_Symbol, PERIOD_CURRENT, 2);
   if(close1 <= 0 || open1 <= 0) return;

   //================================================================
   // SIGNAL
   // Live    : Pressure Index avec poids spread
   // Backtest: direction bougie M1 + confirmation
   //================================================================
   bool isBacktest = (MQLInfoInteger(MQL_TESTER) == 1);
   bool signalBUY  = false;
   bool signalSELL = false;
   bool signalFort = false;

   if(!isBacktest && g_tick_count >= InpTickCount / 2)
     {
      // LIVE : Pressure Index
      signalBUY  = (g_pressure >  InpPressureMin);
      signalSELL = (g_pressure < -InpPressureMin);
      signalFort = (MathAbs(g_pressure) >= InpPressureStrong);
     }
   else
     {
      // BACKTEST : direction bougie + confirmation + filtre amplitude
      // Amplitude >= 0.50$ : evite les micro-bougies (50 pips minimum)
      // Amplitude >= 1.00$ : signal fort (100 pips)
      double ampl     = MathAbs(close1 - open1);
      bool   impulsion = (ampl >= 0.50);   // 50 pips minimum
      signalBUY   = (close1 > open1) && (close1 > close2) && impulsion;
      signalSELL  = (close1 < open1) && (close1 < close2) && impulsion;
      signalFort  = (ampl >= 1.00);        // 100 pips = signal fort
     }

   if(!signalBUY && !signalSELL) return;

   // SL/TP FIXES en pips (adaptes HFT M1)
   // 1 pip = 0.01$ sur XAUUSDm Exness
   double pip_size = 0.01;
   double sl_d = NormalizeDouble(InpSL_Pips * pip_size, _Digits);
   double tp_d = NormalizeDouble(InpTP_Pips * pip_size, _Digits);
   // Lot double seulement en live si signal fort
   bool   isLive = (!isBacktest && g_tick_count >= InpTickCount / 2);
   double lot    = (isLive && signalFort) ? InpLotSize * 2.0 : InpLotSize;

   if(signalBUY)
     {
      double sl = NormalizeDouble(ask - sl_d, _Digits);
      double tp = NormalizeDouble(ask + tp_d, _Digits);
      if(trade.Buy(lot, _Symbol, ask, sl, tp, "HFT_BUY"))
        {
         g_lastTime = barTime;
         Print(StringFormat("[BUY%s] %.3f | SL:%.3f(-%.2f$) | TP:%.3f(+%.2f$) | PI:%.3f",
               signalFort?" FORT":"", ask, sl, sl_d, tp, tp_d, g_pressure));
        }
      else
         Print(StringFormat("[ERR BUY] code:%d %s", trade.ResultRetcode(),
               trade.ResultRetcodeDescription()));
     }
   else
     {
      double sl = NormalizeDouble(bid + sl_d, _Digits);
      double tp = NormalizeDouble(bid - tp_d, _Digits);
      if(trade.Sell(lot, _Symbol, bid, sl, tp, "HFT_SELL"))
        {
         g_lastTime = barTime;
         Print(StringFormat("[SELL%s] %.3f | SL:%.3f(+%.2f$) | TP:%.3f(-%.2f$) | PI:%.3f",
               signalFort?" FORT":"", bid, sl, sl_d, tp, tp_d, g_pressure));
        }
      else
         Print(StringFormat("[ERR SELL] code:%d %s", trade.ResultRetcode(),
               trade.ResultRetcodeDescription()));
     }
  }

//+------------------------------------------------------------------+
//  CALCUL PRESSURE INDEX
//  Tableau circulaire — acces securise
//+------------------------------------------------------------------+
double CalculerPI()
  {
   if(g_tick_count < 2) return 0;

   // Spread moyen
   double sum_s = 0;
   int    n     = MathMin(g_tick_count, InpTickCount);
   for(int i = 0; i < n; i++)
      sum_s += g_tick_spread[i];
   double s_moy = (n > 0) ? sum_s / n : 0;
   if(s_moy <= 0) return 0;

   // Calcul pression
   double pression = 0;
   int    count    = 0;
   for(int i = 1; i < n; i++)
     {
      int cur  = (g_tick_head - 1 - i + InpTickCount) % InpTickCount;
      int prev = (g_tick_head - 2 - i + InpTickCount) % InpTickCount;

      // Securite acces tableau
      if(cur < 0 || cur >= InpTickCount) continue;
      if(prev < 0 || prev >= InpTickCount) continue;
      if(g_tick_bid[prev] <= 0) continue;

      double move   = g_tick_bid[cur] - g_tick_bid[prev];
      double weight = 1.0 + (g_tick_spread[cur] - s_moy) / s_moy;
      weight = MathMax(0.1, MathMin(weight, 5.0));

      if(move > 0)       pression += weight;
      else if(move < 0)  pression -= weight;
      count++;
     }

   return (count > 0) ? pression / count : 0;
  }

//+------------------------------------------------------------------+
//  FIN — HFT_Gold_EA v2.2
//  Prochaine etape : Filtre ATR Volatilite (idee 2)
//+------------------------------------------------------------------+
