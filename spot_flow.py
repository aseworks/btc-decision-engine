"""7-day Bitcoin *spot* taker buy/sell delta from exchange executed-trade aggregates.

A Binance spot hourly kline exposes quote volume and taker-buy quote volume.
CVD (signed dollar notional): sum(2*taker_buy_quote - total_quote_volume).
This is an actual *aggressor-side flow aggregate*, NOT an OHLC-price proxy.
It covers ONE VENUE and ONE SYMBOL (BTCUSDT), not global crypto CVD.
Only completed, consecutive 1-hour records are accepted; absent hours fail closed.
"""
from __future__ import annotations
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


def parse_spot_klines(rows, now=None, source='Binance Global BTCUSDT SPOT', quality=1.0):
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
    if current_hour-end>2*HOUR_MS:
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
                           timeout=(4,8),headers=HEADERS)
            r.raise_for_status()
            return parse_spot_klines(r.json(),source=name,quality=quality)
        except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
            errors.append(f'{name}/{endpoint.split("/")[2]}: {type(exc).__name__}: {str(exc)[:75]}')
    raise ValueError('Automatic spot taker CVD unavailable: '+'; '.join(errors)[:360])
