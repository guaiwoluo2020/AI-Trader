//+------------------------------------------------------------------+
//|                                              mt5TerminalEA.mq5 |
//|                                                     wwananggxxxx |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "wwananggxxxx"
#property link      "https://www.mql5.com"
#property version   "2.04"
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
input string InpServerUrl = "http://182.92.119.121/api"; // WebRequest 白名单中的服务地址
input long InpWebUserId = 0;       // 高级用法：手工绑定 user_id
input string InpEaToken = "";      // 高级用法：手工绑定 EA token
string g_pythonServer = "";
long g_webUserId = 0;
string g_eaToken = "";
string g_activationCode = "";
string g_credentialsFile = "AITrader_credentials.dat";
uint g_lastPythonRequestTime = 0;
uint g_pythonRequestInterval = 100;  // 毫秒
uint g_lastHistoryTaskPollTime = 0;
uint g_historyTaskPollInterval = 5000;  // 历史数据任务每5秒处理一个分片
bool g_historyTaskActive = false;
string g_historyRetryDatasetId = "";
int g_historyRetryChunkIndex = -1;
int g_historyNotFoundRetryCount = 0;
int g_historyNotFoundRetryLimit = 3;

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
double g_freeMargin = 0;
double g_margin = 0;
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
//| 构建带账户绑定凭证的请求头                                       |
//+------------------------------------------------------------------+
string BuildAuthenticatedHeaders()
  {
   return "Content-Type: application/json\r\n"
          + "X-EA-User-ID: " + IntegerToString(g_webUserId) + "\r\n"
          + "X-EA-Token: " + g_eaToken + "\r\n";
  }

//+------------------------------------------------------------------+
//| 从运行文件名读取一次性激活码                                     |
//+------------------------------------------------------------------+
string GetActivationCodeFromProgramName()
  {
   string programName = MQLInfoString(MQL_PROGRAM_NAME);
   string prefix = "mt5TerminalEA_";
   int prefixPos = StringFind(programName, prefix);
   if(prefixPos != 0)
      return "";

   string code = StringSubstr(programName, StringLen(prefix));
   int extensionPos = StringFind(code, ".");
   if(extensionPos >= 0)
      code = StringSubstr(code, 0, extensionPos);
   StringToUpper(code);

   if(StringLen(code) != 12)
      return "";
   for(int i = 0; i < StringLen(code); i++)
     {
      ushort ch = StringGetCharacter(code, i);
      bool isLetter = (ch >= 'A' && ch <= 'Z');
      bool isDigit = (ch >= '2' && ch <= '9');
      if(!isLetter && !isDigit)
         return "";
     }
   return code;
  }

