//+------------------------------------------------------------------+
//|              Silver Bullet ICT EA v5.3                           |
//|              XAUUSD M5                                           |
//|  LOGIQUE :                                                       |
//|  1. Trend   : EMA50/200 sur TF configurable (15m/30m/1h/2h/4h) |
//|  2. Fenetre : Silver Bullet (3 sessions NY)                     |
//|  3. Signal  : CHoCH M5 (cassure structure)                      |
//|  4. SL/TP   : points fixes                                      |
//+------------------------------------------------------------------+
#property copyright "Silver Bullet ICT v5.3"
#property version   "5.30"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//--- Enum liste deroulante TF des EMAs
enum ENUM_EMA_TF
{
   TF_M15  = 0,  // 15 minutes
   TF_M30  = 1,  // 30 minutes
   TF_H1   = 2,  // 1 heure
   TF_H2   = 3,  // 2 heures
   TF_H4   = 4,  // 4 heures
};

//--- Conversion enum -> ENUM_TIMEFRAMES
ENUM_TIMEFRAMES GetTF(ENUM_EMA_TF choice)
{
   switch(choice)
   {
      case TF_M15: return PERIOD_M15;
      case TF_M30: return PERIOD_M30;
      case TF_H1:  return PERIOD_H1;
      case TF_H2:  return PERIOD_H2;
      case TF_H4:  return PERIOD_H4;
      default:     return PERIOD_H1;
   }
}

//--- Inputs
input group "=== TREND : EMAs ==="
input ENUM_EMA_TF InpEMA_TF   = TF_H1; // Timeframe des EMAs
input int    InpEMA_Fast   = 50;        // EMA rapide
input int    InpEMA_Slow   = 200;       // EMA lente

input group "=== SILVER BULLET WINDOWS (heure broker) ==="
// Exness GMT+2 : W1=10h, W2=17h, W3=21h
// Maroc  GMT+0 : W1=08h, W2=15h, W3=19h
input bool   InpW1_Active  = true;  // Window 1 active
input int    InpW1_Start   = 10;    // W1 debut (heure broker)
input int    InpW1_End     = 11;    // W1 fin
input bool   InpW2_Active  = true;  // Window 2 active
input int    InpW2_Start   = 17;    // W2 debut
input int    InpW2_End     = 18;    // W2 fin
input bool   InpW3_Active  = false; // Window 3 active
input int    InpW3_Start   = 21;    // W3 debut
input int    InpW3_End     = 22;    // W3 fin

input group "=== FILTRE JOURS ==="
input bool   InpNoFriday   = true;  // Bloquer vendredi

input group "=== SIGNAL : CHoCH M5 ==="
input int    InpCHoCH_LB   = 10;    // Lookback bougies CHoCH

input group "=== SL / TP FIXES ==="
input int    InpSL_Points  = 200;   // Stop Loss en points
input int    InpTP_Points  = 600;   // Take Profit en points

input group "=== RISK ==="
input double InpLotSize    = 0.01;  // Taille du lot
input int    InpMagic      = 50001; // Magic number

//--- Handles
int h_EMA_Fast = INVALID_HANDLE;
int h_EMA_Slow = INVALID_HANDLE;

