//+------------------------------------------------------------------+
//|                                 EMA_ADX_Scalper_EA_v2.mq5       |
//|                         Expert Advisor — Version 2.1             |
//|                                                                   |
//|  STRATEGIE DOUBLE POSITION :                                     |
//|    T1 : SL=2500 pips | TP=4000 pips (fixe, garanti)             |
//|    T2 : SL=2500 pips | TP illimite  (BE + Trail par etapes)     |
//|                                                                   |
//|    Quand T1 atteint TP :                                         |
//|      → BE active sur T2 (SL = prix entree)                      |
//|      → Trail demarre par etapes de InpTrailStep pips             |
//|                                                                   |
//|  PARAMETRES OPTIMAUX (backtest Jan-Mar 2026) :                  |
//|    Profit +645$ | Drawdown 9.05% | 23 trades                    |
//|    EMAs 20/46/200 | RSI(10) 60-80/30-40                         |
//|    Sessions : Asie(2-9) Londres(12-14) NY(14-21)                |
//|    Jours : Lun Mar Jeu Ven (Mercredi desactive)                 |
//+------------------------------------------------------------------+
#property copyright "EMA_ADX_Scalper_EA v2.1"
#property version   "2.10"
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
input int    InpST_Period    = 14;
input double InpST_Mult      = 3.0;

input group "=== ADX ==="
input int    InpADX_Period   = 21;
input double InpADX_Under    = 10.0;   // Seuil consolidation (<=)
input double InpADX_Confirm  = 13.0;   // Seuil sortie (>)

input group "=== COUCHE 1 : Filtre RSI ==="
input bool   InpUseRSI       = true;
input int    InpRSI_Period   = 10;     // ** backtest optimal
input double InpRSI_BuyMin   = 60.0;
input double InpRSI_BuyMax   = 80.0;
input double InpRSI_SellMin  = 30.0;
input double InpRSI_SellMax  = 40.0;

input group "=== Gestion du Risque ==="
input double InpLotSize      = 0.01;   // Lot par position (T1 et T2 = meme lot)
input int    InpSL_Pips      = 2500;   // Stop Loss en pips (T1 et T2)
input int    InpTP_Pips      = 4000;   // Take Profit T1 en pips
input int    InpCooldown     = 6;      // Barres minimum entre signaux
input bool   InpOneTradeOnly = true;   // Un seul groupe T1/T2 a la fois

input group "=== BE et Trailing T2 ==="
// BE sur T2 est declenche automatiquement quand T1 atteint son TP
// Trail demarre apres le BE, avance par etapes fixes
input int    InpTrailStep    = 1000;   // Etape trail en pips (ex: 1000 pips = 10$)
// Exemple : profit T2 = 5000 pips → SL = entree + 1000 pips
//           profit T2 = 6000 pips → SL = entree + 2000 pips

input group "=== Sessions GMT+2 Exness ==="
input bool   InpUseAsie      = true;
input int    InpAsie_Start   = 2;
input int    InpAsie_End     = 9;      // ** backtest optimal
input bool   InpUseLondres   = true;
input int    InpLondres_Start= 12;     // ** backtest optimal
input int    InpLondres_End  = 14;
input bool   InpUseNY        = true;
input int    InpNY_Start     = 14;
input int    InpNY_End       = 21;

input group "=== Jours de Trading ==="
input bool   InpUseLundi     = true;
input bool   InpUseMardi     = true;
input bool   InpUseMercredi  = false;  // Desactive - faux signaux
input bool   InpUseJeudi     = true;
input bool   InpUseVendredi  = true;

input group "=== Magic Numbers ==="
input int    InpMagic        = 202410; // T1 magic
// T2 utilise automatiquement InpMagic + 1 = 202411

//+------------------------------------------------------------------+
//  HANDLES
//+------------------------------------------------------------------+
int h_EMA21  = INVALID_HANDLE;
int h_EMA36  = INVALID_HANDLE;
int h_EMA150 = INVALID_HANDLE;
int h_ADX    = INVALID_HANDLE;
int h_RSI    = INVALID_HANDLE;
int h_ST_ATR = INVALID_HANDLE;

// Machine a etats ADX
int      g_adxState = 0;
datetime g_lastTime = 0;

// Supertrend
double   g_st_up    = 0;
double   g_st_dn    = 0;
int      g_st_trend = 1;

// Suivi T1 fermeture
bool     g_t1_closed_profit = false;  // T1 a atteint son TP
datetime g_t1_open_time     = 0;      // heure ouverture du groupe actuel

