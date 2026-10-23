"""Independent macro and real on-chain public feeds, fail-closed.

FRED CSV is publicly downloadable without the FRED API key.
Coin Metrics community endpoint is license-limited, and individual metric
availability may change; fallback to a real Blockchain.com chart metric.
No proxy may be described as whale accumulation.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from io import StringIO
import math
import requests
import pandas as pd

UA = {'User-Agent': 'Mozilla/5.0 (compatible; BTCDecisionResearch/0.4)', 'Accept': 'application/json,text/csv,*/*'}


def _fetch(url, params=None):
    r = requests.get(url, params=params, headers=UA, timeout=11)
    r.raise_for_status()
    return r


def _fresh_day(d, maxdays=10):
    dt = pd.to_datetime(d, utc=True).to_pydatetime()
    age = (datetime.now(timezone.utc) - dt).total_seconds()/86400
    if age < -1 or age > maxdays:
        raise ValueError(f'Observation stale or future: {d}')
    return dt


def fred_series(series_id, days=90):
    """FRED CSV, positive/negative values in their original series units."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = 'https://fred.stlouisfed.org/graph/fredgraph.csv'
    text = _fetch(url, {'id':series_id, 'cosd':start.isoformat(), 'coed':end.isoformat()}).text
    if '<html' in text[:300].lower():
        raise ValueError('FRED returned HTML, not a CSV')
    df = pd.read_csv(StringIO(text))
    if series_id not in df or df.empty:
        raise ValueError(f'FRED column {series_id} not present')
    datecol = 'observation_date' if 'observation_date' in df else 'DATE'
    df[series_id] = pd.to_numeric(df[series_id], errors='coerce')
    df = df.dropna(subset=[series_id]).sort_values(datecol)
    if len(df)<16:
        raise ValueError(f'Not enough {series_id} values')
    stamp = str(df.iloc[-1][datecol])
    _fresh_day(stamp, 9)
    return {'id':series_id, 'now':float(df.iloc[-1][series_id]),
            'past':float(df.iloc[max(0,len(df)-21)][series_id]),
            'as_of':stamp,
            'series':[{'date':str(row[datecol]), 'value':float(row[series_id])} for _,row in df.tail(55).iterrows()]}


def macro_public():
    """Estimate macro pressure from 20-trading-observation changes, not predict FOMC.

    Negative pressure: sharply higher 10y yield, stronger trade-weighted dollar,
    widening junk-bond credit spreads. 10y yield: percentage points, not percent.
    """
    specs=[('DGS10','10Y Treasury yield'), ('DTWEXBGS','Trade-weighted USD'),
           ('BAMLH0A0HYM2','US high-yield spread')]
    series={}; errors=[]
    for key,label in specs:
        try: series[key]=fred_series(key)
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
            errors.append(f'{key}: {type(exc).__name__}: {str(exc)[:110]}')
    changes=[]; notes=[]
    if 'DGS10' in series:
        item=series['DGS10']; chg=item['now']-item['past']
        changes.append(-1 if chg>=.25 else (1 if chg<=-.25 else 0))
        notes.append(f'US10Y {item["now"]:.2f}%, change 20 obs {chg:+.2f}pp')
    if 'DTWEXBGS' in series:
        item=series['DTWEXBGS']; chg=100*(item['now']/item['past']-1) if item['past'] else 0
        changes.append(-1 if chg>=1.5 else (1 if chg<=-1.5 else 0))
        notes.append(f'Broad USD 20 obs {chg:+.2f}%')
    if 'BAMLH0A0HYM2' in series:
        item=series['BAMLH0A0HYM2']; chg=item['now']-item['past']
        changes.append(-1 if chg>=.35 else (1 if chg<=-.35 else 0))
        notes.append(f'US HY spread {item["now"]:.2f}pp, change 20 obs {chg:+.2f}pp')
    if not changes:
        raise ValueError('Macro public sources unavailable: '+'; '.join(errors))
    # Two out of three valid inputs needed for a definitive regime; 1 is partial.
    m=sum(changes)/len(changes)
    regime = 'Tightening' if m<=-.34 else ('Easing' if m>=.34 else 'Neutral')
    stamps=[pd.Timestamp(x['as_of']).date() for x in series.values()]
    as_of=datetime.combine(min(stamps),datetime.min.time(),tzinfo=timezone.utc).isoformat()
    return {'regime':regime, 'source':'FRED: '+', '.join(series), 'as_of':as_of,
            'macro_signal_score':round(m*1.5,3), 'macro_quality':len(series)/3,
            'components':series, 'explanation':'; '.join(notes), 'issues':errors}


