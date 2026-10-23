"""Independent macro and real on-chain public feeds, fail-closed.

FRED CSV is publicly downloadable without the FRED API key.
Coin Metrics community endpoint is license-limited, and individual metric
availability may change; fallback to a real Blockchain.com chart metric.
No proxy may be described as whale accumulation.
"""
from __future__ import annotations
BUILD_ID = "v0.7.1-20261024-r2"

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


def _yahoo_tnx():
    """Third-party ^TNX index proxy of nominal US 10-year yield, not Treasury data.

    It is an independent FALLBACK, downweighted and explicitly labelled.
    Quote conventions can differ; impossible or implausible units fail closed.
    """
    obj=_fetch('https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX',
               {'range':'3mo','interval':'1d'}).json()
    blocks=(obj.get('chart',{}).get('result') or [])
    if not blocks:raise ValueError('Yahoo chart has no result')
    block=blocks[0]
    stamps=block.get('timestamp') or []
    quotes=((block.get('indicators') or {}).get('quote') or [])
    closes=(quotes[0].get('close') or []) if quotes else []
    values=[]
    for t,value in zip(stamps,closes):
        try:
            n=float(value)
            if not math.isfinite(n) or n<=0:continue
            # ^TNX is normally expressed as yield x10 (e.g. 49.6 means 4.96%).
            n=n/10 if n>20 else n
            if not .05<n<20:continue
            values.append((datetime.fromtimestamp(int(t),timezone.utc).date().isoformat(),n))
        except (TypeError,ValueError,OverflowError):continue
    return {'DGS10':_series_from_points('DGS10',list(dict(values).items()))},\
           'Yahoo Finance ^TNX market index (third-party 10Y yield proxy)',.45


def macro_public():
    """Parallel independent macro feeds, with explicit provenance and quality.

    Primary: US Treasury official. Backup: documented mirror, then FRED, then
    market ^TNX proxy. Optional USD/credit FRED metrics never block yield signal.
    Missing metrics strictly lower coverage. Does not reuse stale macro numbers.
    """
    attempts=[('Treasury official',_treasury_official),
              ('Treasury mirror',_treasury_mirror),
              ('FRED DGS10',lambda: ({'DGS10':fred_series('DGS10')},'FRED 10-year yield',.95)),
              ('Yahoo ^TNX',_yahoo_tnx),
              ('FRED dollar',lambda: fred_series('DTWEXBGS')),
              ('FRED credit',lambda: fred_series('BAMLH0A0HYM2'))]
    successes={};errors=[]
    # In previous releases nested sequential fallbacks could stall ~30s.
    # Run independent requests concurrently; each request already has timeouts.
    with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
        futures={pool.submit(fn):label for label,fn in attempts}
        for future in as_completed(futures):
            label=futures[future]
            try:successes[label]=future.result()
            except (requests.RequestException, ValueError, TypeError, KeyError,
                    ZeroDivisionError, OSError) as exc:
                errors.append(f'{label}: {type(exc).__name__}: {str(exc)[:100]}')

    series={};provenance=[];reliability=0.0
    for key in ['Treasury official','Treasury mirror','FRED DGS10','Yahoo ^TNX']:
        if key in successes:
            selected,source,reliability=successes[key]
            if 'DGS10' in selected:
                series.update(selected);provenance.append(source);break
    for label,name in [('FRED dollar','DTWEXBGS'),('FRED credit','BAMLH0A0HYM2')]:
        if label in successes:
            series[name]=successes[label];provenance.append('FRED '+name)
    if not series:
        raise ValueError('All independent macro feeds failed: '+'; '.join(errors)[:400])

    directions=[];notes=[];quality=0.0
    if 'DGS10' in series:
        v=series['DGS10'];delta=v['now']-v['past']
        weight=.5*reliability
        directions.append((-1 if delta>=.25 else (1 if delta<=-.25 else 0),weight))
        quality+=weight
        notes.append(f'10Y yield {v["now"]:.2f}% (20 observations {delta:+.2f} pp)')
        if 'DGS2' in series:
            notes.append(f'10Y-2Y curve {v["now"]-series["DGS2"]["now"]:+.2f} pp (context only)')
    if 'DTWEXBGS' in series:
        v=series['DTWEXBGS'];delta=100*(v['now']/v['past']-1) if v['past'] else 0
        directions.append((-1 if delta>=1.5 else (1 if delta<=-1.5 else 0),.25))
        quality+=.25;notes.append(f'Trade-weighted USD {delta:+.2f}% / 20 obs')
    if 'BAMLH0A0HYM2' in series:
        v=series['BAMLH0A0HYM2'];delta=v['now']-v['past']
        directions.append((-1 if delta>=.35 else (1 if delta<=-.35 else 0),.25))
        quality+=.25;notes.append(f'High-yield spread {v["now"]:.2f} pp (change {delta:+.2f} pp)')
    signal=sum(v*w for v,w in directions)/sum(w for _,w in directions)
    regime='Tightening' if signal<=-.34 else ('Easing' if signal>=.34 else 'Neutral')
    as_of=min(pd.to_datetime(v['as_of'],utc=True).date() for v in series.values()).isoformat()
    return {'regime':regime,'source':'; '.join(provenance),
            'as_of':as_of+'T00:00:00+00:00',
            'macro_signal_score':round(signal*1.5,3),
            'macro_quality':round(min(1.0,quality),3),
            'components':series,'explanation':'; '.join(notes),'issues':errors,
            'methodology':'Independent yield/FX/credit composite; third-party proxies discounted; '
                          'missing inputs lower coverage, never assigned zero.'}

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
    """Public Coin Metrics, concurrent per metric; fallback only to real ledger activity.

    No exchange, price proxy or transfer-count is presented as entity-adjusted whales.
    """
    out={'source':'','as_of':'','issues':[]}
    jobs=[('CapMVRVCur','mvrv'),('SOPR','sopr'),('AdrActCnt','active_address_7d_pct')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(_cm_rows,metric):(metric,target) for metric,target in jobs}
        for future in as_completed(futures):
            metric,target=futures[future]
            try:
                series=future.result()
                last=series[-1][1]
                if target=='active_address_7d_pct':
                    prev=sum(p[1] for p in series[-21:-7])/14
                    last7=sum(p[1] for p in series[-7:])/7
                    value=(last7/prev-1)*100 if prev else 0
                else:value=last
                out[target]=round(value,5)
                out['as_of']=max(out['as_of'],series[-1][0])
                out.setdefault('series',{})[target]=[{'date':d,'value':v} for d,v in series[-60:]]
                out['source']+='Coin Metrics Community '+metric+'; '
            except (requests.RequestException,ValueError,TypeError,KeyError,ZeroDivisionError) as exc:
                out['issues'].append(f'{metric}: {type(exc).__name__}: {str(exc)[:90]}')
    if not any(k in out for k in ('mvrv','sopr','active_address_7d_pct')):
        try:out.update(_chain_transactions())
        except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
            out['issues'].append(f'Bitcoin transactions: {type(exc).__name__}: {str(exc)[:90]}')
    if not any(k in out for k in ('mvrv','sopr','active_address_7d_pct','chain_tx_7d_pct')):
        raise ValueError('On-chain community sources unavailable: '+'; '.join(out['issues'])[:250])
    return out
