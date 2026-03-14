//+------------------------------------------------------------------+
//|                    Silver Bullet ICT EA v4.2                     |
//|                  XAUUSD M5 - Heure GMT pure                      |
//|         Maroc GMT+0 | NY Sessions | Top-Down MTF                 |
//+------------------------------------------------------------------+
#property copyright   "ICT Strategy EA"
#property version     "4.20"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade         trade;
CPositionInfo  posInfo;

//+------------------------------------------------------------------+
//|                     INPUT PARAMETERS                             |
//+------------------------------------------------------------------+

input group "=== SILVER BULLET ICT v4.0 ==="
input int      MagicNumber    = 20240101;
input bool     DebugMode      = true;   // Afficher logs de diagnostic

//--------------------------------------------------------------------
// METHODE BIAIS
//--------------------------------------------------------------------
input group "=== METHODE BIAIS ==="
input bool   UseBiasEMA       = true;
// true  = EMA | false = Structure ICT pure

//--------------------------------------------------------------------
// CASCADE - FILTRES OPTIONNELS
// IMPORTANT : desactiver les filtres un par un pour diagnostiquer
//--------------------------------------------------------------------
input group "=== CASCADE - ACTIVATION DES FILTRES ==="
input bool   UseMacroFilter   = false;  // Filtre macro (D1/H8) - OFF par defaut
input bool   UseConfirmFilter = true;   // Filtre confirmation (M15/M10)
input bool   UseCHoCH_Filter  = true;   // Filtre CHoCH M5
input bool   UseFVG_Filter    = true;   // Filtre FVG M5 (entree dans zone)
// NOTE : commencer avec MacroFilter=false pour avoir plus de signaux

//--------------------------------------------------------------------
// NIVEAU 1 - MACRO FILTER (H8 ou D1)
//--------------------------------------------------------------------
input group "=== NIVEAU 1 : MACRO (H8 ou D1) ==="
input ENUM_TIMEFRAMES  MacroTF           = PERIOD_D1;
input int   Macro_EMA1_Period  = 50;
input int   Macro_EMA2_Period  = 0;     // 0 = desactive (prix vs EMA1)
input int   Macro_Struct_LB    = 5;

//--------------------------------------------------------------------
// NIVEAU 2 - BIAIS PRINCIPAL (H1 ou H4)
//--------------------------------------------------------------------
input group "=== NIVEAU 2 : BIAIS (H1 ou H4) ==="
input ENUM_TIMEFRAMES  BiaisTF           = PERIOD_H4;
input int   Biais_EMA1_Period  = 20;
input int   Biais_EMA2_Period  = 50;
input int   Biais_Struct_LB    = 10;
input bool  Biais_RequireBOS   = false; // Exiger BOS en plus des EMA
// false = EMA seul suffit (plus de signaux)
// true  = EMA + BOS obligatoire (moins mais plus fiable)

//--------------------------------------------------------------------
// NIVEAU 3 - CONFIRMATION (M15 ou M10)
//--------------------------------------------------------------------
input group "=== NIVEAU 3 : CONFIRMATION (M15 ou M10) ==="
input ENUM_TIMEFRAMES  ConfirmTF         = PERIOD_M15;
input int   Confirm_EMA1_Period = 9;
input int   Confirm_EMA2_Period = 21;
input int   Confirm_Struct_LB   = 10;

//--------------------------------------------------------------------
// NIVEAU 4 - ENTREE M5
//--------------------------------------------------------------------
input group "=== NIVEAU 4 : ENTREE M5 ==="
input int    Entry_CHoCH_LB    = 8;     // Lookback CHoCH (reduit)
input int    Entry_FVG_LB      = 30;    // Lookback FVG (augmente)
input double FVG_MinPts        = 3.0;   // Taille min FVG (reduit)
input bool   FVG_Fresh         = false; // FVG frais seulement (OFF=plus de signaux)
// IMPORTANT : FVG_Fresh=false donne beaucoup plus de signaux

//--------------------------------------------------------------------
// METHODE 1 - SL/TP EN POINTS
//--------------------------------------------------------------------
input group "=== METHODE 1 : SL/TP FIXE EN POINTS ==="
input bool   M1_Active         = true;
input int    M1_SL_Points      = 200;   // 200 pts = 2$ /0.01lot
input int    M1_TP_Points      = 600;   // 600 pts = 6$ /0.01lot

//--------------------------------------------------------------------
// METHODE 2 - SL/TP PAR ATR (si M1 = false)
//--------------------------------------------------------------------
input group "=== METHODE 2 : SL/TP PAR ATR (si M1 OFF) ==="
input bool             M2_Active      = true;
input int              M2_ATR_Period  = 14;
input ENUM_TIMEFRAMES  M2_ATR_TF      = PERIOD_M5;
input double           M2_SL_Mult    = 1.5;
input double           M2_TP_Mult    = 3.0;

//--------------------------------------------------------------------
// METHODE 3 - BREAK EVEN
//--------------------------------------------------------------------
input group "=== METHODE 3 : BREAK EVEN ==="
input bool   M3_Active           = true;
input bool   M3_UseRR            = true;
input double M3_RR_Trigger       = 1.0;
input int    M3_Points_Trigger   = 200;
input int    M3_Lock_Points      = 5;

