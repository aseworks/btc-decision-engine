"""7-day Bitcoin *spot* taker buy/sell delta from exchange executed-trade aggregates.

A Binance spot hourly kline exposes quote volume and taker-buy quote volume.
CVD (signed dollar notional): sum(2*taker_buy_quote - total_quote_volume).
This is an actual *aggressor-side flow aggregate*, NOT an OHLC-price proxy.
It covers ONE VENUE and ONE SYMBOL (BTCUSDT), not global crypto CVD.
Only completed, consecutive 1-hour records are accepted; absent hours fail closed.
"""
from __future__ import annotations
BUILD_ID = "v0.6.0-20261023-r1"

from datetime import datetime, timezone, timedelta
import math
import requests

HEADERS={'User-Agent':'BTCAtlasResearch/0.5 (+https://streamlit.io)', 'Accept':'application/json'}
HOUR_MS=3_600_000
# Binance's market-data-only endpoint is separately documented and doesn't need credentials.
VENUES=[
    ('https://data-api.binance.vision/api/v3/klines','Binance Global BTCUSDT SPOT',1.0),
    ('https://api.binance.com/api/v3/klines','Binance Global BTCUSDT SPOT',1.0),
    ('https://api1.binance.com/api/v3/klines','Binance Global BTCUSDT SPOT',1.0),
    # Binance.US is a *smaller, different* order book. Keep its quality low.
    ('https://api.binance.us/api/v3/klines','Binance.US BTCUSDT SPOT (limited venue)',0.35),
]


