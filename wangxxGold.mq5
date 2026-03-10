//+------------------------------------------------------------------+
//|                                                     wwxxgold.mq5 |
//|                                                     wwananggxxxx |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "wwananggxxxx"
#property link      "https://www.mql5.com"
#property version   "2.00"
#property strict

//--- 需要访问Web请求权限
#include <Trade/Trade.mqh>
#include <Trade/SymbolInfo.mqh>
#include <Trade/PositionInfo.mqh>
#include <Trade/OrderInfo.mqh>

//+------------------------------------------------------------------+
//| 全局变量定义                                                     |
//+------------------------------------------------------------------+

// Python 服务配置
string g_pythonServer = "http://127.0.0.1:8000";
uint g_lastPythonRequestTime = 0;
uint g_pythonRequestInterval = 100;  // 毫秒

// 统计数据 - 每分钟重置
datetime g_lastStatisticTime = 0;
int g_tickCount = 0;
double g_bidPrice = 0;
double g_askPrice = 0;
double g_accountBalance = 0;
double g_accountEquity = 0;
double g_marginLevel = 0;
string g_positionsSummary = "";  // JSON 格式的持仓汇总

// 当日交易记录 - 用于发送到Python
string g_tradesOfDay = "";

// K线数据推送相关
bool g_klineInitialized = false;           // 是否已发送历史K线数据
datetime g_lastKlinePushTime = 0;          // 上次推送K线时间
int g_klinePushInterval = 60;              // K线推送间隔（秒）
datetime g_lastH4CloseTime = 0;            // 上次H4 K线收盘时间
datetime g_lastH1CloseTime = 0;            // 上次H1 K线收盘时间
datetime g_lastM15CloseTime = 0;           // 上次M15 K线收盘时间
datetime g_lastM5CloseTime = 0;            // 上次M5 K线收盘时间
datetime g_lastM1CloseTime = 0;            // 上次M1 K线收盘时间

// 交易类对象
CTrade trade;
CSymbolInfo symbolInfo;
CPositionInfo positionInfo;

// 风险管理相关
double g_riskLimitPercent = 30.0;  // 30% 账户风险限制

//+------------------------------------------------------------------+
//| URL编码函数 - 处理特殊字符                                        |
//+------------------------------------------------------------------+
string URLEncode(string str)
  {
   string result = "";
   for(int i = 0; i < StringLen(str); i++)
     {
      ushort ch = StringGetCharacter(str, i);
      // 字母、数字、连字符、下划线、点号不需要编码
      if((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') ||
         ch == '-' || ch == '_' || ch == '.')
        {
         result += CharToString(ch);
        }
      else
        {
         // 其他字符编码为 %XX 格式
         result += "%" + StringFormat("%02X", ch);
        }
     }
   return result;
  }

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
   g_lastKlinePushTime = TimeCurrent();

//--- 打印初始化信息
   Print("Expert initialized successfully");
   Print("Python server: ", g_pythonServer);
   Print("Risk limit: ", g_riskLimitPercent, "%");

//--- 启动时推送历史K线数据
   Print("Pushing historical K-line data...");
   PushAllKlineData(true);  // is_full = true

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
   g_tickCount++;
   
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
         long posTicket = PositionGetInteger(POSITION_TICKET);
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
   uchar responseData[];
   string response = "";
   string outheaders = "";
   int responseCode = 0;
   
   // 构建请求URL，携带SYMBOL和当前价格
   string currentPrice = DoubleToString((g_bidPrice + g_askPrice) / 2, _Digits);
   string encodedSymbol = URLEncode(_Symbol);
   string url = g_pythonServer + "/get_trades?symbol=" + encodedSymbol + "&price=" + currentPrice;
   
   // 建立HTTP请求到Python服务
   uchar emptyData[];
   responseCode = WebRequest("GET", url, headers, 5000, emptyData, responseData, outheaders);  // timeout设为5秒

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
      Print("URL: ", url);

      // 打印错误详情
      if(responseCode == 404)
         Print("Endpoint not found. Check server URL.");
      else if(responseCode == 500)
         Print("Server error. Check server logs.");
     }
   else if(responseCode == -1)
     {
      Print("WebRequest is disabled! Please enable WebRequest in MT5 Options -> Expert Advisors");
      Print("Make sure 'localhost' is added to the WebRequest allowed list");
     }
  }

