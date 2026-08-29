"""Pivot/HHHL/BOS-CHoCH market structure engine (non-repainting)."""
from datetime import datetime
from typing import Dict,List

_CACHE={}

DEFAULT_CONFIG={'pivot_legs':3,'medium_pivot_legs':8,'large_pivot_legs':25,'min_reversal_atr':0.5,'break_confirm_bars':2,'range_touch_tolerance':0.003,'range_min_touches':2,'range_max_atr':6.0,'min_segment_bars':12,'trendline_touch_atr':0.5,'trendline_min_touches':2,'trendline_min_bars':18}
def _v(r,k): return float(r.get(k) or r.get(k+'_price') or 0)
def _atr(rows,n=14):
 t=[];p=0
 for r in rows:
  h,l,c=_v(r,'high'),_v(r,'low'),_v(r,'close');t.append(max(h-l,abs(h-p),abs(l-p)) if p else h-l);p=c
 return sum(t[-n:])/max(1,min(n,len(t)))
def _pivots(rows,k,level,atr,min_atr):
 out=[]
 for i in range(k,len(rows)-k):
  h,l=_v(rows[i],'high'),_v(rows[i],'low');q=rows[i-k:i]+rows[i+1:i+k+1]
  if h>max(_v(x,'high') for x in q):out.append({'index':i,'kind':'high','price':h,'confirmed_at':i+k,'level':level})
  if l<min(_v(x,'low') for x in q):out.append({'index':i,'kind':'low','price':l,'confirmed_at':i+k,'level':level})
 clean=[]
 for s in out:
  if clean and clean[-1]['kind']==s['kind']:
   if (s['kind']=='high' and s['price']>clean[-1]['price']) or (s['kind']=='low' and s['price']<clean[-1]['price']):clean[-1]=s
   continue
  if clean and abs(s['price']-clean[-1]['price'])<atr*min_atr:continue
  clean.append(s)
 prev={'high':None,'low':None}
 for s in clean:
  p=prev[s['kind']];s['label']=None if p is None else ('HH' if s['kind']=='high' and s['price']>p else 'LH' if s['kind']=='high' else 'LL' if s['price']<p else 'HL');prev[s['kind']]=s['price']
 return clean
def _range(rows,sw,atr,c):
 recent=rows[-max(30,min(120,len(rows))):]; offset=len(rows)-len(recent); recent_sw=[s for s in sw if s['index']>=offset]
 highs=[s['price'] for s in recent_sw if s['kind']=='high'];lows=[s['price'] for s in recent_sw if s['kind']=='low']
 if len(highs)<2 or len(lows)<2:return None
 top=sum(highs[-3:])/min(3,len(highs));bottom=sum(lows[-3:])/min(3,len(lows));tol=float(c['range_touch_tolerance'])
 ht=sum(abs(x-top)/max(top,1)<=tol for x in highs);lt=sum(abs(x-bottom)/max(bottom,1)<=tol for x in lows);inside=sum(bottom<=_v(x,'close')<=top for x in recent)/max(1,len(recent))
 active=ht>=int(c['range_min_touches']) and lt>=int(c['range_min_touches']) and inside>=.65 and top-bottom<=atr*float(c['range_max_atr'])
 return {'active':active,'top':top,'bottom':bottom,'high_touches':ht,'low_touches':lt,'inside_ratio':round(inside,3),'width_atr':round((top-bottom)/max(atr,1e-9),2)}
def _trendlines(rows,levels,atr,c):
 out=[];zone=atr*float(c['trendline_touch_atr'])
 for level,pivots in levels.items():
  for kind in ('high','low'):
   pts=[p for p in pivots if p['kind']==kind]
   for p1,p2 in zip(pts[-4:-1],pts[-3:]):
    if p2['index']==p1['index']:continue
    slope=(p2['price']-p1['price'])/(p2['index']-p1['index']);touches=0;broken_at=None;confirm=0
    for i in range(p2['index'],len(rows)):
     line=p1['price']+slope*(i-p1['index']);h,l,cl=_v(rows[i],'high'),_v(rows[i],'low'),_v(rows[i],'close')
     if l-zone<=line<=h+zone:touches+=1
     crossed=cl>line+zone if kind=='high' else cl<line-zone;confirm=confirm+1 if crossed else 0
     if confirm>=int(c['break_confirm_bars']):broken_at=i;break
    score=touches*10+(p2['index']-p1['index'])/10+({'small':1,'medium':4,'large':8}[level])
    span=p2['index']-p1['index']
    # A line with only one touch or a very short anchor span is noise, not a structure line.
    if touches >= int(c.get('trendline_min_touches',2)) and span >= int(c.get('trendline_min_bars',18)):
     out.append({'kind':'resistance' if kind=='high' else 'support','level':level,'start_index':p1['index'],'anchor_index':p2['index'],'end_index':broken_at if broken_at is not None else len(rows)-1,'start_price':p1['price'],'anchor_price':p2['price'],'slope':slope,'touches':touches,'score':round(score,2),'broken_at':broken_at})
 return sorted(out,key=lambda x:x['score'],reverse=True)[:4]
