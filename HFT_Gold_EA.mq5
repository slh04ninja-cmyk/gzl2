//+------------------------------------------------------------------+
//|                                           HFT_Gold_EA.mq5        |
//|                    Expert Advisor HFT — Version 1.6              |
//|          TEST MINIMAL — Un trade par bougie sans filtre          |
//+------------------------------------------------------------------+
#property copyright "HFT_Gold_EA v1.6"
#property version   "1.60"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

input double InpLotSize   = 0.01;
input int    InpSL_Pips   = 500;
input int    InpTP_Pips   = 1000;
input int    InpMagic     = 303010;

int h_ATR = INVALID_HANDLE;

int OnInit()
  {
   h_ATR = iATR(_Symbol, PERIOD_CURRENT, 5);
   if(h_ATR == INVALID_HANDLE)
     { Print("ATR error"); return INIT_FAILED; }

   // Filling mode auto
   ENUM_ORDER_TYPE_FILLING fill = ORDER_FILLING_FOK;
   uint fm = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fm & SYMBOL_FILLING_IOC) != 0) fill = ORDER_FILLING_IOC;
   else if((fm & SYMBOL_FILLING_BOC) != 0) fill = ORDER_FILLING_BOC;

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(fill);

   Print("HFT_Gold_EA v1.6 MINIMAL | Fill:", EnumToString(fill));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(h_ATR != INVALID_HANDLE) IndicatorRelease(h_ATR);
  }

void OnTick()
  {
   // Nouvelle bougie seulement
   static datetime lastBar = 0;
   datetime curBar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(curBar == lastBar) return;
   lastBar = curBar;

   // Max 1 position
   if(PositionsTotal() > 0) return;

   // Donnees
   double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
   double open1  = iOpen(_Symbol,  PERIOD_CURRENT, 1);
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // SL/TP fixes simples
   double pip    = 0.01;  // 1 pip XAUUSDm
   double sl_d   = InpSL_Pips  * pip;
   double tp_d   = InpTP_Pips  * pip;

   Print(StringFormat("Nouvelle bougie | C1=%.3f O1=%.3f | Ask=%.3f Bid=%.3f | SL=%.2f TP=%.2f",
         close1, open1, ask, bid, sl_d, tp_d));

   // Signal simple : direction de la bougie precedente
   if(close1 > open1)
     {
      double sl = NormalizeDouble(ask - sl_d, _Digits);
      double tp = NormalizeDouble(ask + tp_d, _Digits);
      Print(StringFormat("Tentative BUY ask=%.3f sl=%.3f tp=%.3f lot=%.2f", ask, sl, tp, InpLotSize));
      bool ok = trade.Buy(InpLotSize, _Symbol, ask, sl, tp, "HFT_TEST");
      Print(StringFormat("BUY result: %s code:%d", trade.ResultRetcodeDescription(), trade.ResultRetcode()));
     }
   else if(close1 < open1)
     {
      double sl = NormalizeDouble(bid + sl_d, _Digits);
      double tp = NormalizeDouble(bid - tp_d, _Digits);
      Print(StringFormat("Tentative SELL bid=%.3f sl=%.3f tp=%.3f lot=%.2f", bid, sl, tp, InpLotSize));
      bool ok = trade.Sell(InpLotSize, _Symbol, bid, sl, tp, "HFT_TEST");
      Print(StringFormat("SELL result: %s code:%d", trade.ResultRetcodeDescription(), trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