def _cm_rows(metric):
    start=(datetime.now(timezone.utc).date()-timedelta(days=82)).isoformat()
    endpoint='https://community-api.coinmetrics.io/v4/timeseries/asset-metrics'
    obj=_fetch(endpoint, {'assets':'btc','metrics':metric,'frequency':'1d','start_time':start,
                          'page_size':'100'}).json()
    data=obj.get('data') or []
    out=[]
    for r in data:
        try:
            v=float(r[metric]); dt=str(r['time'])[:10]
            if math.isfinite(v):out.append((dt,v))
        except (ValueError,TypeError,KeyError):pass
    out.sort()
    if len(out)<22: raise ValueError(f'Insufficient Coin Metrics {metric} observations: {len(out)}')
    _fresh_day(out[-1][0],7)
    return out


def _chain_transactions():
    """Public BTC ledger transaction count. Context-only, NOT whale positioning."""
    url='https://api.blockchain.info/charts/n-transactions'
    obj=_fetch(url, {'timespan':'90days','format':'json'}).json()
    points=[]
    for p in obj.get('values',[]):
        try:
            dt=datetime.fromtimestamp(int(p['x']), timezone.utc).date().isoformat()
            v=float(p['y'])
            if math.isfinite(v) and v>0: points.append((dt,v))
        except (TypeError,ValueError,KeyError):pass
    points.sort()
    if len(points)<22:raise ValueError('Blockchain.com transaction count history unavailable')
    _fresh_day(points[-1][0],8)
    nowavg=sum(x[1] for x in points[-7:])/7
    prevavg=sum(x[1] for x in points[-21:-7])/14
    return {'chain_tx_7d_pct':(nowavg/prevavg-1)*100 if prevavg else 0,
            'as_of':points[-1][0], 'source':'Blockchain.com n-transactions (activity only)',
            'activity_series':[{'date':d,'value':v} for d,v in points[-60:]]}


def onchain_community():
    """Use actual on-chain metrics if freely available; no invented zero values.

    Community access to MVRV and SOPR can be restricted by Coin Metrics.
    Always fall back to chain transaction count, marked context-only.
    """
    out={'source':'', 'as_of':'', 'issues':[]}
    for metric,target in [('CapMVRVCur','mvrv'),('SOPR','sopr'),('AdrActCnt','active_address_7d_pct')]:
        try:
            series=_cm_rows(metric)
            last=series[-1][1]
            if target=='active_address_7d_pct':
                prev=sum(p[1] for p in series[-21:-7])/14
                last7=sum(p[1] for p in series[-7:])/7
                value=(last7/prev-1)*100 if prev else 0
            else: value=last
            out[target]=round(value,5)
            out['as_of']=max(out['as_of'],series[-1][0])
            out.setdefault('series',{})[target]=[{'date':d,'value':v} for d,v in series[-60:]]
            out['source']+='Coin Metrics Community '+metric+'; '
        except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
            out['issues'].append(f'{metric}: {type(exc).__name__}: {str(exc)[:90]}')
    if not any(k in out for k in ('mvrv','sopr','active_address_7d_pct')):
        # Genuine ledger observation but contextual only: not a proxy for whales.
        try:
            v=_chain_transactions();out.update(v)
        except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
            out['issues'].append(f'Bitcoin transactions: {type(exc).__name__}: {str(exc)[:90]}')
    if not any(k in out for k in ('mvrv','sopr','active_address_7d_pct','chain_tx_7d_pct')):
        raise ValueError('On-chain community sources unavailable: '+'; '.join(out['issues'])[:250])
    return out
