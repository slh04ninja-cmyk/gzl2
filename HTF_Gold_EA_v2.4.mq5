//+------------------------------------------------------------------+
//|                                            HTF_Gold_EA_v2.4.mq5 |
//|           MER — Couche 1 : Velocity Spike (VSP)                 |
//|           Biais H1 : EMA (barre fermée + prix actuel optionnel) |
//|                    OU Double SuperTrend                          |
//|           Signal M1 : spike opposé au biais = épuisement        |
//|           SL/TP : ATR OU Points fixes                           |
//|           Options : Breakeven, Fermeture par temps              |
//+------------------------------------------------------------------+
//
//  LOGIQUE COMPLÈTE :
//  1. Lire biais H1 (EMA ou SuperTrend)
//     - EMA  : EMA_Fast[1] vs EMA_Slow[1] (barre fermée obligatoire)
//              + optionnel : prix actuel vs Open H1[0]
//     - ST   : Double SuperTrend H1, les deux alignés
//  2. Détecter Velocity Spike sur M1
//     - Corps bougie > ATR * multiplicateur dans lookback dernières bougies
//     - Direction spike DOIT être OPPOSÉE au biais H1
//       Ex: H1=BULL → cherche spike BAISSIER (vendeurs épuisés) → BUY
//           H1=BEAR → cherche spike HAUSSIER (acheteurs épuisés) → SELL
//  3. Optionnel : bougie de confirmation dans direction du biais
//  4. Entrée dans direction du biais H1
//  5. SL/TP via ATR ou points fixes
//  6. Breakeven et fermeture par temps optionnels
//
//+------------------------------------------------------------------+
#property copyright "HTF Gold EA v2.4"
#property version   "2.40"
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
   SLTP_MODE_POINTS = 1   // Points fixes
};

//+------------------------------------------------------------------+
//| INPUTS                                                           |
//+------------------------------------------------------------------+
input group "=== SYMBOLE ==="
input string            InpSymbol            = "";             // Symbole (vide = chart actuel)

input group "=== FILTRE H1 : BIAIS DIRECTIONNEL ==="
input ENUM_H1_FILTER    InpH1FilterType      = H1_FILTER_EMA;  // Méthode biais H1
input int               InpH1_EMA_Fast       = 20;             // [EMA] Période EMA Fast
input int               InpH1_EMA_Slow       = 100;            // [EMA] Période EMA Slow
input bool              InpH1_ConfirmCurrent = true;           // [EMA] Confirmer avec prix actuel vs Open H1[0]
input int               InpST1_Period        = 10;             // [ST1] Période ATR SuperTrend 1
input double            InpST1_Multiplier    = 1.0;            // [ST1] Multiplicateur SuperTrend 1
input int               InpST2_Period        = 20;             // [ST2] Période ATR SuperTrend 2
input double            InpST2_Multiplier    = 2.0;            // [ST2] Multiplicateur SuperTrend 2

input group "=== COUCHE 1 : VELOCITY SPIKE (VSP) ==="
input int               InpVSP_ATR_Period    = 15;             // Période ATR pour détection spike
input double            InpVSP_Spike_Multi   = 1.6;            // Corps > X * ATR = Spike détecté
input int               InpVSP_LookBack      = 6;              // Chercher spike dans X bougies passées [2..N+1]
input bool              InpVSP_NeedConfirm   = true;           // Bougie confirmation requise

input group "=== SL / TP ==="
input ENUM_SLTP_MODE    InpSLTP_Mode         = SLTP_MODE_ATR;  // Mode calcul SL/TP
input int               InpATR_SL_Period     = 17;             // [ATR] Période ATR pour SL
input double            InpATR_SL_Multi      = 1.6;            // [ATR] Multiplicateur SL
input double            InpRR_Ratio          = 1.4;            // [ATR] Risk:Reward (TP = SL * RR)
input int               InpSL_Points         = 5000;           // [PTS] SL en points  (5000 = 5$)
input int               InpTP_Points         = 7500;           // [PTS] TP en points  (7500 = 7.5$)

input group "=== GESTION DES TRADES ==="
input double            InpLotSize           = 0.01;           // Taille du lot fixe
input int               InpMaxTrades         = 3;              // Nombre max de trades simultanés
input int               InpMagicNumber       = 202604;         // Magic Number
input int               InpSlippage          = 10;             // Slippage maximum (points)

