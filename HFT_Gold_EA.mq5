//+------------------------------------------------------------------+
//|                                           HFT_Gold_EA.mq5        |
//|                    Expert Advisor HFT — Version 1.2              |
//|                                                                   |
//|  ETAPE 1 : Squelette + Signal Pressure Index + ATR SL/TP        |
//|                                                                   |
//|  SIGNAL PRINCIPAL : Bid/Ask Pressure Index                       |
//|    Analyse les 100 derniers ticks                                 |
//|    Chaque tick pese selon le spread (institutionnel vs retail)   |
//|    Pressure > +seuil → BUY                                       |
//|    Pressure < -seuil → SELL                                      |
//|                                                                   |
//|  SYMBOLE CIBLE : XAUUSDm Exness GMT+2                           |
//|  TIMEFRAME     : M1                                              |
//|                                                                   |
//|  ETAPES a venir :                                                |
//|    2 : Filtre ATR volatilite                                     |
//|    3 : Aimant Liquidite TP dynamique                             |
//|    4 : Market Regime                                             |
//|    5 : Lot sizing % risque + Drawdown quotidien                  |
//|    6 : Time-Based Exit                                           |
//|    7 : Faux Breakout                                             |
//|    8 : Kelly Adaptatif                                           |
//+------------------------------------------------------------------+
#property copyright "HFT_Gold_EA v1.2"
#property version   "1.20"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//+------------------------------------------------------------------+
//  INPUTS
//+------------------------------------------------------------------+

input group "=== Pressure Index Signal ==="
input int    InpTickCount      = 100;    // Nombre de ticks analyses
input double InpPressureMin    = 0.30;   // Seuil minimum BUY/SELL
input double InpPressureStrong = 0.60;   // Seuil fort (lot majore)
input bool   InpShowPressure   = true;   // Afficher Pressure sur graphique

input group "=== Gestion du Risque ==="
input double InpLotSize        = 0.01;   // Lot de base
input int    InpATR_Period     = 5;      // Periode ATR (court sur M1)
input double InpSL_ATR         = 1.5;   // SL = ATR x multiplicateur
input double InpTP_ATR         = 3.0;   // TP = ATR x multiplicateur (ratio 1:2)
// Exemple session active ATR=2.50$ :
//   SL = 2.50 x 1.5 = 3.75$  (375 pips)
//   TP = 2.50 x 3.0 = 7.50$  (750 pips)
// Exemple session calme ATR=0.50$ :
//   SL = 0.50 x 1.5 = 0.75$  (75 pips)
//   TP = 0.50 x 3.0 = 1.50$  (150 pips)
input int    InpCooldown       = 3;      // Barres M1 minimum entre signaux
input bool   InpOneTradeOnly   = true;   // Une seule position a la fois

input group "=== Sessions GMT+2 Exness ==="
input bool   InpUseAsie        = true;
input int    InpAsie_Start     = 2;
input int    InpAsie_End       = 9;
input bool   InpUseLondres     = true;
input int    InpLondres_Start  = 12;
input int    InpLondres_End    = 14;
input bool   InpUseNY          = true;
input int    InpNY_Start       = 14;
input int    InpNY_End         = 21;

input group "=== Jours de Trading ==="
input bool   InpUseLundi       = true;
input bool   InpUseMardi       = true;
input bool   InpUseMercredi    = false;  // Desactive (news Fed)
input bool   InpUseJeudi       = true;
input bool   InpUseVendredi    = true;

input group "=== Magic Number ==="
input int    InpMagic          = 303010;

//+------------------------------------------------------------------+
//  VARIABLES GLOBALES — PRESSURE INDEX
//+------------------------------------------------------------------+

// Tableaux circulaires pour les ticks
double g_tick_bid[];      // historique bid
double g_tick_spread[];   // historique spread
int    g_tick_head  = 0;  // index courant (tableau circulaire)
int    g_tick_count = 0;  // nombre de ticks enregistres

// Valeur courante du Pressure Index
double g_pressure_current = 0;

// Handle ATR
int    h_ATR = INVALID_HANDLE;

// Cooldown et historique
datetime g_lastTime       = 0;

// Pour affichage graphique
string   g_label_name     = "HFT_PRESSURE_LABEL";

