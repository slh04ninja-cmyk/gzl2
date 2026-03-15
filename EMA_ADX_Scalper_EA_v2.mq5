//+------------------------------------------------------------------+
//|                                 EMA_ADX_Scalper_EA_v2.mq5       |
//|                         Expert Advisor — Version 2.0             |
//|                                                                   |
//|  NOUVEAUTES v2.0 :                                               |
//|    Filtre jours de trading (Lundi a Vendredi ON/OFF)             |
//|    Parametres defaut = meilleur backtest Jan-Mar 2026            |
//|    Profit +534$ / Win Rate 84% / Drawdown 10.68%                |
//|                                                                   |
//|  DETECTION DE TREND — 2 options (InpUseSupertrend) :            |
//|    false → EMAs 20/46/200 (recommande — backtest confirme)       |
//|    true  → Supertrend ATR                                        |
//|                                                                   |
//|  COUCHES :                                                        |
//|    COUCHE 1 : RSI filtre entree                                  |
//|    COUCHE 2 : ATR SL/TP dynamique                                |
//|    COUCHE 3a: Breakeven automatique                              |
//|    COUCHE 3b: Trailing Stop ATR                                  |
//+------------------------------------------------------------------+
#property copyright "EMA_ADX_Scalper_EA v2.0"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//  INPUTS
//+------------------------------------------------------------------+

input group "=== Detection de Trend ==="
input bool   InpUseSupertrend = false;  // false=EMAs | true=Supertrend

input group "=== Option 1 : EMAs (si InpUseSupertrend=false) ==="
input int    InpEMA21        = 20;
input int    InpEMA36        = 46;
input int    InpEMA150       = 200;
input bool   InpUseEMA200    = true;    // true=EMA20>EMA46>EMA200 | false=EMA20>EMA46

input group "=== Option 2 : Supertrend (si InpUseSupertrend=true) ==="
input int    InpST_Period    = 14;      // Periode ATR du Supertrend
input double InpST_Mult      = 3.0;    // Multiplicateur ATR du Supertrend

input group "=== ADX ==="
input int    InpADX_Period   = 21;     // ** backtest optimal
input double InpADX_Under    = 10.0;   // Seuil consolidation (<=)
input double InpADX_Confirm  = 13.0;   // Seuil sortie (>)

input group "=== COUCHE 1 : Filtre RSI ==="
input bool   InpUseRSI       = true;   // Activer filtre RSI
input int    InpRSI_Period   = 14;
input double InpRSI_BuyMin   = 60.0;  // ** backtest optimal (momentum haussier fort)
input double InpRSI_BuyMax   = 80.0;  // ** backtest optimal (eviter surachat)
input double InpRSI_SellMin  = 30.0;  // ** backtest optimal (eviter survente)
input double InpRSI_SellMax  = 40.0;  // ** backtest optimal (momentum baissier fort)

input group "=== COUCHE 2 : ATR SL/TP Dynamique ==="
input bool   InpUseATR       = true;   // true=SL/TP bases ATR | false=pips fixes
input int    InpATR_Period   = 13;     // ** backtest optimal
input double InpSL_ATR       = 5.0;   // ** backtest optimal
input double InpTP_ATR       = 6.0;   // ** backtest optimal (ratio 1:1.2)

input group "=== Gestion du Risque (SL/TP fixes si ATR desactive) ==="
input double InpLotSize      = 0.01;   // Taille du lot
input int    InpSL_Pips      = 3000;   // Stop Loss en pips  (si InpUseATR=false)
input int    InpTP_Pips      = 3000;   // Take Profit en pips (si InpUseATR=false)
input int    InpCooldown     = 6;      // Barres minimum entre signaux
input bool   InpOneTradeOnly = true;   // Une seule position a la fois

input group "=== COUCHE 3a : Breakeven Automatique ==="
input bool   InpUseBreakeven = true;   // Activer breakeven automatique
input double InpBE_ATR       = 2.0;   // Declencher BE quand profit >= ATR x valeur
input int    InpBE_Buffer    = 5;      // Points buffer au-dela du prix d entree