def analyze(symbol:str,period:str,rows:List[Dict],config:Dict=None)->Dict:
 c={**DEFAULT_CONFIG,**(config or {})};a=_atr(rows);m=float(c['min_reversal_atr']);levels={'small':_pivots(rows,max(2,int(c['pivot_legs'])),'small',a,m),'medium':_pivots(rows,max(3,int(c['medium_pivot_legs'])),'medium',a,m),'large':_pivots(rows,max(5,int(c['large_pivot_legs'])),'large',a,m)};sw=levels['small'];box=_range(rows,levels['medium'] or sw,a,c)
 events=[];state='undetermined';used={'high':False,'low':False};confirm=int(c['break_confirm_bars'])
 for i,r in enumerate(rows):
  hs=[s for s in sw if s['kind']=='high' and s['confirmed_at']<=i];ls=[s for s in sw if s['kind']=='low' and s['confirmed_at']<=i];h=hs[-1] if hs else None;l=ls[-1] if ls else None;close=_v(r,'close')
  if h and close>h['price']+a*.05 and not used['high'] and sum(_v(x,'close')>h['price'] for x in rows[i:i+confirm])>=confirm: events.append({'index':i,'type':'choch' if state=='bearish' else 'bos','direction':'up','level':h['price'],'swing_index':h['index']});state='bullish';used={'high':True,'low':False}
  elif l and close<l['price']-a*.05 and not used['low'] and sum(_v(x,'close')<l['price'] for x in rows[i:i+confirm])>=confirm: events.append({'index':i,'type':'choch' if state=='bullish' else 'bos','direction':'down','level':l['price'],'swing_index':l['index']});state='bearish';used={'high':False,'low':True}
  elif h and _v(r,'high')>h['price'] and close<=h['price']:events.append({'index':i,'type':'liquidity_sweep','direction':'up','level':h['price'],'swing_index':h['index']})
  elif l and _v(r,'low')<l['price'] and close>=l['price']:events.append({'index':i,'type':'liquidity_sweep','direction':'down','level':l['price'],'swing_index':l['index']})
 # Liquidity sweeps are annotations only; they must not split the main structure.
 structure_events=[e for e in events if e['type'] in ('bos','choch')]
 points=[0]+[e['index'] for e in structure_events]+[max(0,len(rows)-1)];segs=[]
 for x,y in zip(points,points[1:]):
  e=next((z for z in structure_events if z['index']==x),None);segs.append({'start_index':x,'end_index':y,'bars':y-x+1,'type':'up' if e and e['direction']=='up' else 'down' if e and e['direction']=='down' else 'transition','event':e})
 merged=[]
 for s in segs:
  if merged and s['bars']<int(c['min_segment_bars']):merged[-1]['end_index']=s['end_index'];merged[-1]['bars']=merged[-1]['end_index']-merged[-1]['start_index']+1
  elif merged and merged[-1]['type']==s['type']:merged[-1]['end_index']=s['end_index'];merged[-1]['bars']=merged[-1]['end_index']-merged[-1]['start_index']+1
  else:merged.append(s)
 segs=merged
 if box and box['active']:
  state='range'
  # A confirmed box is one segment, not an unrelated colour band layered over a trend.
  recent_start=max(0,len(rows)-max(30,min(120,len(rows))))
  segs=[s for s in segs if s['end_index']<recent_start]
  segs.append({'start_index':recent_start,'end_index':len(rows)-1,'bars':len(rows)-recent_start,'type':'sideways','event':{'type':'range_confirmed','direction':'neutral','level':box['top']}})
 trendlines=_trendlines(rows,levels,a,c)
 return {'symbol':symbol,'period':period,'config':c,'atr':a,'swings':sw,'pivot_levels':levels,'range':box,'trendlines':trendlines,'events':events,'segments':segs[-5:],'current_state':state,'last_bar_time':rows[-1].get('timestamp') if rows else None,'analyzed_at':datetime.utcnow().isoformat()}

def analyze_incremental(symbol:str,period:str,rows:List[Dict],config:Dict=None)->Dict:
 """Stateful entry point used by API callers.

 The result is keyed by symbol/period and reused when the latest confirmed bar
 has not changed.  On a new bar we recalculate the bounded input window and
 preserve the previous snapshot metadata so consumers can distinguish a fresh
 calculation from a cached read.
 """
 key=f'{symbol}::{period.upper()}'
 latest=rows[-1].get('timestamp') if rows else None
 cached=_CACHE.get(key)
 if cached and cached.get('last_bar_time')==latest:
  return {**cached,'calculation_mode':'cached'}
 result=analyze(symbol,period,rows,config)
 result['calculation_mode']='incremental' if cached else 'initial'
 result['previous_last_bar_time']=cached.get('last_bar_time') if cached else None
 _CACHE[key]=result
 return result

def restore_snapshot(snapshot:Dict):
 """Restore a previously persisted result into the in-process cache."""
 if isinstance(snapshot,dict) and snapshot.get('symbol') and snapshot.get('period'):
  _CACHE[f"{snapshot['symbol']}::{str(snapshot['period']).upper()}"]=snapshot