//+------------------------------------------------------------------+
int OnInit()
  {
   h_EMA21  = iMA(_Symbol, PERIOD_CURRENT, InpEMA21,  0, MODE_EMA, PRICE_WEIGHTED);
   h_EMA36  = iMA(_Symbol, PERIOD_CURRENT, InpEMA36,  0, MODE_EMA, PRICE_WEIGHTED);
   h_EMA150 = iMA(_Symbol, PERIOD_CURRENT, InpEMA150, 0, MODE_EMA, PRICE_WEIGHTED);
   h_ADX    = iADXWilder(_Symbol, PERIOD_CURRENT, InpADX_Period);

   if(h_EMA21==INVALID_HANDLE || h_EMA36==INVALID_HANDLE ||
      h_EMA150==INVALID_HANDLE || h_ADX==INVALID_HANDLE)
     { Print("Erreur handles EMA/ADX"); return INIT_FAILED; }

   if(InpUseRSI)
     {
      h_RSI = iRSI(_Symbol, PERIOD_CURRENT, InpRSI_Period, PRICE_CLOSE);
      if(h_RSI==INVALID_HANDLE)
        { Print("Erreur handle RSI"); return INIT_FAILED; }
     }

   if(InpUseSupertrend)
     {
      h_ST_ATR = iATR(_Symbol, PERIOD_CURRENT, InpST_Period);
      if(h_ST_ATR==INVALID_HANDLE)
        { Print("Erreur handle ATR ST"); return INIT_FAILED; }
     }

   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   Print("=====================================================");
   Print("EMA ADX Scalper EA v2.1 | ", _Symbol, " | ", EnumToString(Period()));
   Print("Mode : DOUBLE POSITION (T1 TP fixe | T2 BE+Trail)");
   Print("T1 Magic:", InpMagic, " | T2 Magic:", InpMagic+1);
   Print("SL:", InpSL_Pips, "pips | TP T1:", InpTP_Pips, "pips | TrailStep:", InpTrailStep, "pips");
   Print("RSI period:", InpRSI_Period, " [", InpRSI_BuyMin, "-", InpRSI_BuyMax, "]");
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
   if(h_ST_ATR != INVALID_HANDLE) IndicatorRelease(h_ST_ATR);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   // Gestion T2 (BE + Trail) a chaque tick
   GererT2();

   // Detection fermeture T1 a chaque tick
   VerifierT1Ferme();

   // Signaux — nouvelle barre seulement
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   //================================================================
   // LECTURE INDICATEURS
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
   // FILTRE JOURS
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
      default: jourAutorise = false;          break;
     }
   if(!jourAutorise) return;

   //================================================================
   // FILTRE SESSIONS
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
   // UN SEUL GROUPE ACTIF A LA FOIS
   //================================================================
   if(InpOneTradeOnly)
     {
      if(GroupActif()) return;
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
      double atr1   = GetBuffer(h_ST_ATR, 0, 1);
      double atr2   = GetBuffer(h_ST_ATR, 0, 2);
      if(atr1 <= 0 || atr2 <= 0) return;
      double up_raw = src1 - InpST_Mult * atr1;
      double dn_raw = src1 + InpST_Mult * atr1;
      if(g_st_up == 0 && g_st_dn == 0)
        { g_st_up = src2 - InpST_Mult * atr2; g_st_dn = src2 + InpST_Mult * atr2; }
      double new_up = (close2 > g_st_up) ? MathMax(up_raw, g_st_up) : up_raw;
      double new_dn = (close2 < g_st_dn) ? MathMin(dn_raw, g_st_dn) : dn_raw;
      if(g_st_trend == -1 && close1 > g_st_dn) g_st_trend =  1;
      else if(g_st_trend == 1 && close1 < g_st_up) g_st_trend = -1;
      g_st_up = new_up; g_st_dn = new_dn;
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
      if(crossBuy  && (rsiVal < InpRSI_BuyMin  || rsiVal > InpRSI_BuyMax))
        { Print(StringFormat("[RSI BLOQUE BUY]  RSI=%.1f", rsiVal)); return; }
      if(crossSell && (rsiVal < InpRSI_SellMin || rsiVal > InpRSI_SellMax))
        { Print(StringFormat("[RSI BLOQUE SELL] RSI=%.1f", rsiVal)); return; }
     }

   //================================================================
   // CALCUL SL / TP
   // 1 pip = 0.01$ sur XAUUSDm et XAUUSD
   //================================================================
   double pip_size = 0.01;
   double sl_dist  = NormalizeDouble(InpSL_Pips * pip_size, _Digits);
   double tp1_dist = NormalizeDouble(InpTP_Pips * pip_size, _Digits);
   double ask      = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid      = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   string jourNom[] = {"Dim","Lun","Mar","Mer","Jeu","Ven","Sam"};

   //================================================================
   // OUVERTURE DOUBLE POSITION
   //================================================================
   if(crossBuy)
     {
      double sl   = NormalizeDouble(ask - sl_dist,  _Digits);
      double tp1  = NormalizeDouble(ask + tp1_dist, _Digits);

      // T1 — TP fixe
      trade.SetExpertMagicNumber(InpMagic);
      if(trade.Buy(InpLotSize, _Symbol, ask, sl, tp1, "T1_BUY"))
        {
         Print(StringFormat("[T1 BUY] %s Prix:%.3f | SL:%.3f | TP:%.3f (+%d pips) | RSI:%.1f",
               jourNom[dt.day_of_week], ask, sl, tp1, InpTP_Pips, rsiVal));

         // T2 — pas de TP (SL seulement, BE+Trail geres dans GererT2)
         trade.SetExpertMagicNumber(InpMagic + 1);
         if(trade.Buy(InpLotSize, _Symbol, ask, sl, 0, "T2_BUY"))
           {
            g_adxState         = 0;
            g_lastTime         = barTime;
            g_t1_closed_profit = false;
            g_t1_open_time     = TimeCurrent();
            Print(StringFormat("[T2 BUY] %s Prix:%.3f | SL:%.3f | TP:illimite | Trail:%d pips",
                  jourNom[dt.day_of_week], ask, sl, InpTrailStep));
           }
        }
     }
   else if(crossSell)
     {
      double sl   = NormalizeDouble(bid + sl_dist,  _Digits);
      double tp1  = NormalizeDouble(bid - tp1_dist, _Digits);

      // T1 — TP fixe
      trade.SetExpertMagicNumber(InpMagic);
      if(trade.Sell(InpLotSize, _Symbol, bid, sl, tp1, "T1_SELL"))
        {
         Print(StringFormat("[T1 SELL] %s Prix:%.3f | SL:%.3f | TP:%.3f (-%d pips) | RSI:%.1f",
               jourNom[dt.day_of_week], bid, sl, tp1, InpTP_Pips, rsiVal));

         // T2 — pas de TP
         trade.SetExpertMagicNumber(InpMagic + 1);
         if(trade.Sell(InpLotSize, _Symbol, bid, sl, 0, "T2_SELL"))
           {
            g_adxState         = 0;
            g_lastTime         = barTime;
            g_t1_closed_profit = false;
            g_t1_open_time     = TimeCurrent();
            Print(StringFormat("[T2 SELL] %s Prix:%.3f | SL:%.3f | TP:illimite | Trail:%d pips",
                  jourNom[dt.day_of_week], bid, sl, InpTrailStep));
           }
        }
     }
  }