//--- Variables globales
datetime g_LastBar = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   h_EMA_Fast = iMA(_Symbol, GetTF(InpEMA_TF), InpEMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_EMA_Slow = iMA(_Symbol, GetTF(InpEMA_TF), InpEMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   if(h_EMA_Fast == INVALID_HANDLE || h_EMA_Slow == INVALID_HANDLE)
   {
      Print("Erreur creation handles EMA : ", GetLastError());
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   Print("=== Silver Bullet ICT v5.3 | ", _Symbol, " | M5 ===");
   Print("EMA TF:", EnumToString(GetTF(InpEMA_TF)),
         " | Fast:", InpEMA_Fast, " | Slow:", InpEMA_Slow);
   Print("SL:", InpSL_Points, "pts | TP:", InpTP_Points, "pts | Lot:", InpLotSize);
   Print("W1:", InpW1_Active ? StringFormat("ON %02dh-%02dh", InpW1_Start, InpW1_End) : "OFF",
         " | W2:", InpW2_Active ? StringFormat("ON %02dh-%02dh", InpW2_Start, InpW2_End) : "OFF",
         " | W3:", InpW3_Active ? StringFormat("ON %02dh-%02dh", InpW3_Start, InpW3_End) : "OFF");
   Print("Vendredi: ", InpNoFriday ? "BLOQUE" : "AUTORISE");
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

   //--- FILTRE 1 : Fenetre Silver Bullet
   if(!IsWindowAllowed()) return;

   //--- Une seule position a la fois
   if(HasPosition()) return;

   //--- Lire les EMAs (barre fermee = index 1)
   double emaFast = GetEMAValue(h_EMA_Fast, 1);
   double emaSlow = GetEMAValue(h_EMA_Slow, 1);
   if(emaFast == 0 || emaSlow == 0) return;

   //--- FILTRE 2 : Trend EMA
   bool trendBull = (emaFast > emaSlow);
   bool trendBear = (emaFast < emaSlow);
   if(!trendBull && !trendBear) return;

   //--- FILTRE 3 : CHoCH M5 dans le sens du trend
   bool chochBull = trendBull && DetectCHoCH(true);
   bool chochBear = trendBear && DetectCHoCH(false);
   if(!chochBull && !chochBear) return;

   //--- Executer le trade
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl_dist = InpSL_Points * _Point;
   double tp_dist = InpTP_Points * _Point;

   if(chochBull)
   {
      double sl = NormalizeDouble(ask - sl_dist, _Digits);
      double tp = NormalizeDouble(ask + tp_dist, _Digits);
      if(trade.Buy(InpLotSize, _Symbol, ask, sl, tp, "SB_BUY_v51"))
         Print("BUY @ ", ask, " SL:", sl, " TP:", tp,
               " | W:", GetWindowName(),
               " | EMA(", EnumToString(GetTF(InpEMA_TF)), ") Fast:", emaFast, " Slow:", emaSlow);
      else
         Print("Erreur BUY : ", trade.ResultRetcodeDescription());
   }
   else if(chochBear)
   {
      double sl = NormalizeDouble(bid + sl_dist, _Digits);
      double tp = NormalizeDouble(bid - tp_dist, _Digits);
      if(trade.Sell(InpLotSize, _Symbol, bid, sl, tp, "SB_SELL_v51"))
         Print("SELL @ ", bid, " SL:", sl, " TP:", tp,
               " | W:", GetWindowName(),
               " | EMA(", EnumToString(GetTF(InpEMA_TF)), ") Fast:", emaFast, " Slow:", emaSlow);
      else
         Print("Erreur SELL : ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| FILTRE SILVER BULLET WINDOW                                     |
//| Meme logique que EMA_ADX_Scalper_EA :                          |
//| TimeCurrent() = heure serveur broker                           |
//+------------------------------------------------------------------+
bool IsWindowAllowed()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h   = dt.hour;
   int dow = dt.day_of_week;

   // Bloquer weekend
   if(dow == 0 || dow == 6) return false;

   // Bloquer vendredi
   if(InpNoFriday && dow == 5) return false;

   // Verifier fenetres Silver Bullet
   bool w1 = InpW1_Active && (h >= InpW1_Start && h < InpW1_End);
   bool w2 = InpW2_Active && (h >= InpW2_Start && h < InpW2_End);
   bool w3 = InpW3_Active && (h >= InpW3_Start && h < InpW3_End);

   return (w1 || w2 || w3);
}

//+------------------------------------------------------------------+
//| NOM DE LA FENETRE ACTIVE                                        |
//+------------------------------------------------------------------+
string GetWindowName()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int h = dt.hour;
   if(InpW1_Active && h >= InpW1_Start && h < InpW1_End)
      return StringFormat("W1(%02dh-%02dh)", InpW1_Start, InpW1_End);
   if(InpW2_Active && h >= InpW2_Start && h < InpW2_End)
      return StringFormat("W2(%02dh-%02dh)", InpW2_Start, InpW2_End);
   if(InpW3_Active && h >= InpW3_Start && h < InpW3_End)
      return StringFormat("W3(%02dh-%02dh)", InpW3_Start, InpW3_End);
   return "?";
}

//+------------------------------------------------------------------+
//| DETECTION CHoCH M5                                              |
//+------------------------------------------------------------------+
bool DetectCHoCH(bool bullish)
{
   int    lb     = InpCHoCH_LB;
   double close1 = iClose(_Symbol, PERIOD_M5, 1);

   if(bullish)
   {
      int    hiBar     = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, lb, 2);
      if(hiBar < 0) return false;
      double swingHigh = iHigh(_Symbol, PERIOD_M5, hiBar);
      return (close1 > swingHigh);
   }
   else
   {
      int    loBar    = iLowest(_Symbol, PERIOD_M5, MODE_LOW, lb, 2);
      if(loBar < 0) return false;
      double swingLow = iLow(_Symbol, PERIOD_M5, loBar);
      return (close1 < swingLow);
   }
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