input group "=== BREAKEVEN ==="
input bool              InpUseBreakeven      = false;          // Activer le Breakeven automatique
input double            InpBE_TriggerRR      = 0.8;            // Déclencher BE quand profit >= X * (TP-Open)

input group "=== FERMETURE PAR TEMPS ==="
input bool              InpUseTimeClose      = false;          // Activer fermeture automatique par temps
input int               InpMaxMinutes        = 30;             // Fermer après X minutes si pas clôturé

input group "=== FILTRE HORAIRE ==="
input bool              InpUseTimeFilter     = true;           // Activer filtre horaire
input int               InpStartHour         = 7;              // Heure de début trading (broker)
input int               InpEndHour           = 20;             // Heure de fin trading (broker)

//+------------------------------------------------------------------+
//| STRUCTURES                                                       |
//+------------------------------------------------------------------+
struct SuperTrendData
{
   double upper;
   double lower;
   int    direction; // +1 = bullish, -1 = bearish, 0 = indéfini
};

struct VSP_Result
{
   bool   detected;   // true si spike trouvé
   int    direction;  // +1 = spike haussier, -1 = spike baissier
   int    barIndex;   // index de la bougie spike (2..LookBack+1)
   double bodySize;   // taille du corps du spike
};

//+------------------------------------------------------------------+
//| VARIABLES GLOBALES                                               |
//+------------------------------------------------------------------+
CTrade        trade;
CPositionInfo posInfo;

string  symbol;
int     digits;
double  point;

// Handles indicateurs
int h1_ema_fast_handle = INVALID_HANDLE;
int h1_ema_slow_handle = INVALID_HANDLE;
int m1_atr_sl_handle   = INVALID_HANDLE;
int m1_atr_vsp_handle  = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| INITIALISATION                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   point  = SymbolInfoDouble(symbol, SYMBOL_POINT);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   // Créer handles EMA H1
   h1_ema_fast_handle = iMA(symbol, PERIOD_H1, InpH1_EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h1_ema_slow_handle = iMA(symbol, PERIOD_H1, InpH1_EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);

   // Créer handles ATR M1
   m1_atr_sl_handle  = iATR(symbol, PERIOD_M1, InpATR_SL_Period);
   m1_atr_vsp_handle = iATR(symbol, PERIOD_M1, InpVSP_ATR_Period);

   if(h1_ema_fast_handle == INVALID_HANDLE || h1_ema_slow_handle == INVALID_HANDLE ||
      m1_atr_sl_handle   == INVALID_HANDLE || m1_atr_vsp_handle  == INVALID_HANDLE)
   {
      PrintFormat("[INIT ERROR] Impossible de créer les handles d'indicateurs sur %s", symbol);
      return INIT_FAILED;
   }

   // Log démarrage
   string h1Method = (InpH1FilterType == H1_FILTER_EMA)
      ? StringFormat("EMA(%d/%d)%s", InpH1_EMA_Fast, InpH1_EMA_Slow,
                     InpH1_ConfirmCurrent ? "+PrixActuel" : "")
      : StringFormat("DoubleST(%d×%.1f / %d×%.1f)",
                     InpST1_Period, InpST1_Multiplier,
                     InpST2_Period, InpST2_Multiplier);

   string sltpMethod = (InpSLTP_Mode == SLTP_MODE_ATR)
      ? StringFormat("ATR(%d)×%.1f RR=%.1f", InpATR_SL_Period, InpATR_SL_Multi, InpRR_Ratio)
      : StringFormat("PTS SL=%d(%.2f$) TP=%d(%.2f$)",
                     InpSL_Points, InpSL_Points * InpLotSize * 0.1,
                     InpTP_Points, InpTP_Points * InpLotSize * 0.1);

   PrintFormat("[HTF EA v2.4] Démarré | %s | H1: %s | M1: VSP(ATR%d×%.1f LB=%d%s) | %s",
               symbol, h1Method,
               InpVSP_ATR_Period, InpVSP_Spike_Multi, InpVSP_LookBack,
               InpVSP_NeedConfirm ? " +Confirm" : "",
               sltpMethod);

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| DÉINITIALISATION                                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   IndicatorRelease(h1_ema_fast_handle);
   IndicatorRelease(h1_ema_slow_handle);
   IndicatorRelease(m1_atr_sl_handle);
   IndicatorRelease(m1_atr_vsp_handle);
}