//--------------------------------------------------------------------
// METHODE 4 - TRAILING STOP
//--------------------------------------------------------------------
input group "=== METHODE 4 : TRAILING STOP ==="
input bool   M4_Active              = true;
input bool   M4_UseRR               = true;
input double M4_RR_Activation       = 1.5;
input int    M4_Points_Activation   = 300;
input int    M4_Trail_Distance      = 150;
input int    M4_Trail_Step          = 50;

//--------------------------------------------------------------------
// RISK MANAGEMENT
//--------------------------------------------------------------------
input group "=== RISK MANAGEMENT ==="
input double RiskPercent      = 0.5;
input double MaxLot           = 5.0;
input int    MaxDailyTrades   = 2;
input int    MaxSpread        = 50;   // Augmente pour backtesting

//--------------------------------------------------------------------
// SILVER BULLET WINDOWS (GMT+2)
//--------------------------------------------------------------------
//--------------------------------------------------------------------
// SILVER BULLET WINDOWS - en heure GMT pure
// Maroc = GMT+0 donc ces heures = tes heures locales
//--------------------------------------------------------------------
input group "=== WINDOW 1 : NY 03:00 = GMT 08:00 ==="
input int    GMT_Offset   = 0;     // GMT pur (0 = Maroc, universel)
input bool   UseWindow1   = true;
input int    W1_Start     = 800;   // 08:00 GMT
input int    W1_End       = 900;   // 09:00 GMT

input group "=== WINDOW 2 : NY 10:00 = GMT 15:00 ==="
input bool   UseWindow2   = true;
input int    W2_Start     = 1500;  // 15:00 GMT
input int    W2_End       = 1600;  // 16:00 GMT

input group "=== WINDOW 3 : NY 14:00 = GMT 19:00 ==="
input bool   UseWindow3   = false;
input int    W3_Start     = 1900;  // 19:00 GMT
input int    W3_End       = 2000;  // 20:00 GMT

input group "=== FILTRE JOURS ==="
input bool   TradeLundi    = true;
input bool   TradeMardi    = true;
input bool   TradeMercredi = true;
input bool   TradeJeudi    = true;
input bool   TradeVendredi = false;
input int    VendrediStop  = 1600;

//+------------------------------------------------------------------+
//| STRUCTURES                                                       |
//+------------------------------------------------------------------+
struct FVG_Data {
   double   high, low, mid;
   bool     isBullish;
   datetime time;
   bool     valid;
};

struct TradeTrack {
   ulong  ticket;
   bool   BE_Done;
   bool   Trail_Active;
   double Trail_LastSL;
};

//+------------------------------------------------------------------+
//| VARIABLES GLOBALES                                               |
//+------------------------------------------------------------------+
FVG_Data   g_FVG;
bool       g_HasFVG       = false;
bool       g_CHoCH_Bull   = false;
bool       g_CHoCH_Bear   = false;
double     g_MacroBias    = 0;
double     g_MainBias     = 0;
bool       g_ConfirmBull  = false;
bool       g_ConfirmBear  = false;
int        g_DailyTrades  = 0;
datetime   g_LastDay      = 0;
datetime   g_LastBarTime  = 0;
int        g_ATR_Handle   = INVALID_HANDLE;
TradeTrack g_Trades[20];
int        g_TradesCount  = 0;

// Compteurs debug
int        dbg_NoWindow   = 0;
int        dbg_NoMacro    = 0;
int        dbg_NoBiais    = 0;
int        dbg_NoConfirm  = 0;
int        dbg_NoCHoCH    = 0;
int        dbg_NoFVG      = 0;
int        dbg_Entered    = 0;

//+------------------------------------------------------------------+
//| INIT                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   if(_Period != PERIOD_M5) {
      Alert("[ERR] Placer l'EA sur M5 !");
      return INIT_FAILED;
   }
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   g_ATR_Handle = iATR(_Symbol, M2_ATR_TF, M2_ATR_Period);
   if(g_ATR_Handle == INVALID_HANDLE && M2_Active && !M1_Active) {
      Print("[ERR] ATR handle invalide");
      return INIT_FAILED;
   }

   g_FVG.valid = false;
   PrintConfig();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(g_ATR_Handle != INVALID_HANDLE) IndicatorRelease(g_ATR_Handle);
   Comment("");
   PrintDebugStats();
}

