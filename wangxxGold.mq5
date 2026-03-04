//+------------------------------------------------------------------+
//|                                                     wwxxgold.mq5 |
//|                                                     wwananggxxxx |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "wwananggxxxx"
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

//--- 需要访问Web请求权限
#include <Trade\Trade.mqh>
#include <Trade\SymbolInfo.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

//+------------------------------------------------------------------+
//| 全局变量定义                                                     |
//+------------------------------------------------------------------+

// Python 服务配置
string g_pythonServer = "http://localhost:5858";
uint g_lastPythonRequestTime = 0;
int g_pythonRequestInterval = 100;  // 毫秒

// 统计数据 - 每分钟重置
datetime g_lastStatisticTime = 0;
int g_tickCountPerMinute = 0;
double g_bidPrice = 0;
double g_askPrice = 0;
double g_accountBalance = 0;
double g_accountEquity = 0;
double g_marginLevel = 0;
string g_positionsSummary = "";  // JSON 格式的持仓汇总

// 当日交易记录 - 用于发送到Python
string g_tradesOfDay = "";

// 交易类对象
CTrade trade;
CSymbolInfo symbolInfo;
CPositionInfo positionInfo;

// 风险管理相关
double g_riskLimitPercent = 30.0;  // 30% 账户风险限制

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
//--- 初始化交易类
   trade.SetExpertMagicNumber(123456);
   
//--- 初始化时间
   g_lastStatisticTime = TimeCurrent();
   g_lastPythonRequestTime = GetTickCount();
   
//--- 打印初始化信息
   Print("Expert initialized successfully");
   Print("Python server: ", g_pythonServer);
   Print("Risk limit: ", g_riskLimitPercent, "%");
   
//---
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
//---
   Print("Expert deinitialized, reason: ", reason);
  }
//+------------------------------------------------------------------+
//| 更新统计数据 - 每个TICK调用                                      |
//+------------------------------------------------------------------+
void UpdateStatistics()
  {
   g_tickCountPerMinute++;
   
//--- 获取当前价格
   MqlTick lastTick;
   if(SymbolInfoTick(_Symbol, lastTick))
     {
      g_bidPrice = lastTick.bid;
      g_askPrice = lastTick.ask;
     }
   
//--- 获取账户信息
   g_accountBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_accountEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_marginLevel = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
  }