//+------------------------------------------------------------------+
//| 解析JSON格式的交易指令并执行                                      |
//+------------------------------------------------------------------+
void ParseAndExecuteTrades(string jsonData)
  {
   // JSON格式: {"trades": [...], "close_tickets": [...], "pivot_alerts": [...]}
   // EA只处理trades和close_tickets，pivot_alerts由Python推送到前端

   if(StringLen(jsonData) == 0) return;

   // 提取trades数组
   int tradesPos = StringFind(jsonData, "\"trades\":");
   if(tradesPos != -1)
     {
      int tradesStart = StringFind(jsonData, "[", tradesPos);
      int tradesEnd = StringFind(jsonData, "]", tradesStart);
      if(tradesStart != -1 && tradesEnd != -1)
        {
         string tradesJson = StringSubstr(jsonData, tradesStart, tradesEnd - tradesStart + 1);
         // 如果trades数组不为空，打印出来
         if(tradesJson != "[]")
           {
            Print("[EA] 收到交易指令: ", tradesJson);
           }
         ParseTradeArray(tradesJson);
        }
     }
   else
     {
      // 旧格式兼容：直接是数组 [...]
      ParseTradeArray(jsonData);
     }

   // 提取close_tickets数组并执行平仓
   int closePos = StringFind(jsonData, "\"close_tickets\":");
   if(closePos != -1)
     {
      int closeStart = StringFind(jsonData, "[", closePos);
      int closeEnd = StringFind(jsonData, "]", closeStart);
      if(closeStart != -1 && closeEnd != -1)
        {
         string closeJson = StringSubstr(jsonData, closeStart, closeEnd - closeStart + 1);
         ParseAndExecuteClose(closeJson);
        }
     }
  }

//+------------------------------------------------------------------+
//| 解析并执行平仓指令                                                |
//+------------------------------------------------------------------+
void ParseAndExecuteClose(string jsonData)
  {
   // 移除首尾的括号
   if(StringFind(jsonData, "[") == 0)
     {
      jsonData = StringSubstr(jsonData, 1, StringLen(jsonData) - 2);
     }

   if(StringLen(jsonData) == 0) return;

   // 解析ticket列表
   string tickets[];
   int count = StringSplit(jsonData, ',', tickets);

   for(int i = 0; i < count; i++)
     {
      string ticketStr = tickets[i];
      ticketStr = StringTrimLeft(ticketStr);
      ticketStr = StringTrimRight(ticketStr);

      long ticket = StringToInteger(ticketStr);
      if(ticket > 0)
        {
         ClosePositionByTicket(ticket);
        }
     }
  }

//+------------------------------------------------------------------+
//| 根据订单号平仓                                                    |
//+------------------------------------------------------------------+
void ClosePositionByTicket(long ticket)
  {
   // 查找持仓
   for(int i = 0; i < PositionsTotal(); i++)
     {
      if(PositionGetTicket(i) == ticket)
        {
         string posSymbol = PositionGetString(POSITION_SYMBOL);
         double posVolume = PositionGetDouble(POSITION_VOLUME);
         ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

         // 构造平仓请求
         MqlTradeRequest request = {};
         MqlTradeResult result = {};

         request.action = TRADE_ACTION_DEAL;
         request.position = ticket;
         request.symbol = posSymbol;
         request.volume = posVolume;
         request.type = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
         request.comment = "Close by Python command";

         if(OrderSend(request, result))
           {
            Print("[平仓成功] Ticket: ", ticket, " Symbol: ", posSymbol);
           }
         else
           {
            Print("[平仓失败] Ticket: ", ticket, " Error: ", GetLastError());
           }

         return;
        }
     }

   Print("[平仓] 未找到订单号: ", ticket);
  }

