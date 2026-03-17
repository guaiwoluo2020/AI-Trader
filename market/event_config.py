#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场事件配置
定义关注的财经数据、关键人物和事件
"""

# 关注的交易品种
WATCH_SYMBOLS = ["GOLD", "OIL", "BTC", "SPX", "USDJPY"]

# 定期财经数据（财经日历）
ECONOMIC_DATA = [
    # ============ 就业类 ============
    {
        "name": "非农就业人数",
        "name_en": "Non-Farm Payrolls",
        "country": "US",
        "importance": 3,  # 3=高影响, 2=中, 1=低
        "symbols": ["GOLD", "SPX", "USDJPY"],
        "unit": "万人"
    },
    {
        "name": "失业率",
        "name_en": "Unemployment Rate",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX", "USDJPY"],
        "unit": "%"
    },
    {
        "name": "ADP就业人数",
        "name_en": "ADP Nonfarm Employment Change",
        "country": "US",
        "importance": 2,
        "symbols": ["GOLD", "SPX"],
        "unit": "万人"
    },
    {
        "name": "初请失业金人数",
        "name_en": "Initial Jobless Claims",
        "country": "US",
        "importance": 2,
        "symbols": ["GOLD", "SPX"],
        "unit": "万人"
    },

    # ============ 通胀类 ============
    {
        "name": "CPI年率",
        "name_en": "CPI YoY",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX", "USDJPY", "BTC"],
        "unit": "%"
    },
    {
        "name": "核心CPI年率",
        "name_en": "Core CPI YoY",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX", "USDJPY"],
        "unit": "%"
    },
    {
        "name": "PPI年率",
        "name_en": "PPI YoY",
        "country": "US",
        "importance": 2,
        "symbols": ["GOLD", "SPX"],
        "unit": "%"
    },
    {
        "name": "PCE物价指数年率",
        "name_en": "PCE Price Index YoY",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX"],
        "unit": "%"
    },
    {
        "name": "核心PCE年率",
        "name_en": "Core PCE YoY",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX"],
        "unit": "%"
    },

    # ============ 利率类 ============
    {
        "name": "美联储利率决议",
        "name_en": "Federal Funds Rate",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX", "USDJPY", "BTC"],
        "unit": "%"
    },
    {
        "name": "日本央行利率决议",
        "name_en": "BoJ Interest Rate",
        "country": "JP",
        "importance": 3,
        "symbols": ["USDJPY"],
        "unit": "%"
    },
    {
        "name": "欧洲央行利率决议",
        "name_en": "ECB Interest Rate",
        "country": "EU",
        "importance": 2,
        "symbols": ["GOLD"],
        "unit": "%"
    },

    # ============ 经济类 ============
    {
        "name": "GDP年率",
        "name_en": "GDP YoY",
        "country": "US",
        "importance": 3,
        "symbols": ["GOLD", "SPX"],
        "unit": "%"
    },
    {
        "name": "零售销售月率",
        "name_en": "Retail Sales MoM",
        "country": "US",
        "importance": 2,
        "symbols": ["SPX"],
        "unit": "%"
    },
    {
        "name": "ISM制造业PMI",
        "name_en": "ISM Manufacturing PMI",
        "country": "US",
        "importance": 2,
        "symbols": ["SPX"],
        "unit": ""
    },
    {
        "name": "ISM服务业PMI",
        "name_en": "ISM Services PMI",
        "country": "US",
        "importance": 2,
        "symbols": ["SPX"],
        "unit": ""
    },

    # ============ 原油类 ============
    {
        "name": "EIA原油库存",
        "name_en": "EIA Crude Oil Inventories",
        "country": "US",
        "importance": 2,
        "symbols": ["OIL"],
        "unit": "万桶"
    },
    {
        "name": "API原油库存",
        "name_en": "API Crude Oil Stock",
        "country": "US",
        "importance": 1,
        "symbols": ["OIL"],
        "unit": "万桶"
    },

    # ============ 日本数据 ============
    {
        "name": "日本CPI年率",
        "name_en": "Japan CPI YoY",
        "country": "JP",
        "importance": 2,
        "symbols": ["USDJPY"],
        "unit": "%"
    },
    {
        "name": "日本GDP年率",
        "name_en": "Japan GDP YoY",
        "country": "JP",
        "importance": 2,
        "symbols": ["USDJPY"],
        "unit": "%"
    },
]

# 关键人物讲话配置
KEY_SPEAKERS = [
    {
        "name": "特朗普",
        "name_en": "Trump",
        "title": "美国总统",
        "title_en": "US President",
        "keywords": ["特朗普", "Trump", "总统"],
        "importance": 3,
        "watch_topics": ["关税", "贸易", "制裁", "中国", "利率", "美元", "北约", "俄乌", "战争", "减税"],
        "impact_symbols": ["GOLD", "SPX", "USDJPY", "BTC", "OIL"],
        "default_impact": {
            "GOLD": {"关税/制裁": "利好", "战争/冲突": "利好", "减税": "中性"},
            "SPX": {"关税/制裁": "利空", "减税": "利好"},
            "USDJPY": {"关税": "不确定", "利率": "利好"},
            "OIL": {"制裁": "利好", "战争": "利好"},
        }
    },
    {
        "name": "鲍威尔",
        "name_en": "Powell",
        "title": "美联储主席",
        "title_en": "Fed Chair",
        "keywords": ["鲍威尔", "Powell", "美联储主席", "Fed Chair"],
        "importance": 3,
        "watch_topics": ["利率", "通胀", "就业", "降息", "加息", "货币政策", "缩表"],
        "impact_symbols": ["GOLD", "SPX", "USDJPY", "BTC"],
        "default_impact": {
            "GOLD": {"降息": "利好", "加息": "利空", "鸽派": "利好", "鹰派": "利空"},
            "SPX": {"降息": "利好", "加息": "利空", "鸽派": "利好", "鹰派": "利空"},
            "USDJPY": {"降息": "利空", "加息": "利好"},
            "BTC": {"降息": "利好", "加息": "利空"},
        }
    },
    {
        "name": "贝森特",
        "name_en": "Bessent",
        "title": "美国财长",
        "title_en": "US Treasury Secretary",
        "keywords": ["贝森特", "Bessent", "财长", "Treasury Secretary", "财政部"],
        "importance": 2,
        "watch_topics": ["债务", "预算", "制裁", "汇率", "国债"],
        "impact_symbols": ["GOLD", "SPX", "USDJPY"],
        "default_impact": {
            "GOLD": {"债务担忧": "利好", "制裁": "利好"},
            "SPX": {"债务担忧": "利空"},
        }
    },
    {
        "name": "植田和男",
        "name_en": "Ueda",
        "title": "日本央行行长",
        "title_en": "BoJ Governor",
        "keywords": ["植田", "Ueda", "日本央行", "日银", "BoJ"],
        "importance": 2,
        "watch_topics": ["利率", "YCC", "干预", "日元", "宽松"],
        "impact_symbols": ["USDJPY"],
        "default_impact": {
            "USDJPY": {"加息": "利空", "干预": "利空", "宽松": "利好"},
        }
    },
    {
        "name": "拉加德",
        "name_en": "Lagarde",
        "title": "欧洲央行行长",
        "title_en": "ECB President",
        "keywords": ["拉加德", "Lagarde", "欧洲央行", "ECB"],
        "importance": 2,
        "watch_topics": ["利率", "通胀", "欧元"],
        "impact_symbols": ["GOLD"],
        "default_impact": {
            "GOLD": {"降息": "利好", "加息": "利空"},
        }
    },
]

# 关键事件配置
KEY_EVENTS = [
    {
        "name": "FOMC会议",
        "name_en": "FOMC Meeting",
        "type": "scheduled",
        "importance": 3,
        "symbols": ["GOLD", "SPX", "USDJPY", "BTC"],
        "watch_keywords": ["利率决议", "点阵图", "经济预测", "发布会", "FOMC"],
        "description": "美联储联邦公开市场委员会会议"
    },
    {
        "name": "OPEC会议",
        "name_en": "OPEC Meeting",
        "type": "scheduled",
        "importance": 3,
        "symbols": ["OIL"],
        "watch_keywords": ["减产", "增产", "产量配额", "OPEC", "OPEC+"],
        "description": "石油输出国组织会议"
    },
    {
        "name": "G7/G20峰会",
        "name_en": "G7/G20 Summit",
        "type": "scheduled",
        "importance": 2,
        "symbols": ["GOLD", "OIL", "SPX"],
        "watch_keywords": ["G7", "G20", "峰会", "制裁", "贸易"],
        "description": "七国集团/二十国集团峰会"
    },
    {
        "name": "地缘冲突",
        "name_en": "Geopolitical Conflict",
        "type": "breaking",
        "importance": 3,
        "symbols": ["GOLD", "OIL"],
        "watch_keywords": ["战争", "冲突", "制裁", "导弹", "核", "恐怖袭击", "入侵", "军事行动"],
        "description": "地缘政治突发事件"
    },
    {
        "name": "加密监管",
        "name_en": "Crypto Regulation",
        "type": "breaking",
        "importance": 2,
        "symbols": ["BTC"],
        "watch_keywords": ["SEC", "ETF", "比特币", "监管", "禁令", "审批"],
        "description": "加密货币监管新闻"
    },
    {
        "name": "贸易战",
        "name_en": "Trade War",
        "type": "breaking",
        "importance": 3,
        "symbols": ["GOLD", "SPX", "OIL"],
        "watch_keywords": ["关税", "贸易战", "制裁", "禁运", "贸易谈判"],
        "description": "贸易战相关新闻"
    },
]

# 数据影响规则（实际值 vs 预期值）
DATA_IMPACT_RULES = {
    "GOLD": {
        "非农就业人数": {
            "better": "利空",  # 好于预期 -> 利空黄金
            "worse": "利好",   # 差于预期 -> 利好黄金
            "reason_better": "就业强劲，美元走强，黄金承压",
            "reason_worse": "就业疲软，美元走弱，黄金上涨"
        },
        "失业率": {
            "better": "利好",  # 失业率下降
            "worse": "利空",   # 失业率上升
            "reason_better": "失业率下降，经济向好，但可能提前加息",
            "reason_worse": "失业率上升，经济疲软，可能降息"
        },
        "CPI年率": {
            "better": "利空",  # 高于预期 -> 利空
            "worse": "利好",
            "reason_better": "通胀超预期，加息预期升温",
            "reason_worse": "通胀低于预期，降息预期升温"
        },
        "美联储利率决议": {
            "hike": "利空",    # 加息
            "cut": "利好",     # 降息
            "hold": "中性",
            "reason_hike": "加息推高美元，黄金承压",
            "reason_cut": "降息削弱美元，黄金上涨"
        },
        "EIA原油库存": {
            "higher": "利空",
            "lower": "利好",
            "reason_higher": "库存增加，需求疲软",
            "reason_lower": "库存下降，需求旺盛"
        },
    },
    "SPX": {
        "非农就业人数": {
            "better": "利好",
            "worse": "利空",
            "reason_better": "就业强劲，经济向好",
            "reason_worse": "就业疲软，经济担忧"
        },
        "CPI年率": {
            "better": "利空",  # 高通胀利空股市
            "worse": "利好",
            "reason_better": "通胀超预期，加息预期升温",
            "reason_worse": "通胀降温，降息预期升温"
        },
        "美联储利率决议": {
            "hike": "利空",
            "cut": "利好",
            "hold": "中性",
        },
    },
    "USDJPY": {
        "非农就业人数": {
            "better": "利好",  # 好于预期 -> 美元涨 -> USDJPY涨
            "worse": "利空",
        },
        "美联储利率决议": {
            "hike": "利好",
            "cut": "利空",
        },
        "日本央行利率决议": {
            "hike": "利空",  # 日本加息 -> 日元涨 -> USDJPY跌
            "cut": "利好",
        },
    },
    "BTC": {
        "美联储利率决议": {
            "hike": "利空",
            "cut": "利好",
        },
        "CPI年率": {
            "better": "利空",
            "worse": "利好",
        },
    },
    "OIL": {
        "EIA原油库存": {
            "higher": "利空",
            "lower": "利好",
            "reason_higher": "库存增加，供过于求",
            "reason_lower": "库存下降，供不应求"
        },
        "OPEC会议": {
            "cut_production": "利好",  # 减产
            "increase_production": "利空",  # 增产
        },
    },
}

# 获取重要事件名称列表（用于日历过滤）
def get_important_event_names() -> list:
    """获取所有重要事件名称"""
    names = set()
    for event in ECONOMIC_DATA:
        if event['importance'] >= 2:  # 中等及以上重要
            names.add(event['name'])
            names.add(event['name_en'])
    return list(names)

# 获取高影响事件名称列表
def get_high_impact_event_names() -> list:
    """获取高影响事件名称"""
    names = set()
    for event in ECONOMIC_DATA:
        if event['importance'] == 3:  # 高影响
            names.add(event['name'])
            names.add(event['name_en'])
    return list(names)

# 获取事件影响的品种
def get_event_symbols(event_name: str) -> list:
    """获取事件影响的品种列表"""
    for event in ECONOMIC_DATA:
        if event_name in [event['name'], event['name_en']]:
            return event['symbols']
    return WATCH_SYMBOLS  # 默认返回所有品种