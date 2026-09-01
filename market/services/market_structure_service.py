"""Deterministic market-structure engine: pivots, HH/HL/LH/LL and BOS/CHoCH."""
from datetime import datetime
from typing import Dict, List
from repositories.runtime import RuntimeStateRepository

DEFAULT_CONFIG = {"pivot_legs": 3, "reversal_atr": 0.5, "break_confirm_bars": 2, "min_segment_bars": 12, "range_touch_tolerance": 0.003}

def _price(row, key, fallback=0.0):
    return float(row.get(key) or row.get(f"{key}_price") or fallback or 0)

def analyze(symbol: str, period: str, rows: List[Dict], config: Dict = None) -> Dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}; rows = list(rows or []); k = max(2, int(cfg["pivot_legs"]))
    swings=[]
    for i in range(k, len(rows)-k):
        h=_price(rows[i],"high",_price(rows[i],"close")); l=_price(rows[i],"low",_price(rows[i],"close"))
        hs=[_price(x,"high",_price(x,"close")) for x in rows[i-k:i+k+1] if x is not rows[i]]; ls=[_price(x,"low",_price(x,"close")) for x in rows[i-k:i+k+1] if x is not rows[i]]
        if h>max(hs): swings.append({"index":i,"kind":"high","price":h,"label":None})
        if l<min(ls): swings.append({"index":i,"kind":"low","price":l,"label":None})
    prev={"high":None,"low":None}
    for s in swings:
        p=prev[s["kind"]]; s["label"] = ("HH" if s["price"]>p else "LH") if s["kind"]=="high" and p is not None else (("LL" if s["price"]<p else "HL") if s["kind"]=="low" and p is not None else None); prev[s["kind"]]=s["price"]
    events=[]; state="undetermined"; broken={"high":False,"low":False}
    for i,row in enumerate(rows):
        close=_price(row,"close"); highs=[s for s in swings if s["kind"]=="high" and s["index"]<i]; lows=[s for s in swings if s["kind"]=="low" and s["index"]<i]
        rh=highs[-1] if highs else None; rl=lows[-1] if lows else None
        if rh and close>rh["price"] and not broken["high"]:
            event="choch" if state=="bearish" else "bos"; state="bullish"; broken["high"]=True; broken["low"]=False; events.append({"index":i,"type":event,"direction":"up","level":rh["price"]})
        elif rl and close<rl["price"] and not broken["low"]:
            event="choch" if state=="bullish" else "bos"; state="bearish"; broken["low"]=True; broken["high"]=False; events.append({"index":i,"type":event,"direction":"down","level":rl["price"]})
    segments=[]
    points=[0]+[e["index"] for e in events]+[max(0,len(rows)-1)]
    for a,b in zip(points,points[1:]):
        if b<a: continue
        part=rows[a:b+1]; seg_state=state
        if events and a==0: seg_state="undetermined"
        elif events:
            e=next((x for x in events if x["index"]==a),None); seg_state="bullish" if e and e["direction"]=="up" else ("bearish" if e else seg_state)
        segments.append({"start_index":a,"end_index":b,"bars":len(part),"type":{"bullish":"up","bearish":"down"}.get(seg_state,"transition"),"event":next((x for x in events if x["index"]==a),None)})
    return {"symbol":symbol,"period":period,"config":cfg,"swings":swings,"events":events,"segments":segments,"current_state":state,"analyzed_at":datetime.utcnow().isoformat()}

def save_result(user_id:int, account_id:int, result:Dict):
    RuntimeStateRepository(user_id, account_id).upsert_entity("market_structure", f"{result['symbol']}::{result['period']}", result, symbol=result["symbol"], status="active")