//+------------------------------------------------------------------+
//| TICK                                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageOpenTrades();
   UpdateComment();

   datetime barTime = iTime(_Symbol, PERIOD_M5, 0);
   if(barTime == g_LastBarTime) return;
   g_LastBarTime = barTime;

   ResetDailyCounter();

   // ---- Filtre fenetre ----
   if(!IsSilverBulletWindow()) {
      dbg_NoWindow++;
      return;
   }
   if(g_DailyTrades >= MaxDailyTrades) return;
   if(HasOpenPosition())                return;
   if(IsSpreadTooHigh())                return;

   // ---- Niveau 1 : Macro ----
   if(UseMacroFilter) {
      g_MacroBias = GetMacroBias();
      if(g_MacroBias == 0) {
         dbg_NoMacro++;
         if(DebugMode) Print("[MACRO] Neutre - skip");
         return;
      }
   } else {
      g_MacroBias = 0; // Non utilise
   }

   // ---- Niveau 2 : Biais principal ----
   g_MainBias = GetMainBias();

   // Si biais neutre ET filtre biais actif -> skip
   // Si biais neutre ET tous filtres off -> on autorise BUY et SELL
   if(g_MainBias == 0) {
      if(UseConfirmFilter || UseCHoCH_Filter || UseFVG_Filter) {
         // Sans direction claire, on ne peut pas determiner BUY ou SELL
         // SAUF si tous les filtres directionnels sont OFF
         bool allDirFiltersOff = (!UseConfirmFilter && !UseCHoCH_Filter);
         if(!allDirFiltersOff) {
            dbg_NoBiais++;
            if(DebugMode) Print("[BIAIS] Neutre - skip");
            return;
         }
         // Si tous filtres off : forcer un scan dans les 2 sens
         g_MainBias = 1; // sera teste en BUY ET SELL dans CheckEntry
      }
   }

   // Conflit macro/biais
   if(UseMacroFilter && g_MacroBias != 0 &&
      ((g_MacroBias > 0 && g_MainBias < 0) ||
       (g_MacroBias < 0 && g_MainBias > 0))) {
      dbg_NoMacro++;
      if(DebugMode) Print("[MACRO] Conflit macro/biais - skip");
      return;
   }

   // ---- Niveau 3 : Confirmation ----
   g_ConfirmBull = false;
   g_ConfirmBear = false;
   if(UseConfirmFilter) {
      g_ConfirmBull = GetConfirmBias(true);
      g_ConfirmBear = GetConfirmBias(false);
      bool confirmOK = (g_MainBias > 0 && g_ConfirmBull) ||
                       (g_MainBias < 0 && g_ConfirmBear);
      if(!confirmOK) {
         dbg_NoConfirm++;
         if(DebugMode) Print("[CONFIRM] Pas aligne - skip");
         return;
      }
   } else {
      // Filtre off : autoriser les 2 directions
      g_ConfirmBull = true;
      g_ConfirmBear = true;
   }

   // ---- Niveau 4a : CHoCH M5 ----
   if(UseCHoCH_Filter) {
      g_CHoCH_Bull = DetectCHoCH_M5(true);
      g_CHoCH_Bear = DetectCHoCH_M5(false);
      bool chochOK = (g_MainBias > 0 && g_CHoCH_Bull) ||
                     (g_MainBias < 0 && g_CHoCH_Bear);
      if(!chochOK) {
         dbg_NoCHoCH++;
         if(DebugMode) Print("[CHOCH] Pas confirme - skip");
         return;
      }
   } else {
      // Filtre off : autoriser les 2 directions
      g_CHoCH_Bull = true;
      g_CHoCH_Bear = true;
   }

   // ---- Niveau 4b : FVG + Entree ----
   // CORRECTION MAJEURE : on stocke le FVG et on place un ordre limite
   // au lieu d'attendre que le prix soit DANS le FVG au moment du check
   ScanAndStoreFVG();
   CheckEntry();
}

//+------------------------------------------------------------------+
//| SCAN ET STOCKAGE FVG                                            |
//| Correction : stocker le meilleur FVG dans la direction du biais |
//| et placer un ordre limite dedans                                |
//+------------------------------------------------------------------+
void ScanAndStoreFVG()
{
   g_HasFVG      = false;
   g_FVG.valid   = false;

   // Si biais neutre (tous filtres off) -> scanner les 2 sens
   // On prend le FVG le plus recent quelle que soit la direction
   bool lookBull = (g_MainBias >= 0); // inclut 0 = les 2
   bool lookBear = (g_MainBias <= 0); // inclut 0 = les 2

   for(int i = 1; i <= Entry_FVG_LB; i++)
   {
      double h1 = iHigh(_Symbol, PERIOD_M5, i + 2);
      double l1 = iLow (_Symbol, PERIOD_M5, i + 2);
      double h3 = iHigh(_Symbol, PERIOD_M5, i);
      double l3 = iLow (_Symbol, PERIOD_M5, i);
      double c2 = iClose(_Symbol, PERIOD_M5, i + 1); // bougie milieu

      if(lookBull)
      {
         // Bullish FVG
         if(l3 > h1 && (l3 - h1) / _Point >= FVG_MinPts)
         {
            if(FVG_Fresh) {
               bool filled = false;
               for(int j = i - 1; j >= 1; j--)
                  if(iLow(_Symbol, PERIOD_M5, j) <= h1 + _Point) {
                     filled = true; break;
                  }
               if(filled) continue;
            }
            // FVG valide - prix au-dessus ou dans la zone
            double curLow = iLow(_Symbol, PERIOD_M5, 0);
            if(curLow <= l3) { // prix a touche ou sous le haut du FVG
               g_FVG.isBullish = true;
               g_FVG.low       = h1;
               g_FVG.high      = l3;
               g_FVG.mid       = (h1 + l3) / 2.0;
               g_FVG.time      = iTime(_Symbol, PERIOD_M5, i + 1);
               g_FVG.valid     = true;
               g_HasFVG        = true;
               break;
            }
         }
      }
      else if(lookBear)
      {
         // Bearish FVG
         if(h3 < l1 && (l1 - h3) / _Point >= FVG_MinPts)
         {
            if(FVG_Fresh) {
               bool filled = false;
               for(int j = i - 1; j >= 1; j--)
                  if(iHigh(_Symbol, PERIOD_M5, j) >= l1 - _Point) {
                     filled = true; break;
                  }
               if(filled) continue;
            }
            double curHigh = iHigh(_Symbol, PERIOD_M5, 0);
            if(curHigh >= h3) { // prix a touche ou au-dessus du bas du FVG
               g_FVG.isBullish = false;
               g_FVG.low       = h3;
               g_FVG.high      = l1;
               g_FVG.mid       = (h3 + l1) / 2.0;
               g_FVG.time      = iTime(_Symbol, PERIOD_M5, i + 1);
               g_FVG.valid     = true;
               g_HasFVG        = true;
               break;
            }
         }
      }
   }

   if(!g_HasFVG) {
      dbg_NoFVG++;
      if(DebugMode) Print("[FVG] Aucun FVG trouve dans direction biais");
   }
}