//+------------------------------------------------------------------+
//| TICK PRINCIPAL                                                   |
//+------------------------------------------------------------------+
void OnTick()
{
   //--- Exécuter uniquement sur nouvelle bougie M1 fermée
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(symbol, PERIOD_M1, 0);
   if(currentBarTime == lastBarTime) return;
   lastBarTime = currentBarTime;

   //--- Filtre horaire
   if(InpUseTimeFilter && !IsTradeHour()) return;

   //--- Gestion des trades ouverts
   if(InpUseBreakeven) ManageBreakeven();
   if(InpUseTimeClose) ManageTimeClose();

   //--- Ne pas ouvrir si max trades atteint
   if(CountMyTrades() >= InpMaxTrades) return;

   //=================================================================
   //  ÉTAPE 1 : Lire le biais H1
   //=================================================================
   int h1Bias = GetH1Bias();
   if(h1Bias == 0) return; // Pas de biais clair → pas de trade

   //=================================================================
   //  ÉTAPE 2 : Détecter Velocity Spike sur M1
   //  RÈGLE : le spike DOIT être dans la direction OPPOSÉE au biais
   //  Ex: H1=BULL(+1) → cherche spike BAISSIER(-1) → puis BUY
   //      H1=BEAR(-1) → cherche spike HAUSSIER(+1) → puis SELL
   //=================================================================
   VSP_Result vsp = DetectVelocitySpike(h1Bias);
   if(!vsp.detected) return;

   //=================================================================
   //  ÉTAPE 3 : Confirmation optionnelle
   //  La bougie M1[1] (dernière fermée) doit confirmer
   //  le retournement dans la direction du biais H1
   //=================================================================
   if(InpVSP_NeedConfirm)
   {
      double close1 = iClose(symbol, PERIOD_M1, 1);
      double open1  = iOpen(symbol,  PERIOD_M1, 1);

      // H1 BULL → confirmation = bougie haussière (close > open)
      if(h1Bias == 1 && close1 <= open1)
      {
         PrintFormat("[CONFIRM BLOQUÉ] H1=BULL, bougie M1[1] baissière → BUY annulé");
         return;
      }
      // H1 BEAR → confirmation = bougie baissière (close < open)
      if(h1Bias == -1 && close1 >= open1)
      {
         PrintFormat("[CONFIRM BLOQUÉ] H1=BEAR, bougie M1[1] haussière → SELL annulé");
         return;
      }
   }

   //=================================================================
   //  ÉTAPE 4 : Calculer SL et TP
   //=================================================================
   double slDist = 0.0;
   double tpDist = 0.0;

   if(InpSLTP_Mode == SLTP_MODE_ATR)
   {
      double atrBuf[];
      ArraySetAsSeries(atrBuf, true);
      if(CopyBuffer(m1_atr_sl_handle, 0, 0, 3, atrBuf) < 3) return;
      double atr = atrBuf[1]; // barre M1 fermée
      if(atr <= 0.0) return;
      slDist = atr * InpATR_SL_Multi;
      tpDist = slDist * InpRR_Ratio;
   }
   else // SLTP_MODE_POINTS
   {
      // XAUUSDm : profit = lots × 100 × points × 0.001 = lots × points × 0.1
      // 5000 pts × 0.01 lot × 0.1 = 5.00$
      slDist = InpSL_Points * point;
      tpDist = InpTP_Points * point;
   }

   //=================================================================
   //  ÉTAPE 5 : Passer l'ordre dans la direction du biais H1
   //=================================================================
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);

   if(h1Bias == 1) // BUY — H1 BULLISH, spike baissier épuisé
   {
      if(HasOpenTrade(POSITION_TYPE_BUY)) return;
      double sl = NormalizeDouble(ask - slDist, digits);
      double tp = NormalizeDouble(ask + tpDist, digits);
      if(sl <= 0 || tp <= 0) return;
      if(trade.Buy(InpLotSize, symbol, ask, sl, tp,
                   StringFormat("HTF-EA|BUY|H1=%d|spike@bar%d", h1Bias, vsp.barIndex)))
         LogTrade("BUY", ask, sl, tp, slDist, tpDist, vsp);
   }
   else if(h1Bias == -1) // SELL — H1 BEARISH, spike haussier épuisé
   {
      if(HasOpenTrade(POSITION_TYPE_SELL)) return;
      double sl = NormalizeDouble(bid + slDist, digits);
      double tp = NormalizeDouble(bid - tpDist, digits);
      if(sl <= 0 || tp <= 0) return;
      if(trade.Sell(InpLotSize, symbol, bid, sl, tp,
                    StringFormat("HTF-EA|SELL|H1=%d|spike@bar%d", h1Bias, vsp.barIndex)))
         LogTrade("SELL", bid, sl, tp, slDist, tpDist, vsp);
   }
}