//+------------------------------------------------------------------+
//| 获取持仓汇总信息 - 返回JSON格式字符串                             |
//+------------------------------------------------------------------+
string GetPositionsSummary()
  {
   string summary = "[";
   int positionCount = 0;
   
   for(int i = 0; i < PositionsTotal(); i++)
     {
      if(!PositionGetTicket(i)) continue;
      
      long posTicket = PositionGetInteger(POSITION_TICKET);
      string posSymbol = PositionGetString(POSITION_SYMBOL);
      if(posSymbol != _Symbol) continue;  // 只统计当前品种
      
      double posVolume = PositionGetDouble(POSITION_VOLUME);
      double posPriceOpen = PositionGetDouble(POSITION_PRICE_OPEN);
      double posProfit = PositionGetDouble(POSITION_PROFIT);
      double posSL = PositionGetDouble(POSITION_SL);
      double posTP = PositionGetDouble(POSITION_TP);
      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      
      double currentPrice = (posType == POSITION_TYPE_BUY) ? g_bidPrice : g_askPrice;
      double distanceSL = (posSL > 0) ? MathAbs(currentPrice - posSL) : 0;
      double distanceTP = (posTP > 0) ? MathAbs(posTP - currentPrice) : 0;
      
      if(positionCount > 0) summary += ",";
      summary += "{";
      summary += "\"ticket\":" + IntegerToString(posTicket) + ",";
      summary += "\"volume\":" + DoubleToString(posVolume, 2) + ",";
      summary += "\"priceOpen\":" + DoubleToString(posPriceOpen, _Digits) + ",";
      summary += "\"type\":\"" + (posType == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
      summary += "\"profit\":" + DoubleToString(posProfit, 2) + ",";
      summary += "\"distanceSL\":" + DoubleToString(distanceSL, _Digits) + ",";
      summary += "\"distanceTP\":" + DoubleToString(distanceTP, _Digits) + "";
      summary += "}";
      
      positionCount++;
     }
   
   summary += "]";
   return summary;
  }

//+------------------------------------------------------------------+
//| 检查并平仓风险持仓                                                |
//+------------------------------------------------------------------+
void CheckAndCloseRiskyPositions()
  {
   double riskThreshold = g_accountBalance * (g_riskLimitPercent / 100.0);
   
   for(int i = 0; i < PositionsTotal(); i++)
     {
      if(!PositionGetTicket(i)) continue;
      
      string posSymbol = PositionGetString(POSITION_SYMBOL);
      if(posSymbol != _Symbol) continue;
      
      double posProfit = PositionGetDouble(POSITION_PROFIT);
      
      // 如果损失超过阈值，平仓
      if(posProfit < -riskThreshold)
        {
         long posTicket = PositionGetTicket(i);
         Print("Risk limit exceeded! Position profit: ", posProfit, " Limit: ", -riskThreshold);
         
         if(trade.PositionClose(posTicket))
           {
            Print("Position closed successfully: ", posTicket);
            // 记录平仓动作
            RecordTrade("CLOSE", _Symbol, PositionGetDouble(POSITION_VOLUME), 0, 0, 0);
           }
         else
           {
            Print("Failed to close position: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| 请求Python服务获取交易指令                                       |
//+------------------------------------------------------------------+
void RequestTradesFromPython()
  {
   string headers = "Content-Type: application/json\r\n";
   char responseData[];
   string response = "";
   int responseCode = 0;
   
   // 构建请求URL，携带SYMBOL和当前价格
   string currentPrice = DoubleToString((g_bidPrice + g_askPrice) / 2, _Digits);
   string url = g_pythonServer + "/get_trades?symbol=" + _Symbol + "&price=" + currentPrice;
   
   // 建立HTTP请求到Python服务
   responseCode = WebRequest("GET", url, headers, NULL, responseData);
   
   if(responseCode == 200)
     {
      // 将响应转换为字符串
      if(ArraySize(responseData) > 0)
        {
         for(int i = 0; i < ArraySize(responseData); i++)
           {
            response += CharToString(responseData[i]);
           }
         
         // 解析JSON并执行交易
         ParseAndExecuteTrades(response);
        }
     }
   else if(responseCode != -1)  // -1表示请求被禁用
     {
      Print("WebRequest failed. Response code: ", responseCode);
     }
  }

//+------------------------------------------------------------------+
//| 解析JSON格式的交易指令并执行                                      |
//+------------------------------------------------------------------+
void ParseAndExecuteTrades(string jsonData)
  {
   // JSON格式: [{"symbol":"gold","action":"b","mount":0.01,"sl":5000,"tp":5100}, ...]
   // 这里需要简单的JSON解析
   
   if(StringLen(jsonData) == 0) return;
   
   // 移除首尾的括号
   jsonData = StringSubstr(jsonData, 1, StringLen(jsonData) - 2);
   
   // 简单的JSON解析
   int tradeCount = 0;
   int pos = -1;
   
   while(true)
     {
      int startPos = StringFind(jsonData, "{", pos + 1);
      int endPos = StringFind(jsonData, "}", startPos);
      
      if(startPos == -1 || endPos == -1) break;
      
      string tradeStr = StringSubstr(jsonData, startPos + 1, endPos - startPos - 1);
      ExecuteTradeFromJson(tradeStr);
      
      pos = endPos;
      tradeCount++;
      
      if(tradeCount > 100) break;  // 防止无限循环
     }
  }

//+------------------------------------------------------------------+
//| 从JSON字符串执行单个交易                                          |
//+------------------------------------------------------------------+
void ExecuteTradeFromJson(string tradeJson)
  {
   string symbol = ExtractJsonString(tradeJson, "symbol");
   string action = ExtractJsonString(tradeJson, "action");
   double volume = ExtractJsonDouble(tradeJson, "mount");
   double sl = ExtractJsonDouble(tradeJson, "sl");
   double tp = ExtractJsonDouble(tradeJson, "tp");
   
   if(symbol == "" || action == "" || volume <= 0) return;
   if(symbol != _Symbol) return;  // 只处理当前品种
   
   ENUM_ORDER_TYPE orderType = (action == "b") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   
   ExecuteTrade(orderType, volume, sl, tp);
  }

//+------------------------------------------------------------------+
//| 从JSON字符串中提取字符串值                                        |
//+------------------------------------------------------------------+
string ExtractJsonString(string json, string key)
  {
   string searchKey = "\"" + key + "\":\"";
   int startPos = StringFind(json, searchKey);
   
   if(startPos == -1) return "";
   
   startPos += StringLen(searchKey);
   int endPos = StringFind(json, "\"", startPos);
   
   if(endPos == -1) return "";
   
   return StringSubstr(json, startPos, endPos - startPos);
  }

//+------------------------------------------------------------------+
//| 从JSON字符串中提取数值                                            |
//+------------------------------------------------------------------+
double ExtractJsonDouble(string json, string key)
  {
   string searchKey = "\"" + key + "\":";
   int startPos = StringFind(json, searchKey);
   
   if(startPos == -1) return 0;
   
   startPos += StringLen(searchKey);
   int endPos = StringFind(json, ",", startPos);
   
   if(endPos == -1) endPos = StringFind(json, "}", startPos);
   if(endPos == -1) return 0;
   
   string valueStr = StringSubstr(json, startPos, endPos - startPos);
   return StringToDouble(valueStr);
  }

//+------------------------------------------------------------------+
//| 执行交易                                                          |
//+------------------------------------------------------------------+
void ExecuteTrade(ENUM_ORDER_TYPE orderType, double volume, double sl, double tp)
  {
   if(volume <= 0)
     {
      Print("Invalid volume: ", volume);
      return;
     }
   
   // 如果没有指定止损/止盈，按照千分之一计算
   double price = (orderType == ORDER_TYPE_BUY) ? g_askPrice : g_bidPrice;
   if(sl <= 0)
     {
      if(orderType == ORDER_TYPE_BUY)
         sl = price * (1.0 - 0.001);
      else
         sl = price * (1.0 + 0.001);
     }
   if(tp <= 0)
     {
      if(orderType == ORDER_TYPE_BUY)
         tp = price * (1.0 + 0.001);
      else
         tp = price * (1.0 - 0.001);
     }
   
   // 标准化手数
   double minVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   
   volume = MathMax(minVolume, MathMin(volume, maxVolume));
   volume = MathRound(volume / stepVolume) * stepVolume;
   
   // 执行订单
   if(orderType == ORDER_TYPE_BUY)
     {
      if(trade.Buy(volume, _Symbol, 0, sl, tp, "Python AI Trade"))
        {
         Print("Buy order executed: Volume=", volume, " SL=", sl, " TP=", tp);
         RecordTrade("BUY", _Symbol, volume, sl, tp, trade.OrderOpenPrice());
        }
      else
        {
         Print("Buy order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
        }
     }
   else if(orderType == ORDER_TYPE_SELL)
     {
      if(trade.Sell(volume, _Symbol, 0, sl, tp, "Python AI Trade"))
        {
         Print("Sell order executed: Volume=", volume, " SL=", sl, " TP=", tp);
         RecordTrade("SELL", _Symbol, volume, sl, tp, trade.OrderOpenPrice());
        }
      else
        {
         Print("Sell order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
        }
     }
  }

//+------------------------------------------------------------------+
//| 记录交易到全局变量                                                |
//+------------------------------------------------------------------+
void RecordTrade(string action, string symbol, double volume, double sl, double tp, double price)
  {
   string tradeRecord = "{";
   tradeRecord += "\"time\":\"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES) + "\",";
   tradeRecord += "\"action\":\"" + action + "\",";
   tradeRecord += "\"symbol\":\"" + symbol + "\",";
   tradeRecord += "\"volume\":" + DoubleToString(volume, 2) + ",";
   tradeRecord += "\"price\":" + DoubleToString(price, _Digits) + ",";
   tradeRecord += "\"sl\":" + DoubleToString(sl, _Digits) + ",";
   tradeRecord += "\"tp\":" + DoubleToString(tp, _Digits) + "";
   tradeRecord += "}";
   
   if(StringLen(g_tradesOfDay) > 0)
     {
      g_tradesOfDay += ",";
     }
   g_tradesOfDay += tradeRecord;
  }

//+------------------------------------------------------------------+
//| 发送分钟统计数据到Python服务                                      |
//+------------------------------------------------------------------+
void SendMinuteStatistics()
  {
   // 构建统计JSON
   string statisticJson = "{";
   statisticJson += "\"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES) + "\",";
   statisticJson += "\"tickCount\":" + IntegerToString(g_tickCountPerMinute) + ",";
   statisticJson += "\"bidPrice\":" + DoubleToString(g_bidPrice, _Digits) + ",";
   statisticJson += "\"askPrice\":" + DoubleToString(g_askPrice, _Digits) + ",";
   statisticJson += "\"balance\":" + DoubleToString(g_accountBalance, 2) + ",";
   statisticJson += "\"equity\":" + DoubleToString(g_accountEquity, 2) + ",";
   statisticJson += "\"marginLevel\":" + DoubleToString(g_marginLevel, 2) + ",";
   statisticJson += "\"positions\":" + GetPositionsSummary() + ",";
   statisticJson += "\"trades\":[" + g_tradesOfDay + "]";
   statisticJson += "}";
   
   // 发送到Python服务
   SendToPythonServer(statisticJson);
   
   // 重置数据
   g_tradesOfDay = "";
  }

//+------------------------------------------------------------------+
//| 发送数据到Python服务                                              |
//+------------------------------------------------------------------+
void SendToPythonServer(string jsonData)
  {
   string headers = "Content-Type: application/json\r\n";
   char responseData[];
   int responseCode = 0;
   
   responseCode = WebRequest("POST", g_pythonServer + "/send_statistics", headers, jsonData, responseData);
   
   if(responseCode == 200)
     {
      Print("Statistics sent successfully");
     }
   else if(responseCode != -1)
     {
      Print("Failed to send statistics. Response code: ", responseCode);
     }
  }
//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
//--- 更新统计数据
   UpdateStatistics();
   
//--- 检查是否需要进行分钟级统计和发送
   datetime now = TimeCurrent();
   if(now - g_lastStatisticTime >= 60)  // 每分钟执行一次
     {
      SendMinuteStatistics();
      g_lastStatisticTime = now;
      g_tickCountPerMinute = 0;
     }
   
//--- 检查持仓风险并平仓
   CheckAndCloseRiskyPositions();
   
//--- 每100毫秒请求一次Python服务
   uint currentTime = GetTickCount();
   if((currentTime - g_lastPythonRequestTime) >= g_pythonRequestInterval)
     {
      RequestTradesFromPython();
      g_lastPythonRequestTime = currentTime;
     }
  }
//+------------------------------------------------------------------+