//+------------------------------------------------------------------+
//| 读取当前 MT5 终端保存的凭证                                      |
//+------------------------------------------------------------------+
bool LoadCredentials(string expectedActivationCode)
  {
   int handle = FileOpen(g_credentialsFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return false;

   string savedServer = FileReadString(handle);
   string savedCode = FileReadString(handle);
   string savedUserId = FileReadString(handle);
   string savedToken = FileReadString(handle);
   FileClose(handle);

   if(savedServer != g_pythonServer || savedCode != expectedActivationCode ||
      StringLen(savedToken) == 0)
      return false;

   long userId = (long)StringToInteger(savedUserId);
   if(userId <= 0)
      return false;

   g_webUserId = userId;
   g_eaToken = savedToken;
   return true;
  }

//+------------------------------------------------------------------+
//| 保存凭证到当前 MT5 终端                                          |
//+------------------------------------------------------------------+
bool SaveCredentials(string activationCode)
  {
   int handle = FileOpen(
      g_credentialsFile,
      FILE_WRITE | FILE_TXT | FILE_ANSI
   );
   if(handle == INVALID_HANDLE)
      return false;

   FileWrite(handle, g_pythonServer);
   FileWrite(handle, activationCode);
   FileWrite(handle, IntegerToString(g_webUserId));
   FileWrite(handle, g_eaToken);
   FileClose(handle);
   return true;
  }

//+------------------------------------------------------------------+
//| 使用文件名中的一次性激活码换取账户凭证                           |
//+------------------------------------------------------------------+
bool ActivateEA(string activationCode)
  {
   string programName = MQLInfoString(MQL_PROGRAM_NAME);
   string jsonBody = "{";
   jsonBody += "\"activation_code\":\"" + activationCode + "\",";
   jsonBody += "\"mt5_login\":\""
               + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) + "\",";
   jsonBody += "\"mt5_server\":\""
               + EscapeJsonString(AccountInfoString(ACCOUNT_SERVER)) + "\",";
   jsonBody += "\"ea_version\":\"2.04\",";
   jsonBody += "\"program_name\":\"" + EscapeJsonString(programName) + "\"";
   jsonBody += "}";

   uchar postData[];
   uchar responseData[];
   StringToCharArray(jsonBody, postData, 0, WHOLE_ARRAY, CP_UTF8);
   if(ArraySize(postData) > 0)
      ArrayResize(postData, ArraySize(postData) - 1);

   string responseHeaders = "";
   ResetLastError();
   int responseCode = WebRequest(
      "POST",
      g_pythonServer + "/ea/activate",
      "Content-Type: application/json\r\n",
      10000,
      postData,
      responseData,
      responseHeaders
   );
   if(responseCode != 200)
     {
      Print(
         "[EA Activation] Failed. HTTP=", responseCode,
         ", error=", GetLastError(),
         ". Check the activation file and WebRequest whitelist."
      );
      return false;
     }

   string responseText = CharArrayToString(
      responseData, 0, WHOLE_ARRAY, CP_UTF8
   );
   long userId = (long)ExtractJsonDouble(responseText, "user_id");
   string token = ExtractJsonString(responseText, "ea_token");
   if(userId <= 0 || StringLen(token) == 0)
     {
      Print("[EA Activation] Server response did not contain credentials.");
      return false;
     }

   g_webUserId = userId;
   g_eaToken = token;
   if(!SaveCredentials(activationCode))
      Print("[EA Activation] Warning: credentials could not be saved locally.");
   Print("[EA Activation] Account binding completed for user_id=", g_webUserId);
   return true;
  }

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_pythonServer = InpServerUrl;
   while(StringLen(g_pythonServer) > 0 &&
         StringSubstr(g_pythonServer, StringLen(g_pythonServer) - 1, 1) == "/")
      g_pythonServer = StringSubstr(g_pythonServer, 0, StringLen(g_pythonServer) - 1);

   g_activationCode = GetActivationCodeFromProgramName();
   bool credentialsReady = false;
   if(StringLen(g_activationCode) > 0)
     {
      credentialsReady = LoadCredentials(g_activationCode);
      if(!credentialsReady)
         credentialsReady = ActivateEA(g_activationCode);
     }
   else if(InpWebUserId > 0 && StringLen(InpEaToken) > 0)
     {
      g_webUserId = InpWebUserId;
      g_eaToken = InpEaToken;
      credentialsReady = true;
     }
   else
     {
      credentialsReady = LoadCredentials("");
     }

   if(!credentialsReady)
     {
      Print(
         "EA account binding is missing. Download a personalized EX5 file ",
         "or set InpWebUserId and InpEaToken manually."
      );
      return(INIT_PARAMETERS_INCORRECT);
     }

//--- 初始化交易类
   trade.SetExpertMagicNumber(123456);

//--- 初始化时间
   g_lastStatisticTime = TimeCurrent();
   g_lastPythonRequestTime = GetTickCount();
   g_lastKlinePushTime = TimeCurrent();
   g_lastTradeHistoryReportTime = 0;  // 初始化交易历史上报时间

//--- 初始化随机数种子
   MathSrand((uint)TimeCurrent());

//--- 设置定时器，每1秒触发一次
   EventSetTimer(1);

//--- 打印初始化信息
   Print("Expert initialized successfully");
   Print("Python server: ", g_pythonServer);
   Print("Risk limit: ", g_riskLimitPercent, "%");