//+------------------------------------------------------------------+
//| BIAIS H1 — SÉLECTEUR                                            |
//+------------------------------------------------------------------+
int GetH1Bias()
{
   if(InpH1FilterType == H1_FILTER_EMA)
      return GetH1Bias_EMA();
   else
      return GetH1Bias_SuperTrend();
}

//+------------------------------------------------------------------+
//| BIAIS H1 — MÉTHODE EMA                                          |
//|                                                                  |
//| Condition 1 (obligatoire) :                                      |
//|   EMA_Fast[1] vs EMA_Slow[1] sur barre H1 FERMÉE               |
//|   → garantit un biais stable non affecté par la barre en cours  |
//|                                                                  |
//| Condition 2 (optionnelle si InpH1_ConfirmCurrent=true) :        |
//|   Prix actuel vs Open de H1[0] (barre en cours)                 |
//|   H1 BULL → prix actuel > Open H1[0]                           |
//|   H1 BEAR → prix actuel < Open H1[0]                           |
//|   → confirme que la bougie en cours va dans la bonne direction  |
//+------------------------------------------------------------------+
int GetH1Bias_EMA()
{
   // Copier 3 valeurs pour avoir [0]=actuel, [1]=fermée, [2]=précédente
   double emaFast[], emaSlow[];
   ArraySetAsSeries(emaFast, true);
   ArraySetAsSeries(emaSlow, true);

   if(CopyBuffer(h1_ema_fast_handle, 0, 0, 3, emaFast) < 3) return 0;
   if(CopyBuffer(h1_ema_slow_handle, 0, 0, 3, emaSlow) < 3) return 0;

   // Condition 1 : barre H1 FERMÉE [1]
   int bias = 0;
   if(emaFast[1] > emaSlow[1]) bias =  1; // BULLISH
   if(emaFast[1] < emaSlow[1]) bias = -1; // BEARISH
   if(bias == 0) return 0;                // EMAs égales = indéfini

   // Condition 2 : confirmation par prix actuel vs Open H1[0]
   if(InpH1_ConfirmCurrent)
   {
      double currentBid = SymbolInfoDouble(symbol, SYMBOL_BID);
      double openH1     = iOpen(symbol, PERIOD_H1, 0);
      if(openH1 <= 0) return 0;

      // BULL confirmé uniquement si prix > Open H1[0]
      if(bias ==  1 && currentBid < openH1) return 0;
      // BEAR confirmé uniquement si prix < Open H1[0]
      if(bias == -1 && currentBid > openH1) return 0;
   }

   return bias;
}

//+------------------------------------------------------------------+
//| BIAIS H1 — MÉTHODE DOUBLE SUPERTREND                            |
//| Les deux SuperTrends doivent pointer dans la même direction     |
//+------------------------------------------------------------------+
int GetH1Bias_SuperTrend()
{
   int barsNeeded = MathMax(InpST1_Period, InpST2_Period) * 3 + 10;
   SuperTrendData st1 = CalcSuperTrend(InpST1_Period, InpST1_Multiplier, barsNeeded);
   SuperTrendData st2 = CalcSuperTrend(InpST2_Period, InpST2_Multiplier, barsNeeded);

   if(st1.direction ==  1 && st2.direction ==  1) return  1; // Les deux BULL
   if(st1.direction == -1 && st2.direction == -1) return -1; // Les deux BEAR
   return 0; // Divergence = pas de biais
}