//+------------------------------------------------------------------+
//| VERIFIER ET ENTRER                                              |
//| CORRECTION : entrer au close de la bougie si prix dans zone FVG |
//+------------------------------------------------------------------+
void CheckEntry()
{
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double close0 = iClose(_Symbol, PERIOD_M5, 1); // close derniere bougie
   double sl, tp, slDist;

   bool isBullSignal = g_ConfirmBull && g_CHoCH_Bull &&
                       (g_MainBias >= 0); // inclut neutre quand filtres off
   bool isBearSignal = g_ConfirmBear && g_CHoCH_Bear &&
                       (g_MainBias <= 0); // inclut neutre quand filtres off

   // ==== BUY ====
   if(isBullSignal)
   {
      bool enterOK = false;

      if(UseFVG_Filter) {
         // Entrer si le prix est dans le FVG bullish OU au-dessous
         if(g_HasFVG && g_FVG.isBullish) {
            // Prix actuel dans ou proche du FVG
            if(ask <= g_FVG.high + 10 * _Point &&
               ask >= g_FVG.low  - 20 * _Point)
               enterOK = true;
         }
      } else {
         // Sans filtre FVG : entrer directement au marche
         enterOK = true;
      }

      if(enterOK) {
         if(!GetSLTP(true, ask, sl, tp, slDist)) return;
         double lot = CalcLotSize(slDist);
         if(lot <= 0) return;

         if(DebugMode) LogEntry("BUY", ask, sl, tp, lot, slDist);
         if(trade.Buy(lot, _Symbol, ask, sl, tp, "SB_ICT_BUY_v4")) {
            g_DailyTrades++;
            RegisterTrade(trade.ResultOrder());
            dbg_Entered++;
            Print(StringFormat("[TRADE] BUY ouvert #%d @ %.5f", trade.ResultOrder(), ask));
         } else {
            Print("[ERR] BUY echoue : ", trade.ResultRetcodeDescription());
         }
      }
   }

   // ==== SELL ====
   if(isBearSignal)
   {
      bool enterOK = false;

      if(UseFVG_Filter) {
         if(g_HasFVG && !g_FVG.isBullish) {
            if(bid >= g_FVG.low  - 10 * _Point &&
               bid <= g_FVG.high + 20 * _Point)
               enterOK = true;
         }
      } else {
         enterOK = true;
      }

      if(enterOK) {
         if(!GetSLTP(false, bid, sl, tp, slDist)) return;
         double lot = CalcLotSize(slDist);
         if(lot <= 0) return;

         if(DebugMode) LogEntry("SELL", bid, sl, tp, lot, slDist);
         if(trade.Sell(lot, _Symbol, bid, sl, tp, "SB_ICT_SELL_v4")) {
            g_DailyTrades++;
            RegisterTrade(trade.ResultOrder());
            dbg_Entered++;
            Print(StringFormat("[TRADE] SELL ouvert #%d @ %.5f", trade.ResultOrder(), bid));
         } else {
            Print("[ERR] SELL echoue : ", trade.ResultRetcodeDescription());
         }
      }
   }
}

//+------------------------------------------------------------------+
//| BIAIS MACRO                                                     |
//+------------------------------------------------------------------+
double GetMacroBias()
{
   if(UseBiasEMA) {
      double ema1 = GetEMA(MacroTF, Macro_EMA1_Period);
      if(ema1 == 0) return 0;
      if(Macro_EMA2_Period > 0) {
         double ema2 = GetEMA(MacroTF, Macro_EMA2_Period);
         if(ema2 == 0) return 0;
         if(ema1 > ema2) return  1.0;
         if(ema1 < ema2) return -1.0;
      } else {
         double c = iClose(_Symbol, MacroTF, 1);
         if(c > ema1) return  1.0;
         if(c < ema1) return -1.0;
      }
   } else {
      return GetICTStructure(MacroTF, Macro_Struct_LB);
   }
   return 0;
}

