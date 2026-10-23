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
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

UA = {'User-Agent': 'Mozilla/5.0 (compatible; BTCDecisionResearch/0.4)', 'Accept': 'application/json,text/csv,*/*'}


def _fetch(url, params=None):
    r = requests.get(url, params=params, headers=UA, timeout=(3,7))
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


def _series_from_points(series_id, values):
    """Standardise official or third-party historical Treasury daily observations."""
    values=sorted(values)
    if len(values)<16:raise ValueError(f'{series_id}: insufficient history ({len(values)})')
    stamp=values[-1][0]
    _fresh_day(stamp,9)
    return {'id':series_id,'now':values[-1][1],
            'past':values[max(0,len(values)-21)][1], 'as_of':stamp,
            'series':[{'date':d,'value':float(v)} for d,v in values[-55:]]}


def _treasury_official():
    """Official US Treasury 2Y and 10Y (daily par yield curve), no API key."""
    year=datetime.now(timezone.utc).year
    url=f'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all'
    response=requests.get(url,params={'type':'daily_treasury_yield_curve'},headers=UA,timeout=(3,6))
    response.raise_for_status()
    if '<html' in response.text[:250].lower():raise ValueError('Treasury response was HTML')
    df=pd.read_csv(StringIO(response.text))
    df.columns=[str(c).strip() for c in df.columns]
    date_col=next((x for x in ('Date','DATE','date') if x in df),None)
    if not date_col:raise ValueError('Treasury CSV missing date field')
    for key in ('10 Yr','2 Yr'):
        if key not in df:raise ValueError(f'Treasury CSV missing {key}')
    dated=pd.to_datetime(df[date_col],errors='coerce')
    out={}
    for label,key in [('DGS10','10 Yr'),('DGS2','2 Yr')]:
        arr=[]
        for day,value in zip(dated, pd.to_numeric(df[key],errors='coerce')):
            if pd.notna(day) and pd.notna(value):arr.append((day.date().isoformat(),float(value)))
        out[label]=_series_from_points(label,arr)
    return out,'U.S. Treasury official daily yield curve',1.0


def _treasury_mirror():
    """Documented independent mirror of US Treasury CSV, clearly labelled as mirror.

    This mirror is not guaranteed and is NOT equivalent to official direct access.
    """
    obj=_fetch('https://fedgazette.com/fed-data/treasury-yields.json').json()
    if not obj.get('ok') or not isinstance(obj.get('history'),list):
        raise ValueError('Treasury mirror response invalid')
    rows=list(obj['history'])
    if obj.get('latest') and obj['latest'].get('date'):
        rows.append(obj['latest'])
    out={}
    for key,label in [('10 Yr','DGS10'),('2 Yr','DGS2')]:
        points=[]
        for r in rows:
            try:
                day=datetime.strptime(str(r['date']),'%m/%d/%Y').date().isoformat()
                v=float(r[key])
                if math.isfinite(v):points.append((day,v))
            except (KeyError,TypeError,ValueError):pass
        # Dedupe latest row if present in history.
        out[label]=_series_from_points(label,list(dict(points).items()))
    return out,'Fed Gazette mirror of U.S. Treasury yields (third-party)',.82


def macro_public():
    """Fast resilient *partial* macro signal; no invented metrics or silent zeros.

    US Treasury 10Y: 50% coverage, USD index: 25%, credit spread: 25%.
    A missing series reduces *coverage* instead of being counted as neutral.
    """
    series={}; errors=[]; provenance=[]; reliability=1.0
    try:
        official,source,reliability=_treasury_official()
        series.update(official);provenance.append(source)
    except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
        errors.append(f'Treasury official: {type(exc).__name__}: {str(exc)[:85]}')
        try:
            backup,source,reliability=_treasury_mirror()
            series.update(backup);provenance.append(source)
        except (requests.RequestException,ValueError,KeyError,TypeError) as mirror_exc:
            errors.append(f'Treasury mirror: {type(mirror_exc).__name__}: {str(mirror_exc)[:85]}')
            try:
                series['DGS10']=fred_series('DGS10');provenance.append('FRED (10-year yield)')
            except (requests.RequestException,ValueError,KeyError,TypeError) as fred_exc:
                errors.append(f'FRED DGS10: {type(fred_exc).__name__}: {str(fred_exc)[:85]}')

    # FRED is supplementary, NOT the single point of failure. Each series has
    # its own timeout; neither blocks the usable Treasury macro partial result.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(fred_series,key):key for key in ('DTWEXBGS','BAMLH0A0HYM2')}
        for future in as_completed(futures):
            key=futures[future]
            try:
                series[key]=future.result();provenance.append('FRED '+key)
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                errors.append(f'{key}: {type(exc).__name__}: {str(exc)[:85]}')

    if not any(k in series for k in ('DGS10','DTWEXBGS','BAMLH0A0HYM2')):
        raise ValueError('Macro public sources unavailable: '+'; '.join(errors))
    directions=[];notes=[];quality=0.0
    if 'DGS10' in series:
        item=series['DGS10'];chg=item['now']-item['past']
        directions.append((-1 if chg>=.25 else (1 if chg<=-.25 else 0),.5*reliability))
        quality+=.5*reliability
        notes.append(f'10Y UST {item["now"]:.2f}% (20 observations {chg:+.2f}pp)')
        if 'DGS2' in series:
            curve=item['now']-series['DGS2']['now']
            notes.append(f'10Y−2Y curve {curve:+.2f}pp (context, not separately scored)')
    if 'DTWEXBGS' in series:
        item=series['DTWEXBGS'];chg=100*(item['now']/item['past']-1) if item['past'] else 0
        directions.append((-1 if chg>=1.5 else (1 if chg<=-1.5 else 0),.25))
        quality+=.25
        notes.append(f'Trade-weighted USD {chg:+.2f}% / 20 observations')
    if 'BAMLH0A0HYM2' in series:
        item=series['BAMLH0A0HYM2'];chg=item['now']-item['past']
        directions.append((-1 if chg>=.35 else (1 if chg<=-.35 else 0),.25))
        quality+=.25
        notes.append(f'High-yield credit spread {item["now"]:.2f}pp (change {chg:+.2f}pp)')
    val=sum(v*w for v,w in directions)/sum(w for _,w in directions)
    regime='Tightening' if val<=-.34 else ('Easing' if val>=.34 else 'Neutral')
    # Oldest timestamp among inputs for conservative macro freshness.
    as_of=min(pd.to_datetime(item['as_of'],utc=True).date() for item in series.values()).isoformat()
    return {'regime':regime,'source':'; '.join(provenance),'as_of':as_of+'T00:00:00+00:00',
            'macro_signal_score':round(val*1.5,3),'macro_quality':round(min(1,quality),3),
            'components':series,'explanation':'; '.join(notes), 'issues':errors,
            'methodology':'Rates/FX/credit stress partial composite; coverage discounts missing or mirrored series.'}


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