//+------------------------------------------------------------------+
//| CALCUL SUPERTREND (natif, sans indicateur externe)              |
//+------------------------------------------------------------------+
SuperTrendData CalcSuperTrend(int period, double multiplier, int bars)
{
   SuperTrendData result;
   result.direction = 0;
   result.upper     = 0.0;
   result.lower     = 0.0;

   int atrHandle = iATR(symbol, PERIOD_H1, period);
   if(atrHandle == INVALID_HANDLE) return result;

   double atrBuf[], highBuf[], lowBuf[], closeBuf[];
   ArraySetAsSeries(atrBuf,   true);
   ArraySetAsSeries(highBuf,  true);
   ArraySetAsSeries(lowBuf,   true);
   ArraySetAsSeries(closeBuf, true);

   bool ok = (CopyBuffer(atrHandle, 0, 0, bars, atrBuf)   >= bars &&
              CopyHigh  (symbol, PERIOD_H1, 0, bars, highBuf)  >= bars &&
              CopyLow   (symbol, PERIOD_H1, 0, bars, lowBuf)   >= bars &&
              CopyClose (symbol, PERIOD_H1, 0, bars, closeBuf) >= bars);

   IndicatorRelease(atrHandle);
   if(!ok) return result;

   // Calcul des bandes (du plus ancien au plus récent)
   double upperBand[], lowerBand[];
   int    dir[];
   ArrayResize(upperBand, bars);
   ArrayResize(lowerBand, bars);
   ArrayResize(dir,       bars);

   for(int i = bars - 1; i >= 0; i--)
   {
      double hl2 = (highBuf[i] + lowBuf[i]) / 2.0;
      double bu  = hl2 + multiplier * atrBuf[i];
      double bl  = hl2 - multiplier * atrBuf[i];

      if(i == bars - 1) // Première barre (la plus ancienne)
      {
         upperBand[i] = bu;
         lowerBand[i] = bl;
         dir[i]       = 1;
      }
      else
      {
         // Upper band : ne monte que si close précédente > upper précédente
         upperBand[i] = (bu < upperBand[i+1] || closeBuf[i+1] > upperBand[i+1])
                        ? bu : upperBand[i+1];
         // Lower band : ne descend que si close précédente < lower précédente
         lowerBand[i] = (bl > lowerBand[i+1] || closeBuf[i+1] < lowerBand[i+1])
                        ? bl : lowerBand[i+1];
         // Direction
         if     (closeBuf[i] > upperBand[i]) dir[i] =  1;
         else if(closeBuf[i] < lowerBand[i]) dir[i] = -1;
         else                                dir[i] =  dir[i+1];
      }
   }

   // Retourner les données de la barre H1 FERMÉE [1]
   result.direction = dir[1];
   result.upper     = upperBand[1];
   result.lower     = lowerBand[1];
   return result;
}

//+------------------------------------------------------------------+
//| DÉTECTION VELOCITY SPIKE                                         |
//|                                                                  |
//| Cherche dans les bougies M1[2..LookBack+1] un corps anormalement|
//| grand (> ATR * multiplicateur).                                  |
//|                                                                  |
//| IMPORTANT : le spike cherché est OPPOSÉ au biais H1 :           |
//|   H1=BULL(+1) → cherche spike BAISSIER(-1) → signal BUY        |
//|   H1=BEAR(-1) → cherche spike HAUSSIER(+1) → signal SELL       |
//|                                                                  |
//| On commence à bar[2] car bar[0]=en cours, bar[1]=confirmation   |
//+------------------------------------------------------------------+
VSP_Result DetectVelocitySpike(int h1Bias)
{
   VSP_Result result;
   result.detected  = false;
   result.direction = 0;
   result.barIndex  = 0;
   result.bodySize  = 0.0;

   int totalBars = InpVSP_LookBack + 2; // bar[0..LookBack+1]

   // Copier OHLC M1
   double openBuf[], closeBuf[];
   ArraySetAsSeries(openBuf,  true);
   ArraySetAsSeries(closeBuf, true);
   if(CopyOpen (symbol, PERIOD_M1, 0, totalBars, openBuf)  < totalBars) return result;
   if(CopyClose(symbol, PERIOD_M1, 0, totalBars, closeBuf) < totalBars) return result;

   // Copier ATR M1 pour détection spike
   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(m1_atr_vsp_handle, 0, 0, totalBars, atrBuf) < totalBars) return result;

   // Scanner les bougies [2..LookBack+1]
   // bar[0] = bougie en cours (non fermée, ignorée)
   // bar[1] = bougie de confirmation (réservée)
   // bar[2..N+1] = zone de recherche du spike
   int spikeNeeded = -h1Bias; // Direction du spike cherché (opposée au biais)

   for(int i = 2; i <= InpVSP_LookBack + 1; i++)
   {
      double bodySize = MathAbs(closeBuf[i] - openBuf[i]);
      double atrVal   = atrBuf[i];

      if(atrVal <= 0.0) continue;

      if(bodySize >= atrVal * InpVSP_Spike_Multi)
      {
         // Direction de cette bougie
         int spikeDir = (closeBuf[i] > openBuf[i]) ? 1 : -1;

         // Le spike doit être DANS la direction opposée au biais
         if(spikeDir != spikeNeeded) continue;

         result.detected  = true;
         result.direction = spikeDir;
         result.barIndex  = i;
         result.bodySize  = bodySize;
         break; // Prendre le spike le plus récent
      }
   }

   return result;
}