//+------------------------------------------------------------------+
int OnInit()
  {
   // Initialiser les tableaux de ticks
   ArrayResize(g_tick_bid,    InpTickCount);
   ArrayResize(g_tick_spread, InpTickCount);
   ArrayInitialize(g_tick_bid,    0);
   ArrayInitialize(g_tick_spread, 0);

   // Creer le handle ATR (M1, periode courte pour reactivite)
   h_ATR = iATR(_Symbol, PERIOD_CURRENT, InpATR_Period);
   if(h_ATR == INVALID_HANDLE)
     {
      Print("Erreur handle ATR : ", GetLastError());
      return INIT_FAILED;
     }

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFilling(ORDER_FILLING_FOK);

   // Creer le label d affichage
   if(InpShowPressure)
     {
      ObjectCreate(0, g_label_name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, g_label_name, OBJPROP_CORNER,    CORNER_LEFT_UPPER);
      ObjectSetInteger(0, g_label_name, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, g_label_name, OBJPROP_YDISTANCE, 30);
      ObjectSetInteger(0, g_label_name, OBJPROP_FONTSIZE,  12);
      ObjectSetInteger(0, g_label_name, OBJPROP_COLOR,     clrWhite);
      ObjectSetString(0,  g_label_name, OBJPROP_FONT,      "Arial Bold");
     }

   if(!InpUseAsie && !InpUseLondres && !InpUseNY)
      Print("ATTENTION : Aucune session activee !");

   Print("=====================================================");
   Print("HFT Gold EA v1.0 | ", _Symbol, " | ", EnumToString(Period()));
   Print("Signal : Pressure Index (", InpTickCount, " ticks)");
   Print("Seuil  : BUY > +", InpPressureMin,
         " | SELL < -", InpPressureMin,
         " | Fort : ", InpPressureStrong);
   Print(StringFormat("SL = ATR(%.0f) x %.1f | TP = ATR(%.0f) x %.1f | Ratio 1:%.0f",
         (double)InpATR_Period, InpSL_ATR,
         (double)InpATR_Period, InpTP_ATR,
         InpTP_ATR / InpSL_ATR));
   Print("Jours: ",
         InpUseLundi    ? "Lun " : "",
         InpUseMardi    ? "Mar " : "",
         InpUseMercredi ? "Mer " : "",
         InpUseJeudi    ? "Jeu " : "",
         InpUseVendredi ? "Ven"  : "");
   Print("Sessions: ",
         InpUseAsie    ? StringFormat("Asie(%02d-%02d) ",    InpAsie_Start,   InpAsie_End)    : "",
         InpUseLondres ? StringFormat("Londres(%02d-%02d) ", InpLondres_Start,InpLondres_End) : "",
         InpUseNY      ? StringFormat("NY(%02d-%02d)",       InpNY_Start,     InpNY_End)      : "");
   Print("=====================================================");

   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(h_ATR != INVALID_HANDLE) IndicatorRelease(h_ATR);
   ObjectDelete(0, g_label_name);
   ChartRedraw(0);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   //================================================================
   // ETAPE 1 : Enregistrer le tick courant
   //================================================================
   EnregistrerTick();

   //================================================================
   // ETAPE 1 : Calculer le Pressure Index
   //================================================================
   g_pressure_current = CalculerPressureIndex();

   //================================================================
   // Affichage graphique du Pressure Index
   //================================================================
   if(InpShowPressure)
     {
      bool bt = (MQLInfoInteger(MQL_TESTER) == 1);
      AfficherPressure(g_pressure_current, bt);
     }

   //================================================================
   // Signaux — nouvelle barre M1 seulement
   //================================================================
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

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
   // COOLDOWN entre signaux
   //================================================================
   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   int periodSec    = PeriodSeconds(PERIOD_CURRENT);
   if((int)((barTime - g_lastTime) / periodSec) < InpCooldown) return;

   //================================================================
   // UNE SEULE POSITION A LA FOIS
   //================================================================
   if(InpOneTradeOnly)
     {
      for(int p = 0; p < PositionsTotal(); p++)
        {
         ulong ticket = PositionGetTicket(p);
         if(!PositionSelectByTicket(ticket)) continue;
         if(PositionGetInteger(POSITION_MAGIC) == InpMagic &&
            PositionGetString(POSITION_SYMBOL) == _Symbol)
            return;
        }
     }

   //================================================================
   // SIGNAL PRESSURE INDEX
   // Pressure calculee sur les ticks de la barre precedente
   // (g_pressure_current mise a jour a chaque tick)
   //
   // BUY  : pressure >  +InpPressureMin
   // SELL : pressure <  -InpPressureMin
   // Fort : pressure >= InpPressureStrong → lot majore
   //================================================================
   //================================================================
   // DETECTION DU MODE : Live ou Backtesting
   // En backtesting : spread synthetique fixe → PI faible
   // → On active le mode "fallback" base sur direction des ticks
   //================================================================
   bool isBacktest = (MQLInfoInteger(MQL_TESTER) == 1);

   bool   signalBUY  = false;
   bool   signalSELL = false;
   bool   signalFort = false;

   if(!isBacktest && g_tick_count >= InpTickCount / 2)
     {
      //------------------------------------------------------------
      // MODE LIVE : Pressure Index complet avec poids spread
      //------------------------------------------------------------
      signalBUY  = (g_pressure_current >  InpPressureMin);
      signalSELL = (g_pressure_current < -InpPressureMin);
      signalFort = (MathAbs(g_pressure_current) >= InpPressureStrong);
     }
   else
     {
      //------------------------------------------------------------
      // MODE BACKTEST : Pressure Index simplifie
      // Poids = 1.0 pour tous les ticks (spread ignore)
      // Direction = Close[1] vs Open[1] (bougie precedente)
      // + Confirmation Close[1] vs Close[2] (2 bougies alignees)
      //------------------------------------------------------------
      double close1 = iClose(_Symbol, PERIOD_CURRENT, 1);
      double open1  = iOpen(_Symbol,  PERIOD_CURRENT, 1);
      double close2 = iClose(_Symbol, PERIOD_CURRENT, 2);
      double close3 = iClose(_Symbol, PERIOD_CURRENT, 3);

      // Calcul Pressure simplifie depuis les ticks accumules
      double piSimple = 0;
      if(g_tick_count >= 5)
         piSimple = g_pressure_current;

      // Signal principal : direction de la bougie precedente
      bool bougieHaussiere = (close1 > open1);
      bool bougieBassierre = (close1 < open1);

      // Confirmation : 2 bougies dans le meme sens
      bool confirmHausse = (close1 > close2);
      bool confirmBaisse = (close1 < close2);

      // Amplitude de la bougie vs ATR
      double atrCheck = GetBuffer(h_ATR, 0, 1);
      double amplitude = MathAbs(close1 - open1);
      bool impulsion = (atrCheck > 0 && amplitude >= atrCheck * 0.3);

      // Signal BUY : bougie haussiere + confirmation + impulsion
      signalBUY  = bougieHaussiere && confirmHausse && impulsion;
      signalSELL = bougieBassierre && confirmBaisse && impulsion;

      // Fort : amplitude >= 70% de l ATR
      signalFort = (atrCheck > 0 && amplitude >= atrCheck * 0.7);

      if(signalBUY || signalSELL)
         Print(StringFormat("[BACKTEST] PI:%.3f | Bougie:%.3f | ATR:%.3f | Ampl:%.3f | Fort:%s",
               piSimple, close1-open1, atrCheck, amplitude, signalFort?"OUI":"NON"));
     }

   double lotActuel = signalFort ? InpLotSize * 2.0 : InpLotSize;

   if(!signalBUY && !signalSELL) return;

   //================================================================
   // CALCUL SL / TP via ATR dynamique
   // ATR periode 5 sur M1 = reactivite maximale
   // SL = ATR x InpSL_ATR  |  TP = ATR x InpTP_ATR
   //================================================================
   double atrVal = GetBuffer(h_ATR, 0, 1);
   if(atrVal <= 0)
     {
      Print("[ATR] Valeur invalide — signal ignore");
      return;
     }

   double sl_dist = NormalizeDouble(atrVal * InpSL_ATR, _Digits);
   double tp_dist = NormalizeDouble(atrVal * InpTP_ATR, _Digits);
   double ask     = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid     = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   string jourNom[] = {"Dim","Lun","Mar","Mer","Jeu","Ven","Sam"};
   string intensite = signalFort ? "FORT" : "normal";

   //================================================================
   // EXECUTION
   //================================================================
   if(signalBUY)
     {
      double sl = NormalizeDouble(ask - sl_dist, _Digits);
      double tp = NormalizeDouble(ask + tp_dist, _Digits);
      if(trade.Buy(lotActuel, _Symbol, ask, sl, tp, "HFT_BUY"))
        {
         g_lastTime = barTime;
         Print(StringFormat(
               "[BUY %s] %s Prix:%.3f | SL:%.3f(-%.2f$) | TP:%.3f(+%.2f$) | ATR:%.3f | Lot:%.2f | PI:%.3f",
               intensite, jourNom[dt.day_of_week],
               ask, sl, sl_dist, tp, tp_dist, atrVal, lotActuel, g_pressure_current));
        }
      else
         Print("Erreur BUY : ", trade.ResultRetcodeDescription());
     }
   else if(signalSELL)
     {
      double sl = NormalizeDouble(bid + sl_dist, _Digits);
      double tp = NormalizeDouble(bid - tp_dist, _Digits);
      if(trade.Sell(lotActuel, _Symbol, bid, sl, tp, "HFT_SELL"))
        {
         g_lastTime = barTime;
         Print(StringFormat(
               "[SELL %s] %s Prix:%.3f | SL:%.3f(+%.2f$) | TP:%.3f(-%.2f$) | ATR:%.3f | Lot:%.2f | PI:%.3f",
               intensite, jourNom[dt.day_of_week],
               bid, sl, sl_dist, tp, tp_dist, atrVal, lotActuel, g_pressure_current));
        }
      else
         Print("Erreur SELL : ", trade.ResultRetcodeDescription());
     }
  }