//+------------------------------------------------------------------+
//| BIAIS PRINCIPAL                                                 |
//+------------------------------------------------------------------+
double GetMainBias()
{
   if(UseBiasEMA) {
      double ema1 = GetEMA(BiaisTF, Biais_EMA1_Period);
      double ema2 = GetEMA(BiaisTF, Biais_EMA2_Period);
      if(ema1 == 0 || ema2 == 0) return 0;

      bool bull = (ema1 > ema2);
      bool bear = (ema1 < ema2);

      if(Biais_RequireBOS) {
         if(bull && DetectBOS_HTF(true,  BiaisTF)) return  1.0;
         if(bear && DetectBOS_HTF(false, BiaisTF)) return -1.0;
         if(bull) return  0.5;
         if(bear) return -0.5;
      } else {
         if(bull) return  1.0;
         if(bear) return -1.0;
      }
   } else {
      double str = GetICTStructure(BiaisTF, Biais_Struct_LB);
      if(str > 0)  return  1.0;
      if(str < 0)  return -1.0;
   }
   return 0;
}

//+------------------------------------------------------------------+
//| CONFIRMATION (M15/M10)                                          |
//+------------------------------------------------------------------+
bool GetConfirmBias(bool bullish)
{
   if(UseBiasEMA) {
      double ema1 = GetEMA(ConfirmTF, Confirm_EMA1_Period);
      double ema2 = GetEMA(ConfirmTF, Confirm_EMA2_Period);
      if(ema1 == 0 || ema2 == 0) return false;
      return bullish ? (ema1 > ema2) : (ema1 < ema2);
   } else {
      // Structure ICT sur TF de confirmation
      int lb = Confirm_Struct_LB;
      if(bullish) {
         double prevH = iHigh(_Symbol, ConfirmTF, 2);
         return (iClose(_Symbol, ConfirmTF, 1) > prevH);
      } else {
         double prevL = iLow(_Symbol, ConfirmTF, 2);
         return (iClose(_Symbol, ConfirmTF, 1) < prevL);
      }
   }
}

//+------------------------------------------------------------------+
//| CHOCH M5                                                        |
//+------------------------------------------------------------------+
bool DetectCHoCH_M5(bool bullish)
{
   int lb = Entry_CHoCH_LB;
   if(bullish) {
      // BOS simple : cloture au-dessus du high precedent
      double prevH = iHigh(_Symbol, PERIOD_M5, 2);
      double prevL = iLow (_Symbol, PERIOD_M5, 2);
      double c1    = iClose(_Symbol, PERIOD_M5, 1);
      double l1    = iLow  (_Symbol, PERIOD_M5, 1);
      // CHoCH : apres un LL, cassure d'un high
      int lowestBar = iLowest(_Symbol, PERIOD_M5, MODE_LOW, lb, 2);
      double llPrice = iLow(_Symbol, PERIOD_M5, lowestBar);
      if(c1 > prevH && l1 <= llPrice * 1.002) return true;
      // BOS simple sans CHoCH strict
      if(c1 > iHigh(_Symbol, PERIOD_M5, 3)) return true;
   } else {
      double prevL = iLow (_Symbol, PERIOD_M5, 2);
      double c1    = iClose(_Symbol, PERIOD_M5, 1);
      double h1    = iHigh (_Symbol, PERIOD_M5, 1);
      int highestBar = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, lb, 2);
      double hhPrice = iHigh(_Symbol, PERIOD_M5, highestBar);
      if(c1 < prevL && h1 >= hhPrice * 0.998) return true;
      if(c1 < iLow(_Symbol, PERIOD_M5, 3)) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| OUTILS BIAIS                                                    |
//+------------------------------------------------------------------+
double GetEMA(ENUM_TIMEFRAMES tf, int period, int shift = 1)
{
   int h = iMA(_Symbol, tf, period, 0, MODE_EMA, PRICE_CLOSE);
   if(h == INVALID_HANDLE) return 0;
   double buf[]; ArraySetAsSeries(buf, true);
   bool ok = (CopyBuffer(h, 0, shift, 1, buf) >= 1);
   IndicatorRelease(h);
   return ok ? buf[0] : 0;
}

double GetICTStructure(ENUM_TIMEFRAMES tf, int lb)
{
   int hiBar1 = iHighest(_Symbol, tf, MODE_HIGH, lb,      2);
   int hiBar2 = iHighest(_Symbol, tf, MODE_HIGH, lb, lb/2+2);
   int loBar1 = iLowest (_Symbol, tf, MODE_LOW,  lb,      2);
   int loBar2 = iLowest (_Symbol, tf, MODE_LOW,  lb, lb/2+2);
   if(hiBar1<0||hiBar2<0||loBar1<0||loBar2<0) return 0;

   double h1=iHigh(_Symbol,tf,hiBar1), h2=iHigh(_Symbol,tf,hiBar2);
   double l1=iLow (_Symbol,tf,loBar1), l2=iLow (_Symbol,tf,loBar2);

   bool HH=(h1>h2), HL=(l1>l2), LH=(h1<h2), LL=(l1<l2);
   if(HH && HL) return  1.0;
   if(LH && LL) return -1.0;
   if(HL)       return  0.5;
   if(LH)       return -0.5;
   return 0;
}

bool DetectBOS_HTF(bool bull, ENUM_TIMEFRAMES tf)
{
   int lb  = 10;
   if(bull) {
      int bar = iHighest(_Symbol, tf, MODE_HIGH, lb, 2);
      return (iClose(_Symbol, tf, 1) > iHigh(_Symbol, tf, bar));
   } else {
      int bar = iLowest(_Symbol, tf, MODE_LOW, lb, 2);
      return (iClose(_Symbol, tf, 1) < iLow(_Symbol, tf, bar));
   }
}

//+------------------------------------------------------------------+
//| CALCUL SL/TP                                                    |
//+------------------------------------------------------------------+
bool GetSLTP(bool isBuy, double entry, double &sl, double &tp, double &slDist)
{
   double sd=0, td=0;
   if(M1_Active) {
      sd = M1_SL_Points * _Point;
      td = M1_TP_Points * _Point;
   } else if(M2_Active) {
      double atr[]; ArraySetAsSeries(atr, true);
      if(CopyBuffer(g_ATR_Handle, 0, 1, 1, atr) < 1) return false;
      sd = atr[0] * M2_SL_Mult;
      td = atr[0] * M2_TP_Mult;
   } else return false;

   if(isBuy)  { sl=entry-sd; tp=entry+td; }
   else       { sl=entry+sd; tp=entry-td; }
   slDist = sd;
   return true;
}

//+------------------------------------------------------------------+
//| CALCUL LOT                                                      |
//+------------------------------------------------------------------+
double CalcLotSize(double slDist)
{
   if(slDist <= 0) return 0;
   double riskAmt = AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0;
   double tickVal = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSz  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal==0||tickSz==0) return 0;
   double ptVal   = tickVal * (_Point / tickSz);
   double lot     = riskAmt / ((slDist / _Point) * ptVal);
   double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = MathMin(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), MaxLot);
   lot = MathFloor(lot / step) * step;
   return MathMax(minLot, MathMin(maxLot, lot));
}