input group "=== COUCHE 3b : Trailing Stop ATR ==="
input bool   InpUseTrail     = true;   // Activer trailing stop ATR
input double InpTrailStart   = 4.5;   // ** backtest optimal
input double InpTrail_ATR    = 3.5;   // ** backtest optimal

input group "=== Sessions GMT+2 Exness ==="
input bool   InpUseAsie      = true;   // Session Asie    (02h - 10h)
input int    InpAsie_Start   = 2;
input int    InpAsie_End     = 10;
input bool   InpUseLondres   = true;   // Session Londres (10h - 14h)
input int    InpLondres_Start= 10;
input int    InpLondres_End  = 14;
input bool   InpUseNY        = true;   // Session NY      (14h - 21h)
input int    InpNY_Start     = 14;
input int    InpNY_End       = 21;

input group "=== Jours de Trading ==="
input bool   InpUseLundi     = true;   // Lundi
input bool   InpUseMardi     = true;   // Mardi
input bool   InpUseMercredi  = true;   // Mercredi
input bool   InpUseJeudi     = true;   // Jeudi
input bool   InpUseVendredi  = true;   // Vendredi

input group "=== Magic Number ==="
input int    InpMagic        = 202410;

//+------------------------------------------------------------------+
//  HANDLES
//+------------------------------------------------------------------+
int h_EMA21  = INVALID_HANDLE;
int h_EMA36  = INVALID_HANDLE;
int h_EMA150 = INVALID_HANDLE;
int h_ADX    = INVALID_HANDLE;
int h_RSI    = INVALID_HANDLE;
int h_ATR    = INVALID_HANDLE;
int h_ST_ATR = INVALID_HANDLE;

// Machine a etats ADX
int      g_adxState = 0;
datetime g_lastTime = 0;

// Etat interne Supertrend
double   g_st_up    = 0;
double   g_st_dn    = 0;
int      g_st_trend = 1;

