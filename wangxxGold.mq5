//+------------------------------------------------------------------+
//|                                                     wwxxgold.mq5 |
//|                                                     wwananggxxxx |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "wwananggxxxx"
#property link      "https://www.mql5.com"
#property version   "2.01"  // <-- 版本号已更新，确认编译的是最新版本
#property strict

//--- 需要访问Web请求权限
#include <Trade/Trade.mqh>
#include <Trade/SymbolInfo.mqh>
#include <Trade/PositionInfo.mqh>
#include <Trade/OrderInfo.mqh>

//--- 财经日历事件类型常量 (用于switch语句)
// 注意: 不使用const，因为switch需要编译时常量
// CALENDAR_EVENT_TYPE_INDICATOR = 1
// CALENDAR_EVENT_TYPE_SPEECH = 2
// CALENDAR_EVENT_TYPE_MEETING = 3
// CALENDAR_EVENT_TYPE_HOLIDAY = 4

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
double g_spread = 0;           // 点差（金额）
double g_spreadPoints = 0;     // 点差（点数）
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

// 最后一次Tick时间戳
datetime g_lastTickTime = 0;

// 交易类对象
CTrade trade;
CSymbolInfo symbolInfo;
CPositionInfo positionInfo;

// 风险管理相关
double g_riskLimitPercent = 30.0;  // 30% 账户风险限制

//+------------------------------------------------------------------+
//| 财经日历相关变量                                                  |
//+------------------------------------------------------------------+

// 财经日历事件存储结构
struct CalendarEventData
  {
   long               event_id;           // 事件ID
   string             name;               // 事件名称
   string             currency;           // 货币代码
   string             country;            // 国家代码
   int                importance;         // 重要性 0-3
   datetime           publish_time;       // 发布时间
   string             forecast;           // 预测值
   string             previous;           // 前值
   string             actual;             // 实际值
   string             event_type;         // 事件类型
  };

// 存储最近2小时的财经事件（按时间排序）
CalendarEventData g_calendarEvents[];
int g_calendarEventCount = 0;
int g_maxCalendarEvents = 500;  // 最大存储事件数

// 日历检查相关
datetime g_lastCalendarCheckTime = 0;     // 上次检查时间
int g_calendarCheckInterval = 300;        // 检查间隔（秒），5分钟
datetime g_nextEventPublishTime = 0;      // 下一个重要事件发布时间
bool g_calendarInitialized = false;       // 日历是否已初始化

// 交易历史上报相关
datetime g_lastTradeHistoryReportTime = 0;  // 上次上报时间
int g_tradeHistoryReportInterval = 600;      // 上报间隔（秒），10分钟

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
         result += CharToString((uchar)ch);
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
   g_lastCalendarCheckTime = 0;  // 初始化财经日历检查时间
   g_lastTradeHistoryReportTime = 0;  // 初始化交易历史上报时间

//--- 初始化随机数种子
   MathSrand((uint)TimeCurrent());

//--- 初始化财经日历事件数组
   ArrayResize(g_calendarEvents, g_maxCalendarEvents);
   g_calendarEventCount = 0;

//--- 设置定时器，每1秒触发一次
   EventSetTimer(1);

//--- 打印初始化信息
   Print("Expert initialized successfully");
   Print("Python server: ", g_pythonServer);
   Print("Risk limit: ", g_riskLimitPercent, "%");

//--- 启动时推送历史K线数据
   Print("Pushing historical K-line data...");
   PushAllKlineData(true);  // is_full = true

//--- 启动时获取财经日历
   Print("Fetching calendar data...");
   CheckAndUpdateCalendar();

//--- 启动时上报交易历史
   Print("Reporting trade history...");
   ReportTradeHistory();

//---
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
//--- 取消定时器
   EventKillTimer();
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

      // 计算点差
      g_spread = g_askPrice - g_bidPrice;
      // 计算点差（点数）= 点差金额 / 点值
      double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      if(point > 0)
        {
         g_spreadPoints = g_spread / point;
        }
     }

//--- 获取账户信息
   g_accountBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_accountEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   g_marginLevel = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
  }