//+------------------------------------------------------------------+
//  VERIFIER SI T1 A ETE FERME EN PROFIT
//  Cherche dans l historique si T1 (Magic=InpMagic) a ete ferme
//  avec un profit positif depuis l ouverture du groupe actuel
//+------------------------------------------------------------------+
void VerifierT1Ferme()
  {
   if(g_t1_closed_profit) return;       // deja detecte
   if(g_t1_open_time == 0) return;      // aucun groupe ouvert

   // T1 encore ouvert ?
   for(int p = 0; p < PositionsTotal(); p++)
     {
      ulong ticket = PositionGetTicket(p);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
         PositionGetString(POSITION_SYMBOL) == _Symbol)
         return;  // T1 toujours ouvert
     }

   // T1 n est plus ouvert → chercher dans l historique
   datetime from = g_t1_open_time - 60;
   HistorySelect(from, TimeCurrent() + 1);

   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
     {
      ulong dTicket = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(dTicket, DEAL_MAGIC)  != InpMagic)  continue;
      if(HistoryDealGetString(dTicket,  DEAL_SYMBOL) != _Symbol)   continue;
      if(HistoryDealGetInteger(dTicket, DEAL_ENTRY)  != DEAL_ENTRY_OUT) continue;
      if(HistoryDealGetDouble(dTicket,  DEAL_TIME)   < (double)g_t1_open_time) continue;

      double profit = HistoryDealGetDouble(dTicket, DEAL_PROFIT);
      if(profit > 0)
        {
         g_t1_closed_profit = true;
         Print(StringFormat("[T1 TP TOUCHE] profit=+%.2f$ → BE active sur T2", profit));
        }
      else
        {
         // T1 ferme en perte (SL touche) → fermer T2 aussi
         Print("[T1 SL TOUCHE] → Fermeture T2");
         FermerT2();
        }
      break;
     }
  }

