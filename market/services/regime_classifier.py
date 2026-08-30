"""主周期行情状态判定：结构、位移、回撤、斜率与箱体证据综合。"""
from typing import Dict, List

def _number(item, *names):
    for name in names:
        value = getattr(item, name, None)
        if value is None and isinstance(item, dict): value = item.get(name)
        try:
            if value is not None: return float(value)
        except (TypeError, ValueError): pass
    return None

def _rows(klines: List) -> List[Dict[str, float]]:
    out=[]
    for item in list(klines or []):
        close=_number(item,"close","close_price")
        if close is None or close<=0: continue
        high=_number(item,"high","high_price") or close; low=_number(item,"low","low_price") or close
        out.append({"close":close,"high":max(high,low,close),"low":min(high,low,close)})
    return out

def _atr(rows, period=14):
    if not rows: return 0.0
    values=[]; prev=None
    for row in rows:
        values.append(max(row["high"]-row["low"], abs(row["high"]-prev) if prev is not None else 0, abs(row["low"]-prev) if prev is not None else 0)); prev=row["close"]
    return max(sum(values[-min(period,len(values)):])/min(period,len(values)),1e-12)

def _pivots(rows, leg=2, min_reversal_atr=.25):
    if len(rows)<leg*2+1: return []
    atr=_atr(rows); raw=[]
    for i in range(leg,len(rows)-leg):
        left=rows[i-leg:i]; right=rows[i+1:i+leg+1]; h=rows[i]["high"]; l=rows[i]["low"]
        if h>=max(x["high"] for x in left+right): raw.append((i,"high",h))
        if l<=min(x["low"] for x in left+right): raw.append((i,"low",l))
    result=[]
    for item in sorted(raw):
        if result and result[-1][1]==item[1]:
            better=item[2]>=result[-1][2] if item[1]=="high" else item[2]<=result[-1][2]
            if better: result[-1]=item
        elif not result or abs(item[2]-result[-1][2])>=atr*min_reversal_atr: result.append(item)
    return result

def _slope(values):
    n=len(values)
    if n<2:return 0.0
    mx=(n-1)/2; my=sum(values)/n; den=sum((i-mx)**2 for i in range(n))
    return sum((i-mx)*(v-my) for i,v in enumerate(values))/den if den else 0.0

def _evidence(rows):
    closes=[x["close"] for x in rows]; highs=[x["high"] for x in rows]; lows=[x["low"] for x in rows]; n=len(rows); atr=_atr(rows); first,last=closes[0],closes[-1]
    traveled=sum(abs(closes[i]-closes[i-1]) for i in range(1,n)); efficiency=abs(last-first)/traveled if traveled else 0
    move=abs(last-first)/atr; slope=_slope(closes); slope_norm=slope*max(1,n-1)/atr
    pivots=_pivots(rows); hh=hl=lh=ll=0
    for prev,cur in zip(pivots,pivots[1:]):
        if prev[1]==cur[1]=="high": hh+=cur[2]>prev[2]; lh+=cur[2]<prev[2]
        elif prev[1]==cur[1]=="low": hl+=cur[2]>prev[2]; ll+=cur[2]<prev[2]
    up_pairs=hh+hl; down_pairs=lh+ll; total=max(1,up_pairs+down_pairs); up_ratio=up_pairs/total; down_ratio=down_pairs/total
    max_retrace=0.0
    if last>=first:
        extreme=closes[0]
        for v in closes[1:]: extreme=max(extreme,v); max_retrace=max(max_retrace,(extreme-v)/atr)
    else:
        extreme=closes[0]
        for v in closes[1:]: extreme=min(extreme,v); max_retrace=max(max_retrace,(v-extreme)/atr)
    upper=sorted(highs)[max(0,int(n*.90)-1)]; lower=sorted(lows)[min(n-1,int(n*.10))]; tol=max(atr*.6,(upper-lower)*.02,1e-12)
    inside=sum(lower-tol<=v<=upper+tol for v in closes)/n; width=(upper-lower)/atr
    up=int(last>first)+(2 if up_ratio>=.62 else 1 if up_ratio>=.55 else 0)+int(slope_norm>=.35)+int(move>=1)+int(efficiency>=.25)
    down=int(last<first)+(2 if down_ratio>=.62 else 1 if down_ratio>=.55 else 0)+int(slope_norm<=-.35)+int(move>=1)+int(efficiency>=.25)
    rng=int(inside>=.72)+int(1.5<=width<=8)+int(efficiency<.40)
    if max_retrace>4: up-=1; down-=1
    if up>=4 and up>down+1 and rng<2: state="up"
    elif down>=4 and down>up+1 and rng<2: state="down"
    elif rng>=2: state="sideways"
    else: state="transition"
    confidence=int(round(min(1,max(up,down,rng)/5)*100))
    return {"state":state,"change_pct":round((last-first)/first*100,4),"atr":round(atr,8),"normalized_move":round(move,4),"efficiency_ratio":round(efficiency,4),"slope":round(slope,8),"slope_normalized":round(slope_norm,4),"higher_highs":int(hh),"higher_lows":int(hl),"lower_highs":int(lh),"lower_lows":int(ll),"up_structure_ratio":round(up_ratio,4),"down_structure_ratio":round(down_ratio,4),"max_retrace_atr":round(max_retrace,4),"range_inside_ratio":round(inside,4),"range_width_atr":round(width,4),"up_score":up,"down_score":down,"range_score":rng,"confidence":confidence}

def analyze_main_period_regime(klines: List, windows=(20,40,70)) -> Dict:
    rows=_rows(klines)
    if len(rows)<10:return {"regime":"unknown","confidence":0,"windows":[],"reason":"K线数据不足"}
    evidences=[{"bars":w,**_evidence(rows[-w:])} for w in windows if len(rows)>=w] or [{"bars":len(rows),**_evidence(rows)}]
    weights={20:.45,40:.35,70:.20}; scores={s:0.0 for s in ("up","down","sideways","transition")}
    for e in evidences: scores[e["state"]]+=weights.get(e["bars"],1/len(evidences))*(.5+e["confidence"]/200)
    ranked=sorted(scores,key=scores.get,reverse=True); regime=ranked[0]
    if regime=="transition" or scores[regime]-scores[ranked[1]]<.12: regime="sideways" if scores["sideways"]>=scores["transition"] else "transition"
    latest=evidences[0]; confidence=int(round(scores[ranked[0]]/(sum(scores.values()) or 1)*100))
    reason=f"窗口结构={latest['higher_highs']}HH/{latest['higher_lows']}HL、{latest['lower_highs']}LH/{latest['lower_lows']}LL；位移{latest['normalized_move']:.2f}ATR，回撤{latest['max_retrace_atr']:.2f}ATR，斜率{latest['slope_normalized']:.2f}，箱体内部{latest['range_inside_ratio']:.0%}"
    return {"regime":regime,"confidence":confidence,"windows":evidences,"scores":{k:round(v,4) for k,v in scores.items()},"reason":reason}

def classify_main_period_regime(klines: List, lookback: int=20) -> str:
    regime=analyze_main_period_regime(klines).get("regime")
    return regime if regime in {"up","down","sideways"} else "sideways"
