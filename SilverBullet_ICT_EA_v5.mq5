//+------------------------------------------------------------------+
//|              Silver Bullet ICT EA v5.0                           |
//|              XAUUSD M5                                           |
//|  LOGIQUE :                                                       |
//|  1. Trend   : EMA50 > EMA200 = BUY | EMA50 < EMA200 = SELL     |
//|  2. Signal  : CHoCH M5 (cassure structure)                      |
//|  3. SL/TP   : points fixes                                      |
//|  4. Window  : 00h00 - 23h59 (toute la journee)                 |
//+------------------------------------------------------------------+
#property copyright "Silver Bullet ICT v5.0"
#property version   "5.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//--- Inputs
input group "=== TREND : EMAs ==="
input int    InpEMA_Fast   = 50;    // EMA rapide
input int    InpEMA_Slow   = 200;   // EMA lente

input group "=== SIGNAL : CHoCH M5 ==="
input int    InpCHoCH_LB   = 10;    // Lookback bougies CHoCH

input group "=== SL / TP FIXES ==="
input int    InpSL_Points  = 200;   // Stop Loss en points
input int    InpTP_Points  = 600;   // Take Profit en points

input group "=== RISK ==="
input double InpLotSize    = 0.01;  // Taille du lot
input int    InpMagic      = 50001; // Magic number

input group "=== SESSION ==="
input int    InpStartHour  = 0;     // Heure debut (broker)
input int    InpEndHour    = 23;    // Heure fin   (broker)
input bool   InpNoFriday   = true;  // Bloquer vendredi

//--- Handles
int h_EMA_Fast = INVALID_HANDLE;
int h_EMA_Slow = INVALID_HANDLE;

//--- Variables globales
datetime g_LastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   h_EMA_Fast = iMA(_Symbol, PERIOD_M5, InpEMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_EMA_Slow = iMA(_Symbol, PERIOD_M5, InpEMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   if(h_EMA_Fast == INVALID_HANDLE || h_EMA_Slow == INVALID_HANDLE)
   {
      Print("Erreur creation handles EMA : ", GetLastError());
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   Print("Silver Bullet ICT v5.0 | ", _Symbol, " | M5");
   Print("EMA Fast:", InpEMA_Fast, " Slow:", InpEMA_Slow);
   Print("SL:", InpSL_Points, "pts | TP:", InpTP_Points, "pts");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(h_EMA_Fast != INVALID_HANDLE) IndicatorRelease(h_EMA_Fast);
   if(h_EMA_Slow != INVALID_HANDLE) IndicatorRelease(h_EMA_Slow);
   Comment("");
}

//+------------------------------------------------------------------+
void OnTick()
{
   //--- Executer seulement sur nouvelle bougie M5
   datetime curBar = iTime(_Symbol, PERIOD_M5, 0);
   if(curBar == g_LastBar) return;
   g_LastBar = curBar;

   //--- Filtre session
   if(!IsSessionAllowed()) return;

   //--- Une seule position a la fois
   if(HasPosition()) return;

   //--- Lire les EMAs (barre fermee = index 1)
   double emaFast = GetEMAValue(h_EMA_Fast, 1);
   double emaSlow = GetEMAValue(h_EMA_Slow, 1);
   if(emaFast == 0 || emaSlow == 0) return;

   //--- Determiner le trend
   bool trendBull = (emaFast > emaSlow);
   bool trendBear = (emaFast < emaSlow);

   if(!trendBull && !trendBear) return;

   //--- Detecter CHoCH dans le sens du trend
   bool chochBull = trendBull && DetectCHoCH(true);
   bool chochBear = trendBear && DetectCHoCH(false);

   if(!chochBull && !chochBear) return;

   //--- Executer le trade
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl_dist = InpSL_Points * _Point;
   double tp_dist = InpTP_Points * _Point;

   if(chochBull)
   {
      double sl = NormalizeDouble(ask - sl_dist, _Digits);
      double tp = NormalizeDouble(ask + tp_dist, _Digits);
      if(trade.Buy(InpLotSize, _Symbol, ask, sl, tp, "SB_BUY"))
         Print("BUY @ ", ask, " SL:", sl, " TP:", tp,
               " | EMA50:", emaFast, " EMA200:", emaSlow);
      else
         Print("Erreur BUY : ", trade.ResultRetcodeDescription());
   }
   else if(chochBear)
   {
      double sl = NormalizeDouble(bid + sl_dist, _Digits);
      double tp = NormalizeDouble(bid - tp_dist, _Digits);
      if(trade.Sell(InpLotSize, _Symbol, bid, sl, tp, "SB_SELL"))
         Print("SELL @ ", bid, " SL:", sl, " TP:", tp,
               " | EMA50:", emaFast, " EMA200:", emaSlow);
      else
         Print("Erreur SELL : ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| DETECTION CHoCH M5                                              |
//| Bullish : cl-ture au-dessus d'un high precedent apres un LL    |
//| Bearish : cl-ture en-dessous d'un low precedent apres un HH    |
//+------------------------------------------------------------------+
bool DetectCHoCH(bool bullish)
{
   int lb = InpCHoCH_LB;

   // Prix de la derniere bougie fermee
   double close1 = iClose(_Symbol, PERIOD_M5, 1);

   if(bullish)
   {
      // Trouver le plus haut des lb dernieres bougies (hors bougie 0 et 1)
      int  hiBar = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, lb, 2);
      if(hiBar < 0) return false;
      double swingHigh = iHigh(_Symbol, PERIOD_M5, hiBar);

      // Trouver le plus bas recent (pour confirmer qu'il y avait un LL)
      int  loBar = iLowest(_Symbol, PERIOD_M5, MODE_LOW, lb, 2);
      if(loBar < 0) return false;

      // CHoCH : la bougie fermee casse au-dessus du swing high
      // et il y avait un LL recent (loBar > hiBar = le bas est plus recent)
      return (close1 > swingHigh);
   }
   else
   {
      // Trouver le plus bas des lb dernieres bougies
      int  loBar = iLowest(_Symbol, PERIOD_M5, MODE_LOW, lb, 2);
      if(loBar < 0) return false;
      double swingLow = iLow(_Symbol, PERIOD_M5, loBar);

      // Trouver le plus haut recent
      int  hiBar = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, lb, 2);
      if(hiBar < 0) return false;

      // CHoCH : la bougie fermee casse en-dessous du swing low
      return (close1 < swingLow);
   }
}

//+------------------------------------------------------------------+
//| FILTRE SESSION                                                  |
//+------------------------------------------------------------------+
bool IsSessionAllowed()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;

   // Bloquer vendredi si option active
   if(InpNoFriday && dt.day_of_week == 5) return false;

   // Bloquer weekend
   if(dt.day_of_week == 0 || dt.day_of_week == 6) return false;

   // Verifier fenetre horaire
   return (h >= InpStartHour && h <= InpEndHour);
}

//+------------------------------------------------------------------+
//| LIRE UNE EMA DEPUIS SON HANDLE                                  |
//+------------------------------------------------------------------+
double GetEMAValue(int handle, int shift)
{
   double buf[1];
   ArraySetAsSeries(buf, true);
   if(CopyBuffer(handle, 0, shift, 1, buf) < 1) return 0;
   return buf[0];
}

//+------------------------------------------------------------------+
//| VERIFIER POSITION OUVERTE                                       |
//+------------------------------------------------------------------+
bool HasPosition()
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
         if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return true;
   }
   return false;
}
//+------------------------------------------------------------------+