//+------------------------------------------------------------------+
//  GERER T2 — BE + TRAIL PAR ETAPES
//  Appele a chaque tick
//+------------------------------------------------------------------+
void GererT2()
  {
   if(g_t1_open_time == 0) return;

   double pip_size   = 0.01;
   double trail_dist = InpTrailStep * pip_size;

   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (InpMagic + 1)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

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

      //--------------------------------------------------------------
      // BREAKEVEN — declenche quand T1 a touche son TP
      //--------------------------------------------------------------
      if(g_t1_closed_profit)
        {
         if(posType == POSITION_TYPE_BUY)
           {
            double be_sl = NormalizeDouble(openPrice + _Point, _Digits);
            if(currentSL < be_sl)
              {
               newSL = be_sl;
               Print(StringFormat("[BE T2 BUY]  #%d SL:%.3f → %.3f (entree)", ticket, currentSL, newSL));
              }
           }
         else
           {
            double be_sl = NormalizeDouble(openPrice - _Point, _Digits);
            if(currentSL > be_sl || currentSL == 0)
              {
               newSL = be_sl;
               Print(StringFormat("[BE T2 SELL] #%d SL:%.3f → %.3f (entree)", ticket, currentSL, newSL));
              }
           }

         //-----------------------------------------------------------
         // TRAIL PAR ETAPES — uniquement apres BE
         // Chaque InpTrailStep pips de profit supplementaire
         // au-dela du TP T1 → SL avance d un step
         //
         // profit_dist = distance depuis entree en $
         // tp1_dist    = InpTP_Pips x pip_size
         // profit au-dela du TP T1 = profit_dist - tp1_dist
         // etapes = floor((profit_dist - tp1_dist) / trail_dist)
         // nouveau SL = entree + etapes x trail_dist
         //-----------------------------------------------------------
         double tp1_dist    = InpTP_Pips * pip_size;
         double beyond_tp1  = profit_dist - tp1_dist;

         if(beyond_tp1 > 0)
           {
            int    etapes    = (int)MathFloor(beyond_tp1 / trail_dist);
            double trail_sl_dist = etapes * trail_dist;

            if(posType == POSITION_TYPE_BUY)
              {
               double trail_sl = NormalizeDouble(openPrice + trail_sl_dist, _Digits);
               if(trail_sl > newSL)
                 {
                  newSL = trail_sl;
                 }
              }
            else
              {
               double trail_sl = NormalizeDouble(openPrice - trail_sl_dist, _Digits);
               if(trail_sl < newSL || newSL <= openPrice)
                 {
                  newSL = trail_sl;
                 }
              }
           }
        }

      //--------------------------------------------------------------
      // APPLIQUER NOUVEAU SL
      //--------------------------------------------------------------
      if(MathAbs(newSL - currentSL) > _Point)
        {
         if(trade.SetExpertMagicNumber(InpMagic + 1),
            trade.PositionModify(ticket, newSL, currentTP))
            Print(StringFormat("[TRAIL T2] #%d SL:%.3f → %.3f | profit:%.3f$",
                  ticket, currentSL, newSL, profit_dist));
         else
            Print(StringFormat("[TRAIL T2 ERR] #%d | %s", ticket, trade.ResultRetcodeDescription()));
        }
     }
  }

//+------------------------------------------------------------------+
//  FERMER T2 (si T1 ferme en SL)
//+------------------------------------------------------------------+
void FermerT2()
  {
   for(int p = PositionsTotal() - 1; p >= 0; p--)
     {
      ulong ticket = PositionGetTicket(p);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (InpMagic + 1)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      trade.SetExpertMagicNumber(InpMagic + 1);
      if(trade.PositionClose(ticket))
         Print(StringFormat("[T2 FERME] #%d suite SL T1", ticket));
     }
   g_t1_open_time     = 0;
   g_t1_closed_profit = false;
  }

//+------------------------------------------------------------------+
//  VERIFIER SI UN GROUPE T1/T2 EST ENCORE ACTIF
//+------------------------------------------------------------------+
bool GroupActif()
  {
   for(int p = 0; p < PositionsTotal(); p++)
     {
      ulong ticket = PositionGetTicket(p);
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(magic == InpMagic || magic == InpMagic + 1)
         return true;
     }
   return false;
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
      Print("GetBuffer erreur handle=", handle, " err=", GetLastError());
      return 0;
     }
   return arr[0];
  }

//+------------------------------------------------------------------+
//  FIN — EMA_ADX_Scalper_EA v2.1
//+------------------------------------------------------------------+