//+------------------------------------------------------------------+
//| GESTION BREAKEVEN                                                |
//+------------------------------------------------------------------+
void ManageBreakeven()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != symbol || posInfo.Magic() != InpMagicNumber) continue;

      double openPrice = posInfo.PriceOpen();
      double sl        = posInfo.StopLoss();
      double tp        = posInfo.TakeProfit();
      double current   = posInfo.PriceCurrent();

      if(posInfo.PositionType() == POSITION_TYPE_BUY)
      {
         double trigger = openPrice + (tp - openPrice) * InpBE_TriggerRR;
         if(current >= trigger && sl < openPrice)
         {
            trade.PositionModify(posInfo.Ticket(), openPrice, tp);
            PrintFormat("[BE] BUY #%d → SL déplacé à %.5f (breakeven)", posInfo.Ticket(), openPrice);
         }
      }
      else if(posInfo.PositionType() == POSITION_TYPE_SELL)
      {
         double trigger = openPrice - (openPrice - tp) * InpBE_TriggerRR;
         if(current <= trigger && sl > openPrice)
         {
            trade.PositionModify(posInfo.Ticket(), openPrice, tp);
            PrintFormat("[BE] SELL #%d → SL déplacé à %.5f (breakeven)", posInfo.Ticket(), openPrice);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| GESTION FERMETURE PAR TEMPS                                      |
//+------------------------------------------------------------------+
void ManageTimeClose()
{
   datetime now = TimeCurrent();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() != symbol || posInfo.Magic() != InpMagicNumber) continue;

      int minutesOpen = (int)((now - posInfo.Time()) / 60);

      if(minutesOpen >= InpMaxMinutes)
      {
         if(trade.PositionClose(posInfo.Ticket(), InpSlippage))
            PrintFormat("[TIME CLOSE] #%d fermé après %d min | P&L=%.2f$",
                        posInfo.Ticket(), minutesOpen, posInfo.Profit());
         else
            PrintFormat("[TIME CLOSE] Erreur #%d : %s",
                        posInfo.Ticket(), trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
//| UTILITAIRES                                                      |
//+------------------------------------------------------------------+
int CountMyTrades()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!posInfo.SelectByIndex(i)) continue;
      if(posInfo.Symbol() == symbol && posInfo.Magic() == InpMagicNumber)
         count++;
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

void LogTrade(string direction, double price, double sl, double tp,
              double slDist, double tpDist, VSP_Result &vsp)
{
   double slPts = slDist / point;
   double tpPts = tpDist / point;
   // XAUUSDm : profit = lots × points × 0.1
   double slUSD = slPts * InpLotSize * 0.1;
   double tpUSD = tpPts * InpLotSize * 0.1;
   string mode  = (InpSLTP_Mode == SLTP_MODE_ATR) ? "ATR" : "PTS";

   PrintFormat("[HTF EA v2.4][%s] %s @ %.3f | SL=%.3f(%.0fpts/%.2f$) | TP=%.3f(%.0fpts/%.2f$) | spike bar[%d] body=%.3f",
               mode, direction, price,
               sl, slPts, slUSD,
               tp, tpPts, tpUSD,
               vsp.barIndex, vsp.bodySize);
}
//+------------------------------------------------------------------+