//+------------------------------------------------------------------+
//| GESTION TRADES OUVERTS                                          |
//+------------------------------------------------------------------+
void ManageOpenTrades()
{
   for(int i = 0; i < PositionsTotal(); i++) {
      if(!posInfo.SelectByIndex(i))       continue;
      if(posInfo.Magic() != MagicNumber)  continue;
      if(posInfo.Symbol() != _Symbol)     continue;

      ulong  ticket    = posInfo.Ticket();
      double openPrice = posInfo.PriceOpen();
      double curSL     = posInfo.StopLoss();
      double curTP     = posInfo.TakeProfit();
      double bid       = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask       = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      ENUM_POSITION_TYPE type = posInfo.PositionType();
      double slDist    = MathAbs(openPrice - curSL);
      if(slDist < _Point) continue;

      double curPrice  = (type==POSITION_TYPE_BUY) ? bid : ask;
      double profitPts = (type==POSITION_TYPE_BUY)
                         ? (curPrice-openPrice)/_Point
                         : (openPrice-curPrice)/_Point;
      double slPts     = slDist / _Point;
      int    trIdx     = GetTradeTrackIndex(ticket);
      if(trIdx < 0) continue;

      // --- BREAK EVEN ---
      if(M3_Active && !g_Trades[trIdx].BE_Done) {
         bool trig = M3_UseRR
                     ? (profitPts >= slPts * M3_RR_Trigger)
                     : (profitPts >= M3_Points_Trigger);
         if(trig) {
            double newSL = (type==POSITION_TYPE_BUY)
                           ? openPrice + M3_Lock_Points * _Point
                           : openPrice - M3_Lock_Points * _Point;
            bool ok = (type==POSITION_TYPE_BUY  && newSL > curSL+_Point)
                   || (type==POSITION_TYPE_SELL && newSL < curSL-_Point);
            if(ok && trade.PositionModify(ticket, newSL, curTP)) {
               g_Trades[trIdx].BE_Done = true;
               Print(StringFormat("[BE] #%d > %.5f", ticket, newSL));
            }
         }
      }

      // --- TRAILING ---
      if(M4_Active) {
         if(!g_Trades[trIdx].Trail_Active) {
            bool act = M4_UseRR
                       ? (profitPts >= slPts * M4_RR_Activation)
                       : (profitPts >= M4_Points_Activation);
            if(act) { g_Trades[trIdx].Trail_Active = true; }
         }
         if(g_Trades[trIdx].Trail_Active) {
            double newSL=0; bool upd=false;
            if(type==POSITION_TYPE_BUY) {
               newSL = bid - M4_Trail_Distance * _Point;
               upd   = (newSL > curSL + M4_Trail_Step*_Point && newSL > openPrice);
            } else {
               newSL = ask + M4_Trail_Distance * _Point;
               upd   = (newSL < curSL - M4_Trail_Step*_Point && newSL < openPrice);
            }
            if(upd && trade.PositionModify(ticket, newSL, curTP))
               Print(StringFormat("[TRAIL] #%d > %.5f", ticket, newSL));
         }
      }
   }
}

//+------------------------------------------------------------------+
//| TRACKING TRADES                                                 |
//+------------------------------------------------------------------+
void RegisterTrade(ulong ticket)
{
   if(g_TradesCount >= 20) return;
   g_Trades[g_TradesCount].ticket       = ticket;
   g_Trades[g_TradesCount].BE_Done      = false;
   g_Trades[g_TradesCount].Trail_Active = false;
   g_Trades[g_TradesCount].Trail_LastSL = 0;
   g_TradesCount++;
}

int GetTradeTrackIndex(ulong ticket)
{
   for(int i=0; i<g_TradesCount; i++)
      if(g_Trades[i].ticket == ticket) return i;
   RegisterTrade(ticket);
   for(int i=0; i<g_TradesCount; i++)
      if(g_Trades[i].ticket == ticket) return i;
   return -1;
}