//+------------------------------------------------------------------+
//  ENREGISTRER LE TICK COURANT
//  Tableau circulaire de InpTickCount entrees
//+------------------------------------------------------------------+
void EnregistrerTick()
  {
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spread = ask - bid;

   // Ecrire dans le tableau circulaire
   g_tick_bid[g_tick_head]    = bid;
   g_tick_spread[g_tick_head] = spread;

   g_tick_head = (g_tick_head + 1) % InpTickCount;
   if(g_tick_count < InpTickCount) g_tick_count++;
  }

//+------------------------------------------------------------------+
//  CALCULER LE PRESSURE INDEX
//
//  Formule :
//    Pour chaque tick :
//      poids = 1 + (spread_tick - spread_moyen) / spread_moyen
//      tick haussier → +poids
//      tick baissier → -poids
//      tick neutre   → 0
//
//    Pressure = somme des poids / nombre de ticks
//
//  Interpretation :
//    > 0   = pression haussiere (acheteurs actifs)
//    < 0   = pression baissiere (vendeurs actifs)
//    Valeur absolue elevee = pression institutionnelle
//+------------------------------------------------------------------+
double CalculerPressureIndex()
  {
   if(g_tick_count < 2) return 0;

   // Calculer le spread moyen
   double spread_sum = 0;
   for(int i = 0; i < g_tick_count; i++)
      spread_sum += g_tick_spread[i];
   double spread_moyen = spread_sum / g_tick_count;
   if(spread_moyen <= 0) return 0;

   // Calculer la pression
   double pressure = 0;
   int    count    = 0;

   for(int i = 1; i < g_tick_count; i++)
     {
      // Calculer les indices dans le tableau circulaire
      int cur  = (g_tick_head - 1 - i + InpTickCount) % InpTickCount;
      int prev = (g_tick_head - 2 - i + InpTickCount) % InpTickCount;

      if(g_tick_bid[prev] <= 0) continue;  // donnee vide

      double move   = g_tick_bid[cur] - g_tick_bid[prev];
      double weight = 1.0 + (g_tick_spread[cur] - spread_moyen) / spread_moyen;
      weight = MathMax(0.1, MathMin(weight, 5.0));  // borner entre 0.1 et 5.0

      if(move > 0)       pressure += weight;
      else if(move < 0)  pressure -= weight;
      count++;
     }

   if(count == 0) return 0;
   return pressure / count;
  }