//+------------------------------------------------------------------+
int OnInit()
  {
   h_EMA21  = iMA(_Symbol, PERIOD_CURRENT, InpEMA21,  0, MODE_EMA, PRICE_WEIGHTED);
   h_EMA36  = iMA(_Symbol, PERIOD_CURRENT, InpEMA36,  0, MODE_EMA, PRICE_WEIGHTED);
   h_EMA150 = iMA(_Symbol, PERIOD_CURRENT, InpEMA150, 0, MODE_EMA, PRICE_WEIGHTED);
   h_ADX    = iADXWilder(_Symbol, PERIOD_CURRENT, InpADX_Period);

   if(h_EMA21==INVALID_HANDLE || h_EMA36==INVALID_HANDLE ||
      h_EMA150==INVALID_HANDLE || h_ADX==INVALID_HANDLE)
     {
      Print("Erreur handles EMA/ADX : ", GetLastError());
      return INIT_FAILED;
     }

   if(InpUseRSI)
     {
      h_RSI = iRSI(_Symbol, PERIOD_CURRENT, InpRSI_Period, PRICE_CLOSE);
      if(h_RSI == INVALID_HANDLE)
        { Print("Erreur handle RSI : ", GetLastError()); return INIT_FAILED; }
     }

   if(InpUseATR || InpUseBreakeven || InpUseTrail)
     {
      h_ATR = iATR(_Symbol, PERIOD_CURRENT, InpATR_Period);
      if(h_ATR == INVALID_HANDLE)
        { Print("Erreur handle ATR : ", GetLastError()); return INIT_FAILED; }
     }

   if(InpUseSupertrend)
     {
      if(InpST_Period == InpATR_Period && h_ATR != INVALID_HANDLE)
         h_ST_ATR = h_ATR;
      else
        {
         h_ST_ATR = iATR(_Symbol, PERIOD_CURRENT, InpST_Period);
         if(h_ST_ATR == INVALID_HANDLE)
           { Print("Erreur handle ATR Supertrend : ", GetLastError()); return INIT_FAILED; }
        }
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   if(!InpUseAsie && !InpUseLondres && !InpUseNY)
      Print("ATTENTION : Aucune session activee !");
   if(!InpUseLundi && !InpUseMardi && !InpUseMercredi && !InpUseJeudi && !InpUseVendredi)
      Print("ATTENTION : Aucun jour de trading active !");

   Print("=====================================================");
   Print("EMA ADX Scalper EA v2.0 | ", _Symbol, " | ", EnumToString(Period()));
   Print("Trend: ", InpUseSupertrend
         ? StringFormat("SUPERTREND (Period:%d Mult:%.1f)", InpST_Period, InpST_Mult)
         : StringFormat("EMAs (%d/%d/%d EMA200:%s)",
                        InpEMA21, InpEMA36, InpEMA150,
                        InpUseEMA200?"ON":"OFF"));
   Print("ADX period:", InpADX_Period,
         " | RSI:", InpUseRSI?"ON":"OFF",
         " | ATR:", InpUseATR?"ON":"OFF",
         " | BE:", InpUseBreakeven?"ON":"OFF",
         " | Trail:", InpUseTrail?"ON":"OFF");
   Print("Jours: ",
         InpUseLundi    ? "Lun " : "",
         InpUseMardi    ? "Mar " : "",
         InpUseMercredi ? "Mer " : "",
         InpUseJeudi    ? "Jeu " : "",
         InpUseVendredi ? "Ven"  : "");
   Print("Sessions: ",
         InpUseAsie    ? StringFormat("Asie(%02d-%02d) ",    InpAsie_Start,    InpAsie_End)    : "",
         InpUseLondres ? StringFormat("Londres(%02d-%02d) ", InpLondres_Start, InpLondres_End) : "",
         InpUseNY      ? StringFormat("NY(%02d-%02d)",       InpNY_Start,      InpNY_End)      : "");
   if(InpUseATR)
      Print(StringFormat("SL=ATR x%.1f | TP=ATR x%.1f | Trail start=%.1f dist=%.1f",
            InpSL_ATR, InpTP_ATR, InpTrailStart, InpTrail_ATR));
   Print("=====================================================");

   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(h_EMA21  != INVALID_HANDLE) IndicatorRelease(h_EMA21);
   if(h_EMA36  != INVALID_HANDLE) IndicatorRelease(h_EMA36);
   if(h_EMA150 != INVALID_HANDLE) IndicatorRelease(h_EMA150);
   if(h_ADX    != INVALID_HANDLE) IndicatorRelease(h_ADX);
   if(h_RSI    != INVALID_HANDLE) IndicatorRelease(h_RSI);
   if(h_ATR    != INVALID_HANDLE) IndicatorRelease(h_ATR);
   if(h_ST_ATR != INVALID_HANDLE && h_ST_ATR != h_ATR)
      IndicatorRelease(h_ST_ATR);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   // COUCHE 3 — gestion trades actifs a chaque tick
   if(InpUseBreakeven || InpUseTrail)
      GererTradesActifs();

   // Signaux — nouvelle barre seulement
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   //================================================================
   // LECTURE INDICATEURS (barre fermee = index 1)
   //================================================================
   double ema21   = GetBuffer(h_EMA21,  0, 1);
   double ema36   = GetBuffer(h_EMA36,  0, 1);
   double ema150  = GetBuffer(h_EMA150, 0, 1);
   double adxCur  = GetBuffer(h_ADX,   0, 1);
   double dipCur  = GetBuffer(h_ADX,   1, 1);
   double dimCur  = GetBuffer(h_ADX,   2, 1);
   double adxPrev = GetBuffer(h_ADX,   0, 2);
   double dipPrev = GetBuffer(h_ADX,   1, 2);
   double dimPrev = GetBuffer(h_ADX,   2, 2);

   if(adxCur <= 0 || ema21 <= 0) return;

   //================================================================
   // MACHINE A ETATS ADX
   //================================================================
   if(adxCur <= InpADX_Under)
      g_adxState = 1;
   else if(g_adxState == 1 && adxCur > InpADX_Confirm)
      g_adxState = 2;
   else if(g_adxState == 2 && adxCur <= InpADX_Under)
      g_adxState = 1;

   if(g_adxState != 2) return;

   //================================================================
   // FILTRE JOURS DE TRADING
   // MqlDateTime.day_of_week : 0=Dim 1=Lun 2=Mar 3=Mer 4=Jeu 5=Ven 6=Sam
   //================================================================
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   bool jourAutorise = false;
   switch(dt.day_of_week)
     {
      case 1: jourAutorise = InpUseLundi;     break;
      case 2: jourAutorise = InpUseMardi;     break;
      case 3: jourAutorise = InpUseMercredi;  break;
      case 4: jourAutorise = InpUseJeudi;     break;
      case 5: jourAutorise = InpUseVendredi;  break;
      default: jourAutorise = false;           break; // Samedi=6, Dimanche=0
     }
   if(!jourAutorise) return;

   //================================================================
   // FILTRE SESSIONS GMT+2
   //================================================================
   if(InpUseAsie || InpUseLondres || InpUseNY)
     {
      int h = dt.hour;
      bool inAsie    = InpUseAsie    && (h >= InpAsie_Start    && h < InpAsie_End);
      bool inLondres = InpUseLondres && (h >= InpLondres_Start  && h < InpLondres_End);
      bool inNY      = InpUseNY      && (h >= InpNY_Start       && h < InpNY_End);
      if(!inAsie && !inLondres && !inNY) return;
     }

   //================================================================
   // COOLDOWN
   //================================================================
   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   int periodSec    = PeriodSeconds(PERIOD_CURRENT);
   if((int)((barTime - g_lastTime) / periodSec) < InpCooldown) return;

   //================================================================
   // POSITION EXISTANTE
   //================================================================
   if(InpOneTradeOnly && PositionsTotal() > 0)
     {
      for(int p = 0; p < PositionsTotal(); p++)
        {
         ulong ticket = PositionGetTicket(p);
         if(PositionSelectByTicket(ticket))
            if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
               PositionGetString(POSITION_SYMBOL) == _Symbol)
               return;
        }
     }

   //================================================================
   // DETECTION DE TREND
   //================================================================
   bool emaBull = false;
   bool emaBear = false;

   if(!InpUseSupertrend)
     {
      if(InpUseEMA200)
        {
         emaBull = (ema21 > ema36 && ema36 > ema150);
         emaBear = (ema150 > ema36 && ema36 > ema21);
        }
      else
        {
         emaBull = (ema21 > ema36);
         emaBear = (ema36 > ema21);
        }
     }
   else
     {
      double high1  = iHigh(_Symbol, PERIOD_CURRENT, 1);
      double low1   = iLow (_Symbol, PERIOD_CURRENT, 1);
      double high2  = iHigh(_Symbol, PERIOD_CURRENT, 2);
      double low2   = iLow (_Symbol, PERIOD_CURRENT, 2);
      double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
      double close2 = iClose(_Symbol, PERIOD_CURRENT, 2);
      double src1   = (high1 + low1) / 2.0;
      double src2   = (high2 + low2) / 2.0;

      double atr_st1 = GetBuffer(h_ST_ATR, 0, 1);
      double atr_st2 = GetBuffer(h_ST_ATR, 0, 2);
      if(atr_st1 <= 0 || atr_st2 <= 0) return;

      double up_raw1 = src1 - InpST_Mult * atr_st1;
      double dn_raw1 = src1 + InpST_Mult * atr_st1;

      if(g_st_up == 0 && g_st_dn == 0)
        {
         g_st_up = src2 - InpST_Mult * atr_st2;
         g_st_dn = src2 + InpST_Mult * atr_st2;
        }

      double new_up = (close2 > g_st_up) ? MathMax(up_raw1, g_st_up) : up_raw1;
      double new_dn = (close2 < g_st_dn) ? MathMin(dn_raw1, g_st_dn) : dn_raw1;

      if(g_st_trend == -1 && close1 > g_st_dn) g_st_trend =  1;
      else if(g_st_trend == 1 && close1 < g_st_up) g_st_trend = -1;

      g_st_up = new_up;
      g_st_dn = new_dn;

      emaBull = (g_st_trend ==  1);
      emaBear = (g_st_trend == -1);
     }

   if(!emaBull && !emaBear) return;

   //================================================================
   // ADX EN HAUSSE
   //================================================================
   if(adxCur <= adxPrev) return;

   //================================================================
   // CROISEMENT DI
   //================================================================
   bool crossBuy  = emaBull && (adxCur > dimCur) && (adxPrev <= dimPrev);
   bool crossSell = emaBear && (adxCur > dipCur) && (adxPrev <= dipPrev);
   if(!crossBuy && !crossSell) return;

   //================================================================
   // COUCHE 1 — FILTRE RSI
   //================================================================
   double rsiVal = 0;
   if(InpUseRSI)
     {
      rsiVal = GetBuffer(h_RSI, 0, 1);
      if(rsiVal <= 0) return;

      if(crossBuy && (rsiVal < InpRSI_BuyMin || rsiVal > InpRSI_BuyMax))
        {
         Print(StringFormat("[RSI BLOQUE BUY]  RSI=%.1f hors [%.0f-%.0f]",
               rsiVal, InpRSI_BuyMin, InpRSI_BuyMax));
         return;
        }
      if(crossSell && (rsiVal < InpRSI_SellMin || rsiVal > InpRSI_SellMax))
        {
         Print(StringFormat("[RSI BLOQUE SELL] RSI=%.1f hors [%.0f-%.0f]",
               rsiVal, InpRSI_SellMin, InpRSI_SellMax));
         return;
        }
     }

   //================================================================
   // COUCHE 2 — CALCUL SL / TP
   //================================================================
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl_dist, tp_dist;
   double atrVal = 0;

   if(InpUseATR || InpUseBreakeven || InpUseTrail)
      atrVal = GetBuffer(h_ATR, 0, 1);

   if(InpUseATR)
     {
      if(atrVal <= 0) return;
      sl_dist = NormalizeDouble(atrVal * InpSL_ATR, _Digits);
      tp_dist = NormalizeDouble(atrVal * InpTP_ATR, _Digits);
     }
   else
     {
      double pip_size = 0.01;
      sl_dist = NormalizeDouble(InpSL_Pips * pip_size, _Digits);
      tp_dist = NormalizeDouble(InpTP_Pips * pip_size, _Digits);
     }

   //================================================================
   // EXECUTION
   //================================================================
   string jourNom[] = {"Dim","Lun","Mar","Mer","Jeu","Ven","Sam"};
   string trendMode = InpUseSupertrend
                      ? StringFormat("ST(%+d)", g_st_trend)
                      : (InpUseEMA200 ? "EMA3" : "EMA2");

   if(crossBuy)
     {
      double sl = NormalizeDouble(ask - sl_dist, _Digits);
      double tp = NormalizeDouble(ask + tp_dist, _Digits);
      if(trade.Buy(InpLotSize, _Symbol, ask, sl, tp, "EMA_ADX_BUY"))
        {
         g_adxState = 0;
         g_lastTime = barTime;
         Print(StringFormat(
               "[BUY]  %s Prix:%.3f | SL:%.3f(-%.3f$) | TP:%.3f(+%.3f$)"
               " | ADX:%.1f | RSI:%.1f | ATR:%.3f | %s | BE:%s | Trail:%s",
               jourNom[dt.day_of_week],
               ask, sl, sl_dist, tp, tp_dist, adxCur, rsiVal, atrVal,
               trendMode,
               InpUseBreakeven?"ON":"OFF",
               InpUseTrail?"ON":"OFF"));
        }
      else
         Print("Erreur BUY : ", trade.ResultRetcodeDescription());
     }
   else if(crossSell)
     {
      double sl = NormalizeDouble(bid + sl_dist, _Digits);
      double tp = NormalizeDouble(bid - tp_dist, _Digits);
      if(trade.Sell(InpLotSize, _Symbol, bid, sl, tp, "EMA_ADX_SELL"))
        {
         g_adxState = 0;
         g_lastTime = barTime;
         Print(StringFormat(
               "[SELL] %s Prix:%.3f | SL:%.3f(+%.3f$) | TP:%.3f(-%.3f$)"
               " | ADX:%.1f | RSI:%.1f | ATR:%.3f | %s | BE:%s | Trail:%s",
               jourNom[dt.day_of_week],
               bid, sl, sl_dist, tp, tp_dist, adxCur, rsiVal, atrVal,
               trendMode,
               InpUseBreakeven?"ON":"OFF",
               InpUseTrail?"ON":"OFF"));
        }
      else
         Print("Erreur SELL : ", trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//  COUCHE 3 — GESTION DES TRADES ACTIFS
//+------------------------------------------------------------------+
void GererTradesActifs()
  {
   if(h_ATR == INVALID_HANDLE) return;
   double atr = GetBuffer(h_ATR, 0, 1);
   if(atr <= 0) return;

   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)  continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      double curBid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double curAsk    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      ENUM_POSITION_TYPE posType =
            (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      double profit_dist = (posType == POSITION_TYPE_BUY)
                           ? curBid - openPrice
                           : openPrice - curAsk;
      double newSL = currentSL;

      // BREAKEVEN
      if(InpUseBreakeven)
        {
         double be_trigger = atr * InpBE_ATR;
         double buf        = InpBE_Buffer * _Point;
         if(posType == POSITION_TYPE_BUY)
           {
            double be_sl = NormalizeDouble(openPrice + buf, _Digits);
            if(profit_dist >= be_trigger && currentSL < be_sl)
              {
               newSL = be_sl;
               Print(StringFormat("[BE BUY]  #%d SL:%.3f -> BE:%.3f | profit:%.3f >= trig:%.3f",
                     ticket, currentSL, newSL, profit_dist, be_trigger));
              }
           }
         else
           {
            double be_sl = NormalizeDouble(openPrice - buf, _Digits);
            if(profit_dist >= be_trigger && (currentSL > be_sl || currentSL == 0))
              {
               newSL = be_sl;
               Print(StringFormat("[BE SELL] #%d SL:%.3f -> BE:%.3f | profit:%.3f >= trig:%.3f",
                     ticket, currentSL, newSL, profit_dist, be_trigger));
              }
           }
        }

      // TRAILING STOP ATR
      if(InpUseTrail)
        {
         double trail_trigger = atr * InpTrailStart;
         double trail_dist    = NormalizeDouble(atr * InpTrail_ATR, _Digits);
         if(posType == POSITION_TYPE_BUY)
           {
            double trail_sl = NormalizeDouble(curBid - trail_dist, _Digits);
            if(profit_dist >= trail_trigger && trail_sl > newSL)
               newSL = trail_sl;
           }
         else
           {
            double trail_sl = NormalizeDouble(curAsk + trail_dist, _Digits);
            if(profit_dist >= trail_trigger && (trail_sl < newSL || newSL == 0))
               newSL = trail_sl;
           }
        }

      // APPLIQUER NOUVEAU SL
      if(MathAbs(newSL - currentSL) > _Point)
        {
         if(trade.PositionModify(ticket, newSL, currentTP))
            Print(StringFormat("[TRAIL] #%d SL:%.3f -> %.3f | profit:%.3f$ | ATR:%.3f",
                  ticket, currentSL, newSL, profit_dist, atr));
         else
            Print(StringFormat("[TRAIL ERR] #%d | %s", ticket,
                  trade.ResultRetcodeDescription()));
        }
     }
  }

//+------------------------------------------------------------------+
//  LECTURE SECURISEE D UN BUFFER
//+------------------------------------------------------------------+
double GetBuffer(int handle, int buffer, int shift)
  {
   double arr[];
   ArraySetAsSeries(arr, true);
   if(CopyBuffer(handle, buffer, shift, 1, arr) <= 0)
     {
      Print("GetBuffer erreur handle=", handle,
            " buf=", buffer, " shift=", shift,
            " err=", GetLastError());
      return 0;
     }
   return arr[0];
  }

//+------------------------------------------------------------------+
//  FIN — EMA_ADX_Scalper_EA v2.0
//+------------------------------------------------------------------+