void CleanClosedTrades()
{
   for(int i=g_TradesCount-1; i>=0; i--)
      if(!PositionSelectByTicket(g_Trades[i].ticket)) {
         for(int j=i; j<g_TradesCount-1; j++) g_Trades[j]=g_Trades[j+1];
         g_TradesCount--;
      }
}

//+------------------------------------------------------------------+
//| FENETRES ET JOURS                                               |
//+------------------------------------------------------------------+
bool IsTradingDayAllowed()
{
   // Utiliser TimeGMT pour etre independant du serveur broker
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   int t = dt.hour * 100 + dt.min;
   switch(dt.day_of_week) {
      case 1: return TradeLundi;
      case 2: return TradeMardi;
      case 3: return TradeMercredi;
      case 4: return TradeJeudi;
      case 5: return (TradeVendredi && t < VendrediStop);
      default: return false;
   }
}

bool IsSilverBulletWindow()
{
   if(!IsTradingDayAllowed()) return false;
   MqlDateTime dt;
   // TimeGMT() + offset = heure de reference pure
   // GMT_Offset = 0 pour Maroc/GMT universel
   // GMT_Offset = 2 si tu veux utiliser heure serveur Exness
   TimeToStruct(TimeGMT() + GMT_Offset * 3600, dt);
   int t = dt.hour * 100 + dt.min;
   bool w1 = UseWindow1 && (t >= W1_Start && t < W1_End);
   bool w2 = UseWindow2 && (t >= W2_Start && t < W2_End);
   bool w3 = UseWindow3 && (t >= W3_Start && t < W3_End);
   return (w1 || w2 || w3);
}

string GetWindowName()
{
   MqlDateTime dt;
   TimeToStruct(TimeGMT() + GMT_Offset * 3600, dt);
   int t = dt.hour * 100 + dt.min;
   if(UseWindow1 && t>=W1_Start && t<W1_End)
      return StringFormat("W1 GMT %02d:%02d", W1_Start/100, W1_Start%100);
   if(UseWindow2 && t>=W2_Start && t<W2_End)
      return StringFormat("W2 GMT %02d:%02d", W2_Start/100, W2_Start%100);
   if(UseWindow3 && t>=W3_Start && t<W3_End)
      return StringFormat("W3 GMT %02d:%02d", W3_Start/100, W3_Start%100);
   return "Hors fenetre";
}

string GetDayName()
{
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   string d[]={"Dim","Lun","Mar","Mer","Jeu","Ven","Sam"};
   return d[dt.day_of_week];
}

//+------------------------------------------------------------------+
//| UTILITAIRES                                                     |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i=0; i<PositionsTotal(); i++)
      if(posInfo.SelectByIndex(i))
         if(posInfo.Magic()==MagicNumber && posInfo.Symbol()==_Symbol)
            return true;
   return false;
}

bool IsSpreadTooHigh()
{ return (SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > MaxSpread); }

void ResetDailyCounter()
{
   // Utiliser TimeGMT pour le reset journalier
   MqlDateTime dt; TimeToStruct(TimeGMT(), dt);
   datetime today = StringToTime(
      StringFormat("%04d.%02d.%02d 00:00", dt.year, dt.mon, dt.day));
   if(g_LastDay != today) {
      g_DailyTrades = 0; g_LastDay = today; CleanClosedTrades();
   }
}

string TFtoStr(ENUM_TIMEFRAMES tf)
{
   switch(tf) {
      case PERIOD_M1:  return "M1";  case PERIOD_M5:  return "M5";
      case PERIOD_M10: return "M10"; case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30"; case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";  case PERIOD_H8:  return "H8";
      case PERIOD_D1:  return "D1";  default: return "?";
   }
}

//+------------------------------------------------------------------+
//| LOGS ET DASHBOARD                                               |
//+------------------------------------------------------------------+
void LogEntry(string dir, double price, double sl, double tp,
              double lot, double slDist)
{
   Print(StringFormat("[SIGNAL] %s | Prix:%.5f SL:%.5f TP:%.5f Lots:%.2f SL=%.0fpts=%.2f$/0.01lot",
         dir, price, sl, tp, lot, slDist/_Point, slDist/_Point*0.01));
}

void PrintDebugStats()
{
   Print("=== STATS BACKTEST Silver Bullet ICT v4.0 ===");
   Print(StringFormat("Bougies hors fenetre : %d", dbg_NoWindow));
   Print(StringFormat("Macro bloque         : %d", dbg_NoMacro));
   Print(StringFormat("Biais bloque         : %d", dbg_NoBiais));
   Print(StringFormat("Confirm bloque       : %d", dbg_NoConfirm));
   Print(StringFormat("CHoCH bloque         : %d", dbg_NoCHoCH));
   Print(StringFormat("FVG non trouve       : %d", dbg_NoFVG));
   Print(StringFormat("TRADES ENTRES        : %d", dbg_Entered));
   Print("=============================================");
}

