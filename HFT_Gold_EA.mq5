//+------------------------------------------------------------------+
//|                                           HFT_Gold_EA.mq5        |
//|                    Expert Advisor HFT — Version 2.4              |
//|                                                                   |
//|  SIGNAL : Bid/Ask Pressure Index                                  |
//|    Live    : poids spread sur 100 ticks (vrai PI)               |
//|    Backtest: BarPressure OHLC (meme echelle que vrai PI)        |
//|              BarPI = moyenne((Close-Low)/(High-Low)*2-1, 5 bars) |
//|              → InpPressureMin IDENTICAL en live ET backtest     |
//|  SL/TP    : PIPS FIXES (adaptes HFT M1)                         |
//|    SL = 80 pips  = 0.80$  (serre)                               |
//|    TP = 160 pips = 1.60$  (ratio 1:2)                           |
//|  Pas de filtre session ni jour                                   |
//+------------------------------------------------------------------+
#property copyright "HFT_Gold_EA v2.4"
#property version   "2.40"
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

input group "=== BarPressure (approximation PI pour backtest) ==="
input int    InpBarPI_Smooth  = 5;     // Bougies pour lisser le BarPI (1=instantane, 10=lisse)
// BarPI utilise la position du Close dans la bougie (High-Low range)
// Meme seuil InpPressureMin que le vrai PI en live !
// BarPI = moyenne sur N bougies de : (Close-Low)/(High-Low)*2-1
// Valeur de -1.0 (Close au bas) a +1.0 (Close au sommet)

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
double   g_barPI      = 0;  // BarPressure Index (approximation backtest)

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
   Print(StringFormat("BarPI smooth:%d barres | Seuil PI:%.2f (identique live/backtest)",
         InpBarPI_Smooth, InpPressureMin));
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
      //------------------------------------------------------------
      // BACKTEST : BarPressure Index
      //
      // Principe identique au vrai PI :
      //   Si le prix cloture en haut de la bougie
      //   → la majorite des ticks etaient haussiers → BarPI elevé
      //
      // Formule par bougie :
      //   range = High - Low
      //   Si range > 0:
      //     bar_pi = (Close - Low) / range * 2.0 - 1.0
      //     → -1.0 si Close au bas (tout baissier)
      //     →  0.0 si Close au milieu (neutre)
      //     → +1.0 si Close au sommet (tout haussier)
      //
      // BarPI final = moyenne des InpBarPI_Smooth dernieres bougies
      // → meme echelle que le vrai PI → meme seuil InpPressureMin
      //------------------------------------------------------------
      double sum_pi = 0;
      int    valid  = 0;

      for(int i = 1; i <= InpBarPI_Smooth; i++)
        {
         double hi = iHigh (_Symbol, PERIOD_CURRENT, i);
         double lo = iLow  (_Symbol, PERIOD_CURRENT, i);
         double cl = iClose(_Symbol, PERIOD_CURRENT, i);
         double range = hi - lo;
         if(range <= 0) continue;
         sum_pi += ((cl - lo) / range) * 2.0 - 1.0;
         valid++;
        }

      g_barPI = (valid > 0) ? sum_pi / valid : 0;

      // Signal : meme logique que le vrai PI
      signalBUY  = (g_barPI >  InpPressureMin);
      signalSELL = (g_barPI < -InpPressureMin);
      signalFort = (MathAbs(g_barPI) >= InpPressureStrong);
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
//  LECTURE SECURISEE D UN BUFFER INDICATEUR
//+------------------------------------------------------------------+
double GetBuffer(int handle, int buffer, int shift)
  {
   double arr[];
   ArraySetAsSeries(arr, true);
   if(CopyBuffer(handle, buffer, shift, 1, arr) <= 0) return 0;
   return arr[0];
  }

//+------------------------------------------------------------------+
//  FIN — HFT_Gold_EA v2.4
//  Prochaine etape : Filtre ATR Volatilite (idee 2)
//+------------------------------------------------------------------+