//+------------------------------------------------------------------+
//| 获取持仓汇总信息 - 返回JSON格式字符串                             |
//| 参数: onlyCurrentSymbol - true只获取当前品种，false获取所有品种   |
//+------------------------------------------------------------------+
string GetPositionsSummary(bool onlyCurrentSymbol = true)
  {
   string summary = "[";
   int positionCount = 0;

   for(int i = 0; i < PositionsTotal(); i++)
     {
      if(!PositionGetTicket(i)) continue;

      string posSymbol = PositionGetString(POSITION_SYMBOL);
      if(onlyCurrentSymbol && posSymbol != _Symbol) continue;  // 只统计当前品种

      double posVolume = PositionGetDouble(POSITION_VOLUME);
      double posPriceOpen = PositionGetDouble(POSITION_PRICE_OPEN);
      double posProfit = PositionGetDouble(POSITION_PROFIT);
      double posSL = PositionGetDouble(POSITION_SL);
      double posTP = PositionGetDouble(POSITION_TP);
      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      // 获取当前价格（需要根据品种获取对应的bid/ask）
      double currentPrice = 0;
      if(posSymbol == _Symbol)
        {
         currentPrice = (posType == POSITION_TYPE_BUY) ? g_bidPrice : g_askPrice;
        }
      else
        {
         // 对于其他品种，使用当前tick价格
         MqlTick tick;
         if(SymbolInfoTick(posSymbol, tick))
           {
            currentPrice = (posType == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
           }
        }

      double distanceSL = (posSL > 0) ? MathAbs(currentPrice - posSL) : 0;
      double distanceTP = (posTP > 0) ? MathAbs(posTP - currentPrice) : 0;

      if(positionCount > 0) summary += ",";
      summary += "{";
      summary += "\"ticket\":" + IntegerToString(PositionGetInteger(POSITION_TICKET)) + ",";
      summary += "\"symbol\":\"" + posSymbol + "\",";
      summary += "\"volume\":" + DoubleToString(posVolume, 2) + ",";
      summary += "\"priceOpen\":" + DoubleToString(posPriceOpen, _Digits) + ",";
      summary += "\"type\":\"" + (posType == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
      summary += "\"profit\":" + DoubleToString(posProfit, 2) + ",";
      summary += "\"sl\":" + DoubleToString(posSL, _Digits) + ",";
      summary += "\"tp\":" + DoubleToString(posTP, _Digits) + ",";
      summary += "\"distanceSL\":" + DoubleToString(distanceSL, _Digits) + ",";
      summary += "\"distanceTP\":" + DoubleToString(distanceTP, _Digits) + "";
      summary += "}";

      positionCount++;
     }

   summary += "]";
   return summary;
  }

//+------------------------------------------------------------------+
//| 发送持仓数据到Python服务                                          |
//| 参数: allSymbols - true发送所有品种持仓，false只发送当前品种      |
//+------------------------------------------------------------------+
void SendPositionsToPython(bool allSymbols = true)
  {
   string positions = GetPositionsSummary(!allSymbols);  // allSymbols=true时，onlyCurrentSymbol=false

   // 构建JSON请求体
   string jsonBody = "{";
   jsonBody += "\"symbol\":\"" + _Symbol + "\",";  // 当前品种
   jsonBody += "\"positions\":" + positions;
   jsonBody += "}";

   // 发送HTTP POST请求
   string headers = "Content-Type: application/json\r\n";
   uchar postData[];
   uchar responseData[];
   string outheaders = "";
   int responseCode = 0;

   // 将JSON字符串转换为字节数组
   StringToCharArray(jsonBody, postData, 0, WHOLE_ARRAY, CP_UTF8);
   // 移除末尾的null字符
   ArrayResize(postData, ArraySize(postData) - 1);

   string url = g_pythonServer + "/ea/positions";
   responseCode = WebRequest("POST", url, headers, 5000, postData, responseData, outheaders);

   if(responseCode == 200)
     {
      Print("[持仓上报] 成功上报持仓数据");
     }
   else if(responseCode != -1)
     {
      Print("[持仓上报] 失败. Response code: ", responseCode);
     }
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

   bool hasTrades = false;
   bool hasCloseTickets = false;

   // 提取trades数组
   int tradesPos = StringFind(jsonData, "\"trades\":");
   if(tradesPos != -1)
     {
      int tradesStart = StringFind(jsonData, "[", tradesPos);
      int tradesEnd = StringFind(jsonData, "]", tradesStart);
      if(tradesStart != -1 && tradesEnd != -1)
        {
         string tradesJson = StringSubstr(jsonData, tradesStart, tradesEnd - tradesStart + 1);
         // 如果trades数组不为空
         if(tradesJson != "[]")
           {
            Print("[EA] 收到交易指令: ", tradesJson);
            hasTrades = true;
            ParseTradeArray(tradesJson);
           }
        }
     }
   else
     {
      // 旧格式兼容：直接是数组 [...]
      if(StringFind(jsonData, "[") == 0 && StringFind(jsonData, "]") > 0)
        {
         string content = StringSubstr(jsonData, 1, StringLen(jsonData) - 2);
         if(StringLen(content) > 0)
           {
            hasTrades = true;
            ParseTradeArray(jsonData);
           }
        }
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
         Print("[EA] 收到close_tickets: ", closeJson);
         if(closeJson != "[]")
           {
            hasCloseTickets = true;
            ParseAndExecuteClose(closeJson);
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| 解析并执行平仓指令                                                |
//+------------------------------------------------------------------+
void ParseAndExecuteClose(string jsonData)
  {
   Print("[EA] ParseAndExecuteClose 输入: ", jsonData, " 长度: ", StringLen(jsonData));

   // 移除首尾的括号
   if(StringFind(jsonData, "[") == 0)
     {
      jsonData = StringSubstr(jsonData, 1, StringLen(jsonData) - 2);
     }

   Print("[EA] 移除括号后: ", jsonData, " 长度: ", StringLen(jsonData));

   if(StringLen(jsonData) == 0) return;

   // 直接解析数字（假设只有一个ticket）
   long ticket = StringToInteger(jsonData);
   Print("[EA] 直接解析ticket: ", ticket);

   if(ticket > 0)
     {
      ClosePositionByTicket(ticket);
     }
   else
     {
      // 如果有逗号分隔的多个ticket
      string tickets[];
      int count = StringSplit(jsonData, ',', tickets);
      Print("[EA] 多ticket模式, count=", count);

      for(int i = 0; i < count; i++)
        {
         string ticketStr = tickets[i];
         StringTrimLeft(ticketStr);
         StringTrimRight(ticketStr);
         ticket = StringToInteger(ticketStr);
         Print("[EA] ticket[", i, "] str='", ticketStr, "' -> ", ticket);
         if(ticket > 0)
           {
            ClosePositionByTicket(ticket);
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| 根据订单号平仓                                                    |
//+------------------------------------------------------------------+
void ClosePositionByTicket(long ticket)
  {
   Print("[EA] ClosePositionByTicket 尝试平仓: ticket=", ticket);

   // 使用CTrade类平仓（更简单可靠）
   if(trade.PositionClose(ticket))
     {
      Print("[平仓成功] Ticket: ", ticket);
     }
   else
     {
      Print("[平仓失败] Ticket: ", ticket, " Error: ", GetLastError(), " Retcode: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
     }
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
   string description = ExtractJsonString(tradeJson, "description");

   Print("[EA] 收到交易指令: symbol=", symbol, " action=", action, " volume=", volume, " sl=", sl, " tp=", tp, " description=", description);

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

   // 如果没有description，使用默认值
   if(description == "")
     {
      description = "Python AI Trade";
     }

   ENUM_ORDER_TYPE orderType = (action == "b") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;

   Print("[EA] 准备执行交易: ", (orderType == ORDER_TYPE_BUY ? "BUY" : "SELL"), " ", volume, " ", symbol, " desc=", description);
   ExecuteTrade(orderType, volume, sl, tp, description);
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
void ExecuteTrade(ENUM_ORDER_TYPE orderType, double volume, double sl, double tp, string description)
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
      if(trade.Buy(volume, _Symbol, 0, sl, tp, description))
        {
         Print("Buy order executed: Volume=", volume, " SL=", sl, " TP=", tp, " Description=", description);
         RecordTrade("BUY", _Symbol, volume, sl, tp, trade.ResultPrice());
        }
      else
        {
         Print("Buy order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
        }
     }
   else if(orderType == ORDER_TYPE_SELL)
     {
      if(trade.Sell(volume, _Symbol, 0, sl, tp, description))
        {
         Print("Sell order executed: Volume=", volume, " SL=", sl, " TP=", tp, " Description=", description);
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
   statisticJson += "\"spread\":" + DoubleToString(g_spread, _Digits) + ",";
   statisticJson += "\"spreadPoints\":" + DoubleToString(g_spreadPoints, 1) + ",";
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
//--- 记录最后一次Tick时间戳
   g_lastTickTime = TimeCurrent();

//--- 每100毫秒请求一次Python服务
   uint currentTime = GetTickCount();
   if((currentTime - g_lastPythonRequestTime) >= g_pythonRequestInterval)
     {
      RequestTradesFromPython();
      g_lastPythonRequestTime = currentTime;
     }
  }

//+------------------------------------------------------------------+
//| Timer function - 定时任务处理                                     |
//+------------------------------------------------------------------+
void OnTimer()
  {
//--- 检查最后一次Tick时间，如果超过10秒无Tick则跳过（可能休市）
   datetime now = TimeCurrent();
   if(g_lastTickTime == 0 || (now - g_lastTickTime) > 10)
     {
      // 无Tick超过10秒，跳过定时任务
      return;
     }

//--- 更新统计数据
   UpdateStatistics();

//--- 检查并更新财经日历（每1秒调用，但内部会判断是否需要真正获取）
   CheckAndUpdateCalendar();

//--- 检查即将发布的事件提醒
   CheckUpcomingEvents();

//--- 清理过期的财经事件（每小时清理一次）
   static datetime lastCleanupTime = 0;
   if(now - lastCleanupTime >= 3600)
     {
      CleanupExpiredCalendarEvents();
      lastCleanupTime = now;
     }

//--- 交易历史上报（每10分钟，20%概率上报）
   if(g_lastTradeHistoryReportTime == 0 || (now - g_lastTradeHistoryReportTime) >= g_tradeHistoryReportInterval)
     {
      // 20%概率上报 (0-4, 共5个值，等于0时上报)
      int randomReport = (int)(MathRand() % 5);
      if(randomReport == 0)
        {
         ReportTradeHistory();
        }
      g_lastTradeHistoryReportTime = now;
     }

//--- 检查是否需要推送增量K线数据
   CheckAndPushIncrementalKlines();

//--- 检查是否需要进行分钟级统计和发送
   if(now - g_lastStatisticTime >= 6)  // 每6秒执行一次
     {
      SendMinuteStatistics();
      g_lastStatisticTime = now;
      g_tickCount = 0;
     }

//--- 检查持仓风险并平仓
   CheckAndCloseRiskyPositions();

//--- 持仓数据上报：生成0-10的随机数，等于5时上报
   int randomNum = (int)(MathRand() % 11);
   if(randomNum == 5)
     {
      SendPositionsToPython(true);  // 上报所有品种持仓
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

//+------------------------------------------------------------------+
//| 财经日历相关函数                                                   |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| 检查并更新财经日历                                                |
//| 每1秒调用，但只在需要时才真正获取数据                              |
//+------------------------------------------------------------------+
void CheckAndUpdateCalendar()
  {
   datetime now = TimeCurrent();

   // 检查是否需要刷新日历数据
   bool needRefresh = false;
   bool refreshSingleEvent = false;
   long eventToRefresh = 0;

   // 条件1：首次初始化 → 全量刷新
   if(!g_calendarInitialized)
     {
      needRefresh = true;
     }
   // 条件2：到了定期刷新时间（每5分钟） → 全量刷新
   else if(g_lastCalendarCheckTime == 0 || (now - g_lastCalendarCheckTime) >= g_calendarCheckInterval)
     {
      needRefresh = true;
     }
   // 条件3：检查是否有事件刚到发布时间（±3秒），且没有实际值 → 只刷新该事件
   else
     {
      for(int i = 0; i < g_calendarEventCount; i++)
        {
         // 只检查重要事件
         if(g_calendarEvents[i].importance < 2) continue;

         datetime publishTime = g_calendarEvents[i].publish_time;
         int secondsDiff = (int)(now - publishTime);

         // 时间刚好到达发布时间（0-3秒内），且没有实际值
         if(secondsDiff >= 0 && secondsDiff <= 3 && g_calendarEvents[i].actual == "")
           {
            needRefresh = true;
            refreshSingleEvent = true;
            eventToRefresh = g_calendarEvents[i].event_id;
            Print("[财经日历] 事件发布时间到达，刷新事件: ", g_calendarEvents[i].name);
            break;
           }
        }
     }

   if(!needRefresh)
     {
      return;  // 无需刷新
     }

   // 需要更新日历数据
   if(refreshSingleEvent)
     {
      // 只刷新单个事件
      RefreshSingleEvent(eventToRefresh);
     }
   else
     {
      // 全量刷新
      RefreshAllCalendarEvents();
     }
  }

//+------------------------------------------------------------------+
//| 刷新单个事件的实际值                                              |
//+------------------------------------------------------------------+
void RefreshSingleEvent(long eventId)
  {
   // 找到该事件在数组中的位置
   int eventIndex = -1;
   for(int i = 0; i < g_calendarEventCount; i++)
     {
      if(g_calendarEvents[i].event_id == eventId)
        {
         eventIndex = i;
         break;
        }
     }

   if(eventIndex < 0) return;

   // 获取该事件的最新值
   MqlCalendarValue values[];
   datetime now = TimeCurrent();
   datetime startTime = now - 3600;  // 过去1小时
   datetime endTime = now + 3600;    // 未来1小时

   if(CalendarValueHistoryByEvent(eventId, values, startTime, endTime) > 0)
     {
      if(ArraySize(values) > 0)
        {
         string actualValue = CalendarValueToString(values[0].actual_value, 0);

         // 只有当实际值有更新时才处理
         if(actualValue != "" && actualValue != g_calendarEvents[eventIndex].actual)
           {
            g_calendarEvents[eventIndex].actual = actualValue;
            if(ArraySize(values) > 0 && values[0].forecast_value != DBL_MAX)
              {
               g_calendarEvents[eventIndex].forecast = CalendarValueToString(values[0].forecast_value, 0);
              }

            Print("[财经日历] 事件实际值更新: ", g_calendarEvents[eventIndex].name, " = ", actualValue);

            // 发送单个事件更新到Python
            SendSingleEventToPython(eventIndex);

            // 更新下一个重要事件时间
            CalculateNextEventTime();
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| 全量刷新财经日历                                                  |
//+------------------------------------------------------------------+
void RefreshAllCalendarEvents()
  {
   Print("[财经日历] 开始获取日历数据...");

   // 获取过去6小时到未来48小时的事件
   // 注意：MT5日历API可能会返回这个范围内的所有数据
   datetime startTime = TimeCurrent() - 6 * 3600;  // 过去6小时
   datetime endTime = startTime + 54 * 3600;       // 到未来48小时

   // 清空现有事件
   g_calendarEventCount = 0;
   ArrayResize(g_calendarEvents, g_maxCalendarEvents);

   // 获取所有国家的事件（主要关注主要经济体）
   // 注意：CalendarEventByCurrency 需要货币代码（USD, EUR等），不是国家代码
   string currencies[] = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD"};

   for(int i = 0; i < ArraySize(currencies); i++)
     {
      FetchCalendarEventsByCountry(currencies[i], startTime, endTime);
     }

   // 按发布时间排序
   SortCalendarEvents();

   // 计算下一个重要事件时间
   CalculateNextEventTime();

   // 更新检查时间
   g_lastCalendarCheckTime = TimeCurrent();
   g_calendarInitialized = true;

   Print("[财经日历] 获取完成，共 ", g_calendarEventCount, " 条事件");

   // 发送到Python服务端
   if(g_calendarEventCount > 0)
     {
      SendCalendarToPython();
     }
   else
     {
      Print("[财经日历] 警告: 未获取到任何事件数据，请检查MT5日历设置");
     }
  }

//+------------------------------------------------------------------+
//| 发送单个事件更新到Python                                          |
//+------------------------------------------------------------------+
void SendSingleEventToPython(int eventIndex)
  {
   if(eventIndex < 0 || eventIndex >= g_calendarEventCount) return;

   // 构建单个事件的JSON
   string json = "{";
   json += "\"event_id\":\"" + IntegerToString(g_calendarEvents[eventIndex].event_id) + "\",";
   json += "\"actual\":\"" + EscapeJsonString(g_calendarEvents[eventIndex].actual) + "\",";
   json += "\"forecast\":\"" + EscapeJsonString(g_calendarEvents[eventIndex].forecast) + "\",";
   json += "\"previous\":\"" + EscapeJsonString(g_calendarEvents[eventIndex].previous) + "\"";
   json += "}";

   // 发送到Python
   string headers = "Content-Type: application/json\r\n";
   uchar postData[];
   uchar responseData[];
   string outheaders = "";
   int responseCode = 0;

   StringToCharArray(json, postData);
   int nullIndex = ArraySize(postData) - 1;
   if(nullIndex >= 0 && postData[nullIndex] == 0)
     {
      ArrayResize(postData, nullIndex);
     }

   string url = g_pythonServer + "/calendar_event_result";
   responseCode = WebRequest("POST", url, headers, "", 10000, postData, ArraySize(postData), responseData, outheaders);

   if(responseCode == 200)
     {
      Print("[财经日历] 单事件更新发送成功: ", g_calendarEvents[eventIndex].name);
     }
   else
     {
      Print("[财经日历] 单事件更新发送失败，Response code: ", responseCode);
     }
  }

//+------------------------------------------------------------------+
//| 获取指定货币的财经事件                                            |
//+------------------------------------------------------------------+
void FetchCalendarEventsByCountry(string currency, datetime startTime, datetime endTime)
  {
   // 获取该货币的所有事件
   MqlCalendarEvent events[];
   int count = CalendarEventByCurrency(currency, events);

   if(count <= 0)
     {
      Print("[财经日历] ", currency, ": 未获取到事件");
      return;
     }

   Print("[财经日历] ", currency, ": 获取到 ", count, " 个事件");

   // 用于跟踪变化的变量
   ulong changeTime = 0;
   int addedCount = 0;

   // 遍历事件
   for(int i = 0; i < count && g_calendarEventCount < g_maxCalendarEvents; i++)
     {
      MqlCalendarEvent event = events[i];

      // 获取事件值
      MqlCalendarValue values[];
      datetime eventTime = 0;
      int eventImportance = 2;  // 默认中等重要性

      // 先尝试获取历史值（包含当前时间附近的值）
      bool hasValues = CalendarValueHistoryByEvent(event.id, values, startTime, endTime) > 0;

      // 如果没有历史值，尝试获取最新值
      if(!hasValues)
        {
         changeTime = 0;
         hasValues = CalendarValueLastByEvent(event.id, changeTime, values) > 0;
        }

      if(hasValues && ArraySize(values) > 0)
        {
         // 从values获取时间
         eventTime = values[0].time;

         // 只保留指定时间范围内的事件
         if(eventTime < startTime || eventTime > endTime) continue;

         // 存储事件
         g_calendarEvents[g_calendarEventCount].event_id = (long)event.id;
         g_calendarEvents[g_calendarEventCount].name = event.name;
         g_calendarEvents[g_calendarEventCount].currency = currency;
         g_calendarEvents[g_calendarEventCount].country = CharToString((uchar)event.country_id);
         g_calendarEvents[g_calendarEventCount].importance = eventImportance;
         g_calendarEvents[g_calendarEventCount].publish_time = eventTime;
         g_calendarEvents[g_calendarEventCount].event_type = EventTypeToString((int)event.type);

         g_calendarEvents[g_calendarEventCount].forecast = CalendarValueToString(values[0].forecast_value, 0);
         g_calendarEvents[g_calendarEventCount].previous = CalendarValueToString(values[0].prev_value, 0);
         g_calendarEvents[g_calendarEventCount].actual = CalendarValueToString(values[0].actual_value, 0);

         g_calendarEventCount++;
         addedCount++;
        }
     }

   Print("[财经日历] ", currency, ": 成功添加 ", addedCount, " 个事件");
  }

//+------------------------------------------------------------------+
//| 日历值转换为字符串                                                |
//+------------------------------------------------------------------+
string CalendarValueToString(double value, ushort unit)
  {
   if(value == DBL_MAX || value == 0) return "";

   string result = DoubleToString(value, 2);

   // 单位后缀已简化处理
   return result;
  }

//+------------------------------------------------------------------+
//| 事件类型转换为字符串                                              |
//+------------------------------------------------------------------+
string EventTypeToString(int type)
  {
   switch(type)
     {
      case 1: return "indicator";  // CALENDAR_EVENT_TYPE_INDICATOR
      case 2: return "speech";     // CALENDAR_EVENT_TYPE_SPEECH
      case 3: return "meeting";    // CALENDAR_EVENT_TYPE_MEETING
      case 4: return "holiday";    // CALENDAR_EVENT_TYPE_HOLIDAY
      default: return "other";
     }
  }

//+------------------------------------------------------------------+
//| 按发布时间排序事件                                                |
//+------------------------------------------------------------------+
void SortCalendarEvents()
  {
   // 简单的冒泡排序
   for(int i = 0; i < g_calendarEventCount - 1; i++)
     {
      for(int j = i + 1; j < g_calendarEventCount; j++)
        {
         if(g_calendarEvents[i].publish_time > g_calendarEvents[j].publish_time)
           {
            CalendarEventData temp = g_calendarEvents[i];
            g_calendarEvents[i] = g_calendarEvents[j];
            g_calendarEvents[j] = temp;
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| 计算下一个重要事件时间                                            |
//+------------------------------------------------------------------+
void CalculateNextEventTime()
  {
   datetime now = TimeCurrent();
   g_nextEventPublishTime = 0;

   for(int i = 0; i < g_calendarEventCount; i++)
     {
      // 只关注重要性 >= 2 的事件
      if(g_calendarEvents[i].importance >= 2 && g_calendarEvents[i].publish_time > now)
        {
         g_nextEventPublishTime = g_calendarEvents[i].publish_time;
         Print("[财经日历] 下一个重要事件: ", g_calendarEvents[i].name,
               " 时间: ", TimeToString(g_calendarEvents[i].publish_time, TIME_DATE | TIME_MINUTES));
         break;
        }
     }
  }

//+------------------------------------------------------------------+
//| 发送财经日历到Python服务                                          |
//+------------------------------------------------------------------+
void SendCalendarToPython()
  {
   if(g_calendarEventCount == 0) return;

   // 构建JSON
   string json = "{\"events\":[";

   for(int i = 0; i < g_calendarEventCount; i++)
     {
      if(i > 0) json += ",";

      json += "{";
      json += "\"id\":\"" + IntegerToString(g_calendarEvents[i].event_id) + "\",";
      json += "\"name\":\"" + EscapeJsonString(g_calendarEvents[i].name) + "\",";
      json += "\"name_en\":\"" + EscapeJsonString(g_calendarEvents[i].name) + "\",";
      json += "\"country\":\"" + EscapeJsonString(g_calendarEvents[i].country) + "\",";
      json += "\"currency\":\"" + EscapeJsonString(g_calendarEvents[i].currency) + "\",";
      json += "\"importance\":" + IntegerToString(g_calendarEvents[i].importance) + ",";
      json += "\"publish_time\":\"" + TimeToString(g_calendarEvents[i].publish_time, TIME_DATE | TIME_MINUTES | TIME_SECONDS) + "\",";
      json += "\"forecast\":\"" + EscapeJsonString(g_calendarEvents[i].forecast) + "\",";
      json += "\"previous\":\"" + EscapeJsonString(g_calendarEvents[i].previous) + "\",";
      json += "\"actual\":\"" + EscapeJsonString(g_calendarEvents[i].actual) + "\",";
      json += "\"event_type\":\"" + EscapeJsonString(g_calendarEvents[i].event_type) + "\"";
      json += "}";
     }

   json += "]}";

   // 发送到Python
   string headers = "Content-Type: application/json\r\n";
   uchar postData[];
   uchar responseData[];
   string outheaders = "";
   int responseCode = 0;

   StringToCharArray(json, postData);
   int nullIndex = ArraySize(postData) - 1;
   if(nullIndex >= 0 && postData[nullIndex] == 0)
     {
      ArrayResize(postData, nullIndex);
     }

   string url = g_pythonServer + "/calendar";
   responseCode = WebRequest("POST", url, headers, "", 10000, postData, ArraySize(postData), responseData, outheaders);

   if(responseCode == 200)
     {
      Print("[财经日历] 发送成功，共 ", g_calendarEventCount, " 条事件");
     }
   else
     {
      Print("[财经日历] 发送失败，Response code: ", responseCode);
     }
  }

//+------------------------------------------------------------------+
//| JSON字符串转义                                                    |
//| 转义所有JSON无效的控制字符(0x00-0x1F)和非ASCII字符                  |
//| 非ASCII字符使用\uXXXX格式转义，确保Unicode正确传递                  |
//+------------------------------------------------------------------+
string EscapeJsonString(string str)
  {
   string result = "";
   for(int i = 0; i < StringLen(str); i++)
     {
      ushort ch = StringGetCharacter(str, i);
      switch(ch)
        {
         case '"':  result += "\\\""; break;
         case '\\': result += "\\\\"; break;
         case '\n': result += "\\n"; break;
         case '\r': result += "\\r"; break;
         case '\t': result += "\\t"; break;
         // MQL5不支持\b和\f，使用Unicode转义
         case 0x08: result += "\\b"; break;  // backspace
         case 0x0C: result += "\\f"; break;  // form feed
         default:
           // 转义所有控制字符 (0x00-0x1F) 和 DEL (0x7F)
           if(ch < 32 || ch == 0x7F)
             {
              // 使用 \uXXXX 格式转义
              result += "\\u" + StringFormat("%04X", ch);
             }
           else if(ch < 128)
             {
              // ASCII 字符直接添加
              result += CharToString((uchar)ch);
             }
           else
             {
              // 非ASCII字符(如中文)使用 \uXXXX 格式转义
              // 这样可以避免 StringToCharArray 的编码问题
              result += "\\u" + StringFormat("%04X", ch);
             }
        }
     }
   return result;
  }

//+------------------------------------------------------------------+
//| 检查是否有事件即将发布并提醒                                      |
//+------------------------------------------------------------------+
void CheckUpcomingEvents()
  {
   if(!g_calendarInitialized || g_calendarEventCount == 0) return;

   datetime now = TimeCurrent();

   for(int i = 0; i < g_calendarEventCount; i++)
     {
      // 只检查重要事件
      if(g_calendarEvents[i].importance < 2) continue;

      datetime publishTime = g_calendarEvents[i].publish_time;
      int secondsToPublish = (int)(publishTime - now);

      // 事件将在5分钟内发布
      if(secondsToPublish > 0 && secondsToPublish <= 300)
        {
         string msg = StringFormat("[财经日历提醒] %s (%s) 将在 %d 分钟后发布",
                                   g_calendarEvents[i].name,
                                   g_calendarEvents[i].currency,
                                   secondsToPublish / 60);
         Print(msg);

         // TODO: 可以在这里添加推送通知到前端的逻辑
        }
     }
  }

//+------------------------------------------------------------------+
//| 清理过期的财经事件                                                |
//+------------------------------------------------------------------+
void CleanupExpiredCalendarEvents()
  {
   datetime now = TimeCurrent();
   int writeIndex = 0;

   for(int readIndex = 0; readIndex < g_calendarEventCount; readIndex++)
     {
      // 保留未来事件和过去2小时内的事件
      if(g_calendarEvents[readIndex].publish_time > now - 2 * 3600)
        {
         if(writeIndex != readIndex)
           {
            g_calendarEvents[writeIndex] = g_calendarEvents[readIndex];
           }
         writeIndex++;
        }
     }

   if(writeIndex != g_calendarEventCount)
     {
      Print("[财经日历] 清理过期事件: ", g_calendarEventCount - writeIndex, " 条");
      g_calendarEventCount = writeIndex;
     }
  }

//+------------------------------------------------------------------+
//| 获取并上报交易历史                                                |
//+------------------------------------------------------------------+
void ReportTradeHistory()
  {
   datetime now = TimeCurrent();
   datetime from = now - 24 * 3600;  // 最近24小时

   // 选择交易历史
   if(!HistorySelect(from, now))
     {
      Print("[交易历史] 获取交易历史失败");
      return;
     }

   // 获取成交数量
   int deals_total = HistoryDealsTotal();
   if(deals_total == 0)
     {
      Print("[交易历史] 最近24小时无成交记录");
      return;
     }

   Print("[交易历史] 最近24小时成交数: ", deals_total);

   // 构建JSON数据
   string json = "{\"deals\":[";

   int validDeals = 0;
   for(int i = 0; i < deals_total; i++)
     {
      ulong deal_ticket = HistoryDealGetTicket(i);
      if(deal_ticket == 0) continue;

      // 只处理实际成交记录（排除余额调整等）
      long deal_entry = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
      if(deal_entry != DEAL_ENTRY_IN && deal_entry != DEAL_ENTRY_OUT && deal_entry != DEAL_ENTRY_OUT_BY)
         continue;

      // 获取成交属性
      long deal_type = HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
      if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL)
         continue;  // 只处理买入和卖出

      double deal_volume = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
      double deal_price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
      double deal_profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
      double deal_swap = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
      double deal_commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
      string deal_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
      datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
      string deal_comment = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
      long deal_order = HistoryDealGetInteger(deal_ticket, DEAL_ORDER);

      if(validDeals > 0) json += ",";

      json += "{";
      json += "\"ticket\":" + IntegerToString(deal_ticket) + ",";
      json += "\"order\":" + IntegerToString(deal_order) + ",";
      json += "\"symbol\":\"" + deal_symbol + "\",";
      json += "\"type\":" + IntegerToString(deal_type) + ",";
      json += "\"entry\":" + IntegerToString(deal_entry) + ",";
      json += "\"volume\":" + DoubleToString(deal_volume, 2) + ",";
      json += "\"price\":" + DoubleToString(deal_price, 2) + ",";
      json += "\"profit\":" + DoubleToString(deal_profit, 2) + ",";
      json += "\"swap\":" + DoubleToString(deal_swap, 2) + ",";
      json += "\"commission\":" + DoubleToString(deal_commission, 2) + ",";
      json += "\"time\":\"" + TimeToString(deal_time, TIME_DATE | TIME_MINUTES | TIME_SECONDS) + "\",";
      json += "\"comment\":\"" + EscapeJsonString(deal_comment) + "\"";
      json += "}";

      validDeals++;
     }

   json += "]}";

   if(validDeals == 0)
     {
      Print("[交易历史] 无有效成交记录");
      return;
     }

   // 发送到Python服务端
   string headers = "Content-Type: application/json\r\n";
   uchar postData[];
   uchar responseData[];
   string outheaders = "";
   int responseCode = 0;

   StringToCharArray(json, postData);
   int nullIndex = ArraySize(postData) - 1;
   if(nullIndex >= 0 && postData[nullIndex] == 0)
     {
      ArrayResize(postData, nullIndex);
     }

   string url = g_pythonServer + "/trade_history";
   responseCode = WebRequest("POST", url, headers, "", 15000, postData, ArraySize(postData), responseData, outheaders);

   if(responseCode == 200)
     {
      Print("[交易历史] 上报成功，共 ", validDeals, " 条成交记录");
     }
   else
     {
      Print("[交易历史] 上报失败，Response code: ", responseCode);
     }
  }