void PrintConfig()
{
   Print("=== Silver Bullet ICT EA v4.2 ===");
   Print("=== Heure GMT pure (Maroc GMT+0) ===");
   Print(StringFormat("GMT_Offset : %d", GMT_Offset));
   Print(StringFormat("W1 : %s  GMT %04d-%04d", UseWindow1?"ON":"OFF", W1_Start, W1_End));
   Print(StringFormat("W2 : %s  GMT %04d-%04d", UseWindow2?"ON":"OFF", W2_Start, W2_End));
   Print(StringFormat("W3 : %s  GMT %04d-%04d", UseWindow3?"ON":"OFF", W3_Start, W3_End));
   Print(StringFormat("Methode : %s", UseBiasEMA ? "EMA" : "Structure ICT"));
   Print(StringFormat("Macro   : %s (%s)", UseMacroFilter ? "ON" : "OFF", TFtoStr(MacroTF)));
   Print(StringFormat("Biais   : %s EMA%d/EMA%d BOS:%s", TFtoStr(BiaisTF),
         Biais_EMA1_Period, Biais_EMA2_Period, Biais_RequireBOS?"ON":"OFF"));
   Print(StringFormat("Confirm : %s (%s)", UseConfirmFilter ? "ON" : "OFF", TFtoStr(ConfirmTF)));
   Print(StringFormat("CHoCH   : %s", UseCHoCH_Filter ? "ON" : "OFF"));
   Print(StringFormat("FVG     : %s Fresh:%s MinPts:%.0f", UseFVG_Filter?"ON":"OFF",
         FVG_Fresh?"ON":"OFF", FVG_MinPts));
   Print(StringFormat("M1 SL/TP Points : %s SL=%d TP=%d", M1_Active?"ON":"OFF",
         M1_SL_Points, M1_TP_Points));
   Print(StringFormat("Risque  : %.1f%% MaxTrades:%d", RiskPercent, MaxDailyTrades));
}

void UpdateComment()
{
   MqlDateTime dtGMT; TimeToStruct(TimeGMT(), dtGMT);
   string gmtTime = StringFormat("GMT %02d:%02d", dtGMT.hour, dtGMT.min);
   string macro  = !UseMacroFilter ? "OFF"
                 : g_MacroBias > 0 ? "BULL" : g_MacroBias < 0 ? "BEAR" : "NEUT";
   string biais  = g_MainBias  > 0 ? "BULL" : g_MainBias  < 0 ? "BEAR" : "NEUT";
   string conf   = g_ConfirmBull ? "BUY" : g_ConfirmBear ? "SELL" : "NEUT";
   string choch  = g_CHoCH_Bull  ? "BUY" : g_CHoCH_Bear  ? "SELL" : "NEUT";
   string fvg    = g_HasFVG ? StringFormat("%.2f-%.2f %s",
                   g_FVG.low, g_FVG.high, g_FVG.isBullish?"BUY":"SELL") : "NONE";
   string win    = IsSilverBulletWindow() ? GetWindowName()
                 : GetDayName() + " hors fenetre";

   Comment(StringFormat(
      "=== SILVER BULLET ICT v4.2 ===\n"
      "Heure GMT : %s\n"
      "Methode   : %s\n"
      "Fenetre   : %s\n"
      "-----------------------------\n"
      "L1 Macro  (%s): %s\n"
      "L2 Biais  (%s): %s\n"
      "L3 Confirm(%s): %s\n"
      "L4 CHoCH   M5 : %s\n"
      "L4 FVG     M5 : %s\n"
      "-----------------------------\n"
      "M1 SL/TP : %s\n"
      "M3 BE    : %s\n"
      "M4 Trail : %s\n"
      "-----------------------------\n"
      "Trades/j : %d/%d\n"
      "Spread   : %d pts\n"
      "Balance  : %.2f USD\n"
      "-----------------------------\n"
      "DEBUG (filtres bloques)\n"
      "Fenetre: %d | Macro: %d\n"
      "Biais: %d | Confirm: %d\n"
      "CHoCH: %d | FVG: %d\n"
      "TRADES: %d\n"
      "=============================",
      gmtTime,
      UseBiasEMA ? "EMA" : "STRUCTURE",
      win,
      TFtoStr(MacroTF), macro,
      TFtoStr(BiaisTF), biais,
      TFtoStr(ConfirmTF), conf,
      choch, fvg,
      M1_Active ? StringFormat("ON SL:%d TP:%d",M1_SL_Points,M1_TP_Points):"OFF",
      M3_Active ? (M3_UseRR ? StringFormat("ON RR%.1f",M3_RR_Trigger)
                            : StringFormat("ON %dpts",M3_Points_Trigger)):"OFF",
      M4_Active ? (M4_UseRR ? StringFormat("ON RR%.1f",M4_RR_Activation)
                            : StringFormat("ON %dpts",M4_Points_Activation)):"OFF",
      g_DailyTrades, MaxDailyTrades,
      (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD),
      AccountInfoDouble(ACCOUNT_BALANCE),
      dbg_NoWindow, dbg_NoMacro,
      dbg_NoBiais, dbg_NoConfirm,
      dbg_NoCHoCH, dbg_NoFVG,
      dbg_Entered
   ));
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   if(!HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != MagicNumber) return;
   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)
      HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY) {
      double p = HistoryDealGetDouble(trans.deal, DEAL_PROFIT);
      Print(p>=0 ? StringFormat("[WIN] +%.2f USD",p) : StringFormat("[LOSS] %.2f USD",p));
      CleanClosedTrades();
   }
}
//+------------------------------------------------------------------+