//+------------------------------------------------------------------+
//| 解析交易数组                                                      |
//+------------------------------------------------------------------+
void ParseTradeArray(string jsonData)
  {
   // 移除首尾的括号
   if(StringFind(jsonData, "[") == 0)
     {
      jsonData = StringSubstr(jsonData, 1, StringLen(jsonData) - 2);
     }

   if(StringLen(jsonData) == 0) return;

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

   Print("[EA] 收到交易指令: symbol=", symbol, " action=", action, " volume=", volume, " sl=", sl, " tp=", tp);

   if(symbol == "" || action == "" || volume <= 0)
     {
      Print("[EA] 交易参数无效，跳过");
      return;
     }

   if(symbol != _Symbol)
     {
      Print("[EA] Symbol不匹配，跳过。收到: ", symbol, " 当前品种: ", _Symbol);
      return;
     }

   ENUM_ORDER_TYPE orderType = (action == "b") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   Print("[EA] 准备执行交易: ", (orderType == ORDER_TYPE_BUY ? "BUY" : "SELL"), " ", volume, " ", symbol);
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
         RecordTrade("BUY", _Symbol, volume, sl, tp, trade.ResultPrice());
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
         RecordTrade("SELL", _Symbol, volume, sl, tp, trade.ResultPrice());
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
   statisticJson += "\"symbol\":\"" + _Symbol + "\",";
   statisticJson += "\"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_MINUTES) + "\",";
   statisticJson += "\"tickCount\":" + IntegerToString(g_tickCount) + ",";
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
   uchar responseData[];
   string outheaders = "";
   int responseCode = 0;

   // 使用CharArrayToString确保正确转换，然后再转回uchar数组
   string jsonStr = jsonData;
   uchar postData[];
   StringToCharArray(jsonStr, postData);

   // 移除StringToCharArray添加的null终止符
   int nullIndex = ArraySize(postData) - 1;
   if(nullIndex >= 0 && postData[nullIndex] == 0)
     {
      ArrayResize(postData, nullIndex);
     }

   int dataSize = ArraySize(postData);

   // 调试：打印发送的数据
   Print("Sending JSON data size: ", dataSize, " bytes");
   Print("JSON: ", jsonStr);

   // 修正: POST请求需要9个参数 (method, url, headers, cookie, timeout, data, dataSize, result, resultHeaders)
   responseCode = WebRequest(
      "POST",
      g_pythonServer + "/send_statistics",
      headers,
      "",           // cookie
      5000,         // timeout (5秒)
      postData,
      dataSize,
      responseData,
      outheaders
   );

   if(responseCode == 200)
     {
      Print("Statistics sent successfully");
     }
   else if(responseCode != -1)
     {
      Print("Failed to send statistics. Response code: ", responseCode);

      // 打印详细错误信息
      if(responseCode == -1)
        {
         Print("WebRequest is disabled! Please enable WebRequest in MT5 Options -> Expert Advisors");
        }
      else
        {
         // 打印响应内容以便调试
         string responseText = "";
         for(int i = 0; i < ArraySize(responseData); i++)
           {
            responseText += CharToString(responseData[i]);
           }
         Print("Response: ", responseText);
        }
     }
  }
//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
//--- 更新统计数据
   UpdateStatistics();

//--- 检查是否需要推送增量K线数据
   CheckAndPushIncrementalKlines();