def parse_spot_klines(rows, now=None, source='Binance Global BTCUSDT SPOT', quality=1.0, allowed_lag_hours=2):
    """Compute trailing 168 completed hours and 24h delta, with strict continuity.

    Assumes Binance native 12-column API records. Ignores the open/incomplete hour.
    """
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: now=now.replace(tzinfo=timezone.utc)
    now_ms=int(now.timestamp()*1000)
    completed={}
    if not isinstance(rows,list):raise ValueError('Exchange returned non-list kline data')
    for row in rows:
        if not isinstance(row,list) or len(row)<11:continue
        try:
            t=int(row[0]); closed=int(row[6]); total=float(row[7]); buy=float(row[10])
        except (ValueError, TypeError, IndexError):continue
        if (not all(math.isfinite(v) for v in (total,buy)) or total<=0
                or buy<0 or buy>total*(1+1e-8) or t % HOUR_MS!=0):continue
        if closed<now_ms and closed>=t+HOUR_MS-1 and closed<=t+HOUR_MS:
            completed[t]=(total,buy)
    if len(completed)<168:
        raise ValueError(f'Only {len(completed)} completed hourly spot observations; require 168')
    end=max(completed)
    current_hour=(now_ms//HOUR_MS)*HOUR_MS
    if current_hour-end>allowed_lag_hours*HOUR_MS:
        raise ValueError('Latest completed hourly observation is stale')
    expected=[end-i*HOUR_MS for i in range(167,-1,-1)]
    gaps=[t for t in expected if t not in completed]
    if gaps:raise ValueError(f'Incomplete 7D kline series: missing {len(gaps)} hourly observations')
    series=[];total_quote=0;total_buy=0
    daily={}
    for t in expected:
        total,buy=completed[t]
        delta=2*buy-total
        total_quote+=total;total_buy+=buy
        date=datetime.fromtimestamp(t/1000,timezone.utc).date().isoformat()
        daily[date]=daily.get(date,0)+delta
        series.append({'open_time':t,'delta_usd':delta})
    last24=sum(s['delta_usd'] for s in series[-24:])
    last_timestamp=datetime.fromtimestamp((end+HOUR_MS-1)/1000,timezone.utc).isoformat()
    return {'spot_cvd_7d_usd':round(sum(s['delta_usd'] for s in series),2),
            'spot_cvd_24h_usd':round(last24,2),
            'spot_cvd_quote_volume_7d_usd':round(total_quote,2),
            'spot_cvd_buy_share_pct':round(100*total_buy/total_quote,3),
            'spot_cvd_series':[{'date':day,'delta_usd':round(v,2)} for day,v in sorted(daily.items())],
            'spot_cvd_source':source+'; taker-buy quote volume from executed SPOT trades',
            'spot_cvd_as_of':last_timestamp,'spot_cvd_quality':float(quality),
            'spot_cvd_hours':168,
            'spot_cvd_note':'ONE exchange BTCUSDT pair, seven completed rolling days; not global BTC spot CVD.'}


def automatic_spot_cvd():
    errors=[]
    for endpoint,name,quality in VENUES:
        try:
            r=requests.get(endpoint,params={'symbol':'BTCUSDT','interval':'1h','limit':175},
                           timeout=(2,5),headers=HEADERS)
            r.raise_for_status()
            return parse_spot_klines(r.json(),source=name,quality=quality)
        except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
            errors.append(f'{name}/{endpoint.split("/")[2]}: {type(exc).__name__}: {str(exc)[:75]}')
    raise ValueError('Automatic spot taker CVD unavailable: '+'; '.join(errors)[:360])

# Independent publication channel (Binance public daily archives). The archive is
# released after the UTC trading day ends, so it is *delayed* and down-weighted.
# Official documentation: https://github.com/binance/binance-public-data
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO, TextIOWrapper
from zipfile import ZipFile, BadZipFile
import csv

ARCHIVE_BASE='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1h'


def _archive_day(day):
    """Read one real 24-hour archived spot kline CSV (2025+: microsecond timestamps)."""
    label=day.isoformat()
    name=f'BTCUSDT-1h-{label}'
    url=f'{ARCHIVE_BASE}/{name}.zip'
    response=requests.get(url,headers=HEADERS,timeout=(2,6))
    response.raise_for_status()
    if len(response.content)>2_000_000:
        raise ValueError(f'Archived hourly kline file too large: {label}')
    try:
        with ZipFile(BytesIO(response.content)) as zipped:
            if len(zipped.namelist())!=1:raise ValueError('Unexpected archive member count')
            info=zipped.infolist()[0]
            if not info.filename.endswith('.csv') or info.file_size>2_000_000:
                raise ValueError('Unexpected archive member')
            with zipped.open(info) as fd:
                records=list(csv.reader(TextIOWrapper(fd,encoding='utf-8-sig')))
    except BadZipFile as exc:
        raise ValueError(f'Invalid archive ZIP {label}') from exc
    normalized=[]
    for r in records:
        if len(r)<11:continue
        try:
            op=int(r[0]);cl=int(r[6])
            # Official Binance archive switched from milliseconds to microseconds in 2025.
            if op>=10**14: op//=1000
            if cl>=10**14: cl//=1000
            vals=list(r);vals[0]=op;vals[6]=cl
            if datetime.fromtimestamp(op/1000,timezone.utc).date()!=day:continue
            normalized.append(vals)
        except (ValueError,TypeError,OverflowError):pass
    if len(normalized)!=24:raise ValueError(f'Archive {label}: got {len(normalized)} hours, need 24')
    return normalized


def archived_spot_cvd(now=None):
    """CVD for 7 completed UTC days, from public spot exchange execution aggregates.

    Tries through yesterday, then through the day before yesterday if the latest
    archive has not yet been published. Distinct window from rolling 168h live.
    """
    now=now or datetime.now(timezone.utc)
    failures=[]
    for lag,quality in ((1,.82),(2,.68)):
        final_day=now.date()-timedelta(days=lag)
        dates=[final_day-timedelta(days=i) for i in range(6,-1,-1)]
        data={}
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures={pool.submit(_archive_day,day):day for day in dates}
            for future in as_completed(futures):
                try:data[futures[future]]=future.result()
                except (requests.RequestException, ValueError, OSError, BadZipFile) as exc:
                    failures.append(f'{futures[future]}:{type(exc).__name__}')
        if len(data)!=7:continue
        all_rows=[r for day in dates for r in data[day]]
        result=parse_spot_klines(all_rows,now=now,
                source='Binance Global BTCUSDT SPOT · public daily ZIP archive',
                quality=quality, allowed_lag_hours=51)
        result['spot_cvd_note']=(f'Archived execution-derived taker delta; seven completed UTC days ending '
                 f'{final_day.isoformat()}, NOT rolling live 168h. Published with a delay.')
        result['spot_cvd_mode']='archive'
        return result
    raise ValueError('Binance public archive unavailable: '+', '.join(failures[:12]))


# Preserve original live API algorithm but use the genuine executed-trade archive
# if an API is blocked in the hosted deployment.
_legacy_live_spot_cvd=automatic_spot_cvd

def _live_spot_cvd():
    """Race independent API endpoints; prefer Binance Global when available."""
    errors=[];successes=[]
    venues=[VENUES[0],VENUES[1],VENUES[3]]
    def request_one(info):
        endpoint,name,quality=info
        r=requests.get(endpoint,params={'symbol':'BTCUSDT','interval':'1h','limit':175},
                       timeout=(2,5),headers=HEADERS)
        r.raise_for_status()
        return parse_spot_klines(r.json(),source=name,quality=quality)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(request_one,v):v for v in venues}
        for f in as_completed(futures):
            try:successes.append(f.result())
            except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
                errors.append(f'{futures[f][1]}: {type(exc).__name__}')
    if successes:
        return sorted(successes,key=lambda x:x['spot_cvd_quality'],reverse=True)[0]
    raise ValueError('All real-time spot kline endpoints unavailable: '+', '.join(errors))


def automatic_spot_cvd():
    try:
        out=_live_spot_cvd()
        out['spot_cvd_mode']='live'
        return out
    except (requests.RequestException, ValueError, TypeError, KeyError) as api_error:
        try:
            out=archived_spot_cvd()
            out['spot_cvd_fallback_reason']=f'Exchange API: {type(api_error).__name__}'
            return out
        except (requests.RequestException, ValueError, TypeError, KeyError) as archive_error:
            raise ValueError(
                f'Spot exchange APIs unavailable ({str(api_error)[:240]}); '
                f'archive unavailable ({str(archive_error)[:180]})') from archive_error