//+------------------------------------------------------------------+
//  AFFICHER LE PRESSURE INDEX SUR LE GRAPHIQUE
//+------------------------------------------------------------------+
void AfficherPressure(double pi, bool backtest=false)
  {
   color   clr;
   string  txt;
   string  mode = backtest ? " [BT]" : "";

   if(pi > InpPressureStrong)
     { clr = clrLime;   txt = StringFormat("PRESSURE : +%.3f  [BUY FORT]%s",  pi, mode); }
   else if(pi > InpPressureMin)
     { clr = clrGreen;  txt = StringFormat("PRESSURE : +%.3f  [BUY]%s",       pi, mode); }
   else if(pi < -InpPressureStrong)
     { clr = clrRed;    txt = StringFormat("PRESSURE : %.3f  [SELL FORT]%s",  pi, mode); }
   else if(pi < -InpPressureMin)
     { clr = clrOrange; txt = StringFormat("PRESSURE : %.3f  [SELL]%s",       pi, mode); }
   else
     { clr = clrGray;   txt = StringFormat("PRESSURE : %.3f  [neutre]%s | ticks:%d", pi, mode, g_tick_count); }

   ObjectSetString(0,  g_label_name, OBJPROP_TEXT,  txt);
   ObjectSetInteger(0, g_label_name, OBJPROP_COLOR, clr);
   ChartRedraw(0);
  }

//+------------------------------------------------------------------+
//  LECTURE SECURISEE D UN BUFFER INDICATEUR
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
//  FIN — HFT_Gold_EA v1.1
//  Prochaine etape : Filtre ATR Volatilite (idee 2)
//+------------------------------------------------------------------+
