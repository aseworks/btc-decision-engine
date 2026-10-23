"""Read-only data adapters. Failure never silently becomes a zero or demo value."""
from __future__ import annotations
import os
import requests
from datetime import datetime, timezone, timedelta

HEADERS={'Accept':'application/json','User-Agent':'BTC-Decision-Research/0.2'}


def get_json(url, params=None):
    r=requests.get(url,params=params,headers=HEADERS,timeout=12)
    r.raise_for_status()
    return r.json()


def coinbase_daily():
    raw=get_json('https://api.exchange.coinbase.com/products/BTC-USD/candles', {'granularity':86400})
    if not isinstance(raw,list) or len(raw)<201: raise ValueError('Coinbase returned too few candles')
    # [time, low, high, open, close, volume]. Exclude still-forming UTC daily candle.
    today=datetime.now(timezone.utc).date()
    candles=sorted([r for r in raw if len(r)>=6 and datetime.fromtimestamp(r[0], timezone.utc).date()<today],key=lambda r:r[0])
    if len(candles)<201: raise ValueError('Not enough completed daily candles')
    close=[float(x[4]) for x in candles]
    vol=[float(x[5]) for x in candles]
    ret7=(close[-1]/close[-8]-1)*100
    # Compare last completed day's volume with preceding 30 completed days.
    rv=vol[-1]/(sum(vol[-31:-1])/30) if sum(vol[-31:-1]) else None
    return dict(last_close=close[-1],sma50=sum(close[-50:])/50,sma200=sum(close[-200:])/200,
                return_7d=ret7,relative_volume=rv,as_of=datetime.fromtimestamp(candles[-1][0],timezone.utc).isoformat(),
                close_series=[{'date':datetime.fromtimestamp(x[0],timezone.utc).strftime('%Y-%m-%d'),
                               'close':float(x[4])} for x in candles[-120:]])


def binance_derivatives():
    """Fetch funding and OI independently; one failing endpoint must not erase the other."""
    base='https://fapi.binance.com'
    out={}; issues=[]
    try:
        funding=get_json(base+'/fapi/v1/fundingRate', {'symbol':'BTCUSDT','limit':100})
        recent=[x for x in funding if int(x['fundingTime']) >= int((datetime.now(timezone.utc)-timedelta(days=3)).timestamp()*1000)]
        if recent:
            out['funding_3d_avg_pct']=sum(float(x['fundingRate'])*100 for x in recent)/len(recent)
            out['as_of']=datetime.fromtimestamp(int(recent[-1]['fundingTime'])/1000,timezone.utc).isoformat()
    except (requests.RequestException,ValueError,TypeError,KeyError) as e:
        issues.append('funding '+str(e))
    try:
        oi=get_json(base+'/futures/data/openInterestHist', {'symbol':'BTCUSDT','period':'1d','limit':11})
        points=sorted([x for x in oi if datetime.fromtimestamp(x['timestamp']/1000,timezone.utc).date()<datetime.now(timezone.utc).date()],key=lambda x:x['timestamp'])
        if len(points)>=8:
            old=float(points[-8]['sumOpenInterestValue']); new=float(points[-1]['sumOpenInterestValue'])
            if old>0: out['oi_change_7d_pct']=(new/old-1)*100
            out['as_of']=datetime.fromtimestamp(points[-1]['timestamp']/1000,timezone.utc).isoformat()
    except (requests.RequestException,ValueError,TypeError,KeyError) as e:
        issues.append('open interest '+str(e))
    if not out:
        raise ValueError('Binance unavailable: '+('; '.join(issues) if issues else 'empty response'))
    return out


def glassnode_onchain(api_key: str):
    """Optional aSOPR + entity-based whale cohort; subscription required."""
    endpoints={'asopr':'indicators/sopr_adjusted','whale':'entities/supply_balance_1k_10k'}
    fetched={}; observed=[]
    for k,p in endpoints.items():
        rows=get_json('https://api.glassnode.com/v1/metrics/'+p,{'a':'BTC','i':'24h','api_key':api_key})
        rows=[r for r in rows if isinstance(r.get('v'),(int,float))]
        if len(rows)<32: raise ValueError('Insufficient Glassnode history for '+k)
        rows.sort(key=lambda x:x['t']); fetched[k]=rows
        observed.append(int(rows[-1]['t']))
    out={'asopr':float(fetched['asopr'][-1]['v']),
         'whale_1k_10k_30d_pct':(float(fetched['whale'][-1]['v'])/float(fetched['whale'][-31]['v'])-1)*100,
         'as_of':datetime.fromtimestamp(min(observed),timezone.utc).isoformat(),
         'source':'Glassnode API (aSOPR + entity whale 1K–10K BTC)'}
    return out


def fetch_all(glassnode_key=''):
    market={}; extras={}; errors=[]
    for name, fn in [('price',coinbase_daily),('derivatives',binance_derivatives)]:
        try: market[name]=fn()
        except (requests.RequestException, ValueError, KeyError, TypeError, ZeroDivisionError) as e:
            errors.append(f'{name}: {e}')
    if glassnode_key:
        try:extras['onchain']=glassnode_onchain(glassnode_key)
        except (requests.RequestException, ValueError, KeyError, TypeError,ZeroDivisionError) as e:
            errors.append('onchain: API data unavailable / permission denied / insufficient history (secret hidden)')
    return market,extras,errors