//--- 检查是否需要进行分钟级统计和发送
   datetime now = TimeCurrent();
   if(now - g_lastStatisticTime >= 6)  // 每6秒执行一次
     {
      SendMinuteStatistics();
      g_lastStatisticTime = now;
      g_tickCount = 0;
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

//+------------------------------------------------------------------+
//| K线数据相关函数                                                   |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| 推送所有周期的K线数据                                             |
//+------------------------------------------------------------------+
bool PushAllKlineData(bool isFull)
  {
   bool success = true;

   // 推送各周期K线数据
   // H4: 6个月约1100根
   if(!PushKlineData(PERIOD_H4, isFull ? 1100 : 1))
      success = false;

   // H1: 1个月约720根
   if(!PushKlineData(PERIOD_H1, isFull ? 720 : 1))
      success = false;

   // M15: 3天约288根
   if(!PushKlineData(PERIOD_M15, isFull ? 288 : 1))
      success = false;

   // M5: 24小时约288根
   if(!PushKlineData(PERIOD_M5, isFull ? 288 : 1))
      success = false;

   // M1: 1小时约60根
   if(!PushKlineData(PERIOD_M1, isFull ? 60 : 1))
      success = false;

   if(success && isFull)
     {
      g_klineInitialized = true;
      Print("Historical K-line data pushed successfully");
     }

   return success;
  }

//+------------------------------------------------------------------+
//| 推送单个周期的K线数据                                             |
//+------------------------------------------------------------------+
bool PushKlineData(ENUM_TIMEFRAMES period, int count)
  {
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   // 获取K线数据
   int copied = CopyRates(_Symbol, period, 0, count, rates);
   if(copied <= 0)
     {
      Print("Failed to get K-line data for period: ", PeriodToString(period));
      return false;
     }

   // 构建JSON
   string klineJson = BuildKlineJson(period, rates, copied);

   // 发送到Python服务
   string periodStr = PeriodToString(period);
   string url = g_pythonServer + "/ea/kline/" + periodStr;

   return SendKlineToServer(url, klineJson);
  }

//+------------------------------------------------------------------+
//| 构建K线JSON数据                                                   |
//+------------------------------------------------------------------+
string BuildKlineJson(ENUM_TIMEFRAMES period, MqlRates &rates[], int count)
  {
   string json = "{\"symbol\":\"" + _Symbol + "\",";
   json += "\"is_full\":" + (g_klineInitialized ? "false" : "true") + ",";
   json += "\"klines\":[";

   for(int i = count - 1; i >= 0; i--)  // 从旧到新排序
     {
      if(i < count - 1) json += ",";
      json += "{";
      json += "\"timestamp\":\"" + TimeToString(rates[i].time, TIME_DATE | TIME_MINUTES) + "\",";
      json += "\"open\":" + DoubleToString(rates[i].open, _Digits) + ",";
      json += "\"high\":" + DoubleToString(rates[i].high, _Digits) + ",";
      json += "\"low\":" + DoubleToString(rates[i].low, _Digits) + ",";
      json += "\"close\":" + DoubleToString(rates[i].close, _Digits) + ",";
      json += "\"volume\":" + DoubleToString(rates[i].tick_volume, 0);
      json += "}";
     }

   json += "]}";
   return json;
  }

//+------------------------------------------------------------------+
//| 发送K线数据到服务器                                               |
//+------------------------------------------------------------------+
bool SendKlineToServer(string url, string jsonData)
  {
   string headers = "Content-Type: application/json\r\n";
   uchar responseData[];
   uchar postData[];
   string outheaders = "";
   int responseCode = 0;

   StringToCharArray(jsonData, postData);
   int nullIndex = ArraySize(postData) - 1;
   if(nullIndex >= 0 && postData[nullIndex] == 0)
     {
      ArrayResize(postData, nullIndex);
     }

   int dataSize = ArraySize(postData);

   responseCode = WebRequest(
      "POST",
      url,
      headers,
      "",
      10000,  // 10秒超时
      postData,
      dataSize,
      responseData,
      outheaders
   );

   if(responseCode == 200)
     {
      return true;
     }
   else if(responseCode == 400)
     {
      // 检查是否是8888错误码（需要全量数据）
      string responseText = "";
      for(int i = 0; i < ArraySize(responseData); i++)
        {
         responseText += CharToString(responseData[i]);
        }

      if(StringFind(responseText, "8888") >= 0)
        {
         Print("Server needs full K-line data, resending...");
         g_klineInitialized = false;
         PushAllKlineData(true);
        }
      return false;
     }
   else
     {
      Print("Failed to push K-line data. Response code: ", responseCode);
      return false;
     }
  }

//+------------------------------------------------------------------+
//| 检查并推送增量K线数据                                             |
//+------------------------------------------------------------------+
void CheckAndPushIncrementalKlines()
  {
   datetime now = TimeCurrent();
   datetime barTime;

   // 检查H4 K线是否有新周期
   barTime = iTime(_Symbol, PERIOD_H4, 0);
   if(barTime != 0 && barTime != g_lastH4CloseTime)
     {
      g_lastH4CloseTime = barTime;
      if(g_klineInitialized) PushKlineData(PERIOD_H4, 1);
     }

   // 检查H1 K线
   barTime = iTime(_Symbol, PERIOD_H1, 0);
   if(barTime != 0 && barTime != g_lastH1CloseTime)
     {
      g_lastH1CloseTime = barTime;
      if(g_klineInitialized) PushKlineData(PERIOD_H1, 1);
     }

   // 检查M15 K线
   barTime = iTime(_Symbol, PERIOD_M15, 0);
   if(barTime != 0 && barTime != g_lastM15CloseTime)
     {
      g_lastM15CloseTime = barTime;
      if(g_klineInitialized) PushKlineData(PERIOD_M15, 1);
     }

   // 检查M5 K线
   barTime = iTime(_Symbol, PERIOD_M5, 0);
   if(barTime != 0 && barTime != g_lastM5CloseTime)
     {
      g_lastM5CloseTime = barTime;
      if(g_klineInitialized) PushKlineData(PERIOD_M5, 1);
     }

   // 检查M1 K线
   barTime = iTime(_Symbol, PERIOD_M1, 0);
   if(barTime != 0 && barTime != g_lastM1CloseTime)
     {
      g_lastM1CloseTime = barTime;
      if(g_klineInitialized) PushKlineData(PERIOD_M1, 1);
     }
  }

//+------------------------------------------------------------------+
//| 周期转换为字符串                                                  |
//+------------------------------------------------------------------+
string PeriodToString(ENUM_TIMEFRAMES period)
  {
   switch(period)
     {
      case PERIOD_H4:  return "H4";
      case PERIOD_H1:  return "H1";
      case PERIOD_M15: return "M15";
      case PERIOD_M5:  return "M5";
      case PERIOD_M1:  return "M1";
      default: return "M5";
     }
  }