//--- 启动时推送历史K线数据
   Print("Pushing historical K-line data...");
   PushAllKlineData(true);  // is_full = true

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
   g_freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   g_margin = AccountInfoDouble(ACCOUNT_MARGIN);
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
      string posComment = PositionGetString(POSITION_COMMENT);
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
      summary += "\"openTime\":" + IntegerToString(PositionGetInteger(POSITION_TIME)) + ",";
      summary += "\"type\":\"" + (posType == POSITION_TYPE_BUY ? "BUY" : "SELL") + "\",";
      summary += "\"profit\":" + DoubleToString(posProfit, 2) + ",";
      summary += "\"comment\":\"" + EscapeJsonString(posComment) + "\",";
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
   string headers = BuildAuthenticatedHeaders();
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
   // 信用账户可能余额很低但权益正常，取较大值避免风控阈值被压到开仓点差以下。
   double riskBase = MathMax(g_accountBalance, g_accountEquity);
   if(riskBase <= 0)
      return;

   double riskThreshold = riskBase * (g_riskLimitPercent / 100.0);
   
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
   string headers = BuildAuthenticatedHeaders();
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
      Print("Make sure 'http://182.92.119.121' is added to the WebRequest allowed list");
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

   int updatesPos = StringFind(jsonData, "\"position_updates\":");
   if(updatesPos != -1)
     {
      int updatesStart = StringFind(jsonData, "[", updatesPos);
      int updatesEnd = StringFind(jsonData, "]", updatesStart);
      if(updatesStart != -1 && updatesEnd != -1 && updatesEnd > updatesStart + 1)
        {
         string updatesJson = StringSubstr(jsonData, updatesStart + 1, updatesEnd - updatesStart - 1);
         int cursor = 0;
         while(cursor < StringLen(updatesJson))
           {
            int objectStart = StringFind(updatesJson, "{", cursor);
            int objectEnd = StringFind(updatesJson, "}", objectStart);
            if(objectStart == -1 || objectEnd == -1) break;
            string updateJson = StringSubstr(updatesJson, objectStart, objectEnd - objectStart + 1);
            long ticket = (long)ExtractJsonDouble(updateJson, "ticket");
            double sl = ExtractJsonDouble(updateJson, "sl");
            double tp = ExtractJsonDouble(updateJson, "tp");
            if(ticket > 0 && PositionSelectByTicket(ticket))
              {
               if(trade.PositionModify(ticket, sl, tp))
                  Print("[持仓更新成功] Ticket: ", ticket, " SL: ", sl, " TP: ", tp);
               else
                  Print("[持仓更新失败] Ticket: ", ticket, " Retcode: ", trade.ResultRetcodeDescription());
              }
            cursor = objectEnd + 1;
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
   string instructionId = ExtractJsonString(tradeJson, "instruction_id");
   string orderId = ExtractJsonString(tradeJson, "order_id");
   string symbol = ExtractJsonString(tradeJson, "symbol");
   string action = ExtractJsonString(tradeJson, "action");
   double requestedPrice = ExtractJsonDouble(tradeJson, "price");
   double volume = ExtractJsonDouble(tradeJson, "mount");
   double sl = ExtractJsonDouble(tradeJson, "sl");
   double tp = ExtractJsonDouble(tradeJson, "tp");
   string exitMode = ExtractJsonString(tradeJson, "exit_mode");
   string description = ExtractJsonString(tradeJson, "description");

   Print("[EA] 收到交易指令: symbol=", symbol, " action=", action, " volume=", volume, " sl=", sl, " tp=", tp, " description=", description);

   if(symbol == "" || action == "" || volume <= 0)
     {
      Print("[EA] 交易参数无效，跳过");
      return;
     }

   string normalizedSymbol = symbol;
   string normalizedCurrentSymbol = _Symbol;
   StringToUpper(normalizedSymbol);
   StringToUpper(normalizedCurrentSymbol);
   if(normalizedSymbol != normalizedCurrentSymbol)
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
   ExecuteTrade(orderType, volume, sl, tp, description, exitMode,
                instructionId, orderId, requestedPrice, action);
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
   if(endPos == -1) endPos = StringLen(json);
   
   string valueStr = StringSubstr(json, startPos, endPos - startPos);
   return StringToDouble(valueStr);
  }

//+------------------------------------------------------------------+
//| 执行交易                                                          |
//+------------------------------------------------------------------+
void ExecuteTrade(ENUM_ORDER_TYPE orderType, double volume, double sl, double tp,
                  string description, string exitMode, string instructionId, string orderId,
                  double requestedPrice, string action)
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
   if(tp <= 0 && exitMode == "fixed_rr")
     {
      if(orderType == ORDER_TYPE_BUY)
         tp = price * (1.0 + 0.001);
      else
         tp = price * (1.0 - 0.001);
     }
   else if(exitMode != "fixed_rr")
     {
      // Dynamic exits are managed by the server; MT5 keeps the initial SL only.
      tp = 0;
     }

   // 标准化手数
   double minVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepVolume = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   volume = MathMax(minVolume, MathMin(volume, maxVolume));
   volume = MathRound(volume / stepVolume) * stepVolume;

   // 执行订单
   bool succeeded = false;
   if(orderType == ORDER_TYPE_BUY)
     {
      succeeded = trade.Buy(volume, _Symbol, 0, sl, tp, description);
      if(succeeded)
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
      succeeded = trade.Sell(volume, _Symbol, 0, sl, tp, description);
      if(succeeded)
        {
         Print("Sell order executed: Volume=", volume, " SL=", sl, " TP=", tp, " Description=", description);
         RecordTrade("SELL", _Symbol, volume, sl, tp, trade.ResultPrice());
        }
      else
        {
         Print("Sell order failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
        }
     }

   SendTradeExecutionReport(
      instructionId,
      orderId,
      _Symbol,
      action,
      succeeded,
      requestedPrice,
      trade.ResultPrice(),
      volume,
      succeeded ? trade.ResultVolume() : 0,
      (long)trade.ResultOrder(),
      (long)trade.ResultDeal(),
      (long)trade.ResultRetcode(),
      succeeded ? "" : trade.ResultRetcodeDescription()
   );
  }

//+------------------------------------------------------------------+
//| 即时回报服务端交易指令执行结果                                   |
//+------------------------------------------------------------------+
void SendTradeExecutionReport(
   string instructionId, string orderId, string symbol, string action,
   bool success, double requestedPrice, double executedPrice,
   double requestedVolume, double executedVolume, long mt5Order,
   long mt5Deal, long retcode, string errorMessage)
  {
   if(instructionId == "")
      return;

   string jsonBody = "{";
   jsonBody += "\"instruction_id\":\"" + EscapeJsonString(instructionId) + "\",";
   jsonBody += "\"order_id\":\"" + EscapeJsonString(orderId) + "\",";
   jsonBody += "\"symbol\":\"" + EscapeJsonString(symbol) + "\",";
   jsonBody += "\"action\":\"" + EscapeJsonString(action) + "\",";
   jsonBody += "\"success\":" + (success ? "true" : "false") + ",";
   jsonBody += "\"requested_price\":" + DoubleToString(requestedPrice, _Digits) + ",";
   jsonBody += "\"executed_price\":" + DoubleToString(executedPrice, _Digits) + ",";
   jsonBody += "\"requested_volume\":" + DoubleToString(requestedVolume, 2) + ",";
   jsonBody += "\"executed_volume\":" + DoubleToString(executedVolume, 2) + ",";
   jsonBody += "\"mt5_order\":" + IntegerToString(mt5Order) + ",";
   jsonBody += "\"mt5_deal\":" + IntegerToString(mt5Deal) + ",";
   jsonBody += "\"retcode\":" + IntegerToString(retcode) + ",";
   jsonBody += "\"error_message\":\"" + EscapeJsonString(errorMessage) + "\"";
   jsonBody += "}";

   uchar postData[];
   uchar responseData[];
   string outheaders = "";
   StringToCharArray(jsonBody, postData, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(postData, ArraySize(postData) - 1);
   int responseCode = WebRequest(
      "POST",
      g_pythonServer + "/ea/trade_execution",
      BuildAuthenticatedHeaders(),
      5000,
      postData,
      responseData,
      outheaders
   );
   if(responseCode != 200)
      Print("[EA] 交易执行回报失败: HTTP ", responseCode);
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
   statisticJson += "\"freeMargin\":" + DoubleToString(g_freeMargin, 2) + ",";
   statisticJson += "\"margin\":" + DoubleToString(g_margin, 2) + ",";
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
   string headers = BuildAuthenticatedHeaders();
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

   responseCode = WebRequest(
      "POST",
      g_pythonServer + "/send_statistics",
      headers,
      5000,         // timeout (5秒)
      postData,
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
//--- 历史任务不依赖实时Tick，休市期间也可以继续下载
   CheckHistoricalDataTask();
   if(g_historyTaskActive)
      return;  // 采集期间优先处理历史分片

//--- 检查最后一次Tick时间，如果超过10秒无Tick则跳过（可能休市）
   datetime now = TimeCurrent();
   if(g_lastTickTime == 0 || (now - g_lastTickTime) > 10)
     {
      // 无Tick超过10秒，跳过定时任务
      return;
     }

//--- 更新统计数据
   UpdateStatistics();

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
   string headers = BuildAuthenticatedHeaders();
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
      10000,  // 10秒超时
      postData,
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
//| 检查并处理历史回测数据任务                                       |
//+------------------------------------------------------------------+
void CheckHistoricalDataTask()
  {
   uint currentTime = GetTickCount();
   if((currentTime - g_lastHistoryTaskPollTime) < g_historyTaskPollInterval)
      return;
   g_lastHistoryTaskPollTime = currentTime;

   string headers = BuildAuthenticatedHeaders();
   uchar emptyData[];
   uchar responseData[];
   string responseHeaders = "";
   string url = g_pythonServer
                + "/ea/backtest-data/tasks/next?symbol="
                + URLEncode(_Symbol);
   int responseCode = WebRequest(
      "GET", url, headers, 5000, emptyData, responseData, responseHeaders
   );
   if(responseCode != 200)
     {
      if(responseCode != -1)
         Print("[历史数据] 获取任务失败，HTTP=", responseCode);
      return;
     }

   string response = CharArrayToString(
      responseData, 0, WHOLE_ARRAY, CP_UTF8
   );
   string datasetId = ExtractJsonString(response, "dataset_id");
   if(StringLen(datasetId) == 0)
     {
      g_historyTaskActive = false;
      return;
     }
   g_historyTaskActive = true;

   int chunkIndex = (int)ExtractJsonDouble(response, "chunk_index");
   long rangeStart = (long)ExtractJsonDouble(response, "range_start");
   long rangeEnd = (long)ExtractJsonDouble(response, "range_end");
   if(rangeStart <= 0 || rangeEnd < rangeStart)
     {
      Print("[历史数据] 服务端返回了无效时间范围");
      return;
     }

   UploadHistoricalDataChunk(
      datasetId, chunkIndex, (datetime)rangeStart, (datetime)rangeEnd
   );
  }

//+------------------------------------------------------------------+
//| 从MT5读取并上传一个历史M1分片                                    |
//+------------------------------------------------------------------+
bool UploadHistoricalDataChunk(
   string datasetId,
   int chunkIndex,
   datetime rangeStart,
   datetime rangeEnd
)
  {
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   int copied = CopyRates(
      _Symbol, PERIOD_M1, rangeStart, rangeEnd, rates
   );
   if(copied < 0)
     {
      int copyError = GetLastError();
      bool isSameChunk = (
         g_historyRetryDatasetId == datasetId
         && g_historyRetryChunkIndex == chunkIndex
      );
      if(!isSameChunk)
        {
         g_historyRetryDatasetId = datasetId;
         g_historyRetryChunkIndex = chunkIndex;
         g_historyNotFoundRetryCount = 0;
        }

      // 4401 表示该时间片没有历史数据，常见于周末、节假日或券商历史起点之前。
      // 有限重试后上传空分片，避免整个数据集永久卡在同一时间段。
      if(copyError == 4401)
         g_historyNotFoundRetryCount++;
      if(copyError != 4401
         || g_historyNotFoundRetryCount < g_historyNotFoundRetryLimit)
        {
         Print(
            "[历史数据] CopyRates暂未就绪，将稍后重试。error=",
            copyError, ", retry=", g_historyNotFoundRetryCount,
            ", from=", TimeToString(rangeStart),
            ", to=", TimeToString(rangeEnd)
         );
         return false;
        }

      Print(
         "[历史数据] 该时间片无历史数据，按空分片继续。error=",
         copyError, ", from=", TimeToString(rangeStart),
         ", to=", TimeToString(rangeEnd)
      );
      copied = 0;
     }

   string json = "{";
   json += "\"chunk_index\":" + IntegerToString(chunkIndex) + ",";
   json += "\"range_start\":" + IntegerToString((long)rangeStart) + ",";
   json += "\"range_end\":" + IntegerToString((long)rangeEnd) + ",";
   json += "\"broker_server\":\""
           + EscapeJsonString(AccountInfoString(ACCOUNT_SERVER)) + "\",";
   json += "\"ea_version\":\"2.04\",";
   json += "\"bars\":[";

   for(int i = 0; i < copied; i++)
     {
      if(i > 0) json += ",";
      json += "{";
      json += "\"time\":" + IntegerToString((long)rates[i].time) + ",";
      json += "\"open\":" + DoubleToString(rates[i].open, _Digits) + ",";
      json += "\"high\":" + DoubleToString(rates[i].high, _Digits) + ",";
      json += "\"low\":" + DoubleToString(rates[i].low, _Digits) + ",";
      json += "\"close\":" + DoubleToString(rates[i].close, _Digits) + ",";
      json += "\"tick_volume\":"
              + IntegerToString((long)rates[i].tick_volume) + ",";
      json += "\"real_volume\":"
              + IntegerToString((long)rates[i].real_volume) + ",";
      json += "\"spread\":" + IntegerToString((long)rates[i].spread);
      json += "}";
     }
   json += "]}";

   uchar postData[];
   uchar responseData[];
   StringToCharArray(json, postData, 0, WHOLE_ARRAY, CP_UTF8);
   if(ArraySize(postData) > 0)
      ArrayResize(postData, ArraySize(postData) - 1);

   string responseHeaders = "";
   string url = g_pythonServer
                + "/ea/backtest-data/tasks/" + datasetId + "/chunks";
   int responseCode = WebRequest(
      "POST",
      url,
      BuildAuthenticatedHeaders(),
      15000,
      postData,
      responseData,
      responseHeaders
   );
   if(responseCode != 200)
     {
      string errorText = CharArrayToString(
         responseData, 0, WHOLE_ARRAY, CP_UTF8
      );
      Print(
         "[历史数据] 分片上传失败，HTTP=", responseCode,
         ", dataset=", datasetId, ", chunk=", chunkIndex,
         ", response=", errorText
      );
      return false;
     }

   string response = CharArrayToString(
      responseData, 0, WHOLE_ARRAY, CP_UTF8
   );
   double progress = ExtractJsonDouble(response, "progress");
   string datasetStatus = ExtractJsonString(response, "dataset_status");
   Print(
      "[历史数据] 分片上传成功，dataset=", datasetId,
      ", chunk=", chunkIndex, ", bars=", copied,
      ", progress=", DoubleToString(progress, 1), "%",
      ", status=", datasetStatus
   );
   g_historyRetryDatasetId = "";
   g_historyRetryChunkIndex = -1;
   g_historyNotFoundRetryCount = 0;
   return true;
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
   string headers = BuildAuthenticatedHeaders();
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
   responseCode = WebRequest("POST", url, headers, 15000, postData, responseData, outheaders);

   if(responseCode == 200)
     {
      Print("[交易历史] 上报成功，共 ", validDeals, " 条成交记录");
     }
   else
     {
      Print("[交易历史] 上报失败，Response code: ", responseCode);
     }
  }
