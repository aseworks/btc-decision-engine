"""BTC research data connectors. Fail closed; never replace missing data with zero.

Every data point has an observation time and source. ETF primary: Farside public
HTML, with TFTC public table as backup. Not vendor-guaranteed APIs: HTML changes
can break parsing, and fetch failures are shown in dashboard.
"""
from __future__ import annotations
BUILD_ID = "v0.7.0-20261024-r1"

from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
import re
import requests
from bs4 import BeautifulSoup
from macro_onchain import macro_public, onchain_community
from spot_flow import automatic_spot_cvd

HEADERS = {'Accept':'application/json,text/html;q=0.9,*/*;q=0.8',
           'User-Agent':'Mozilla/5.0 (compatible; BTCDecisionResearch/0.3; +https://streamlit.io/)'}


def get_response(url, params=None):
    response = requests.get(url, params=params, headers=HEADERS, timeout=(3,8))
    response.raise_for_status()
    return response


def get_json(url, params=None):
    return get_response(url, params).json()


def now_utc():
    return datetime.now(timezone.utc)


def coinbase_daily():
    """Two ~155-day paginated windows; avoid requiring 201 samples from one request."""
    now = now_utc()
    endpoint = 'https://api.exchange.coinbase.com/products/BTC-USD/candles'
    all_rows={}
    # Coinbase caps ~300 candles/request. Two overlapping periods are sufficient.
    for older, newer in [(320, 160), (162, 0)]:
        start=(now-timedelta(days=older)).isoformat()
        end=(now-timedelta(days=newer)).isoformat()
        rows=get_json(endpoint, {'granularity':86400,'start':start,'end':end})
        if not isinstance(rows,list): raise ValueError('Coinbase returned unexpected candles payload')
        for r in rows:
            if isinstance(r,list) and len(r)>=6:
                t=int(r[0]); all_rows[t]=r
    today=now.date()
    candles=sorted([r for t,r in all_rows.items() if datetime.fromtimestamp(t,timezone.utc).date()<today],key=lambda r:r[0])
    if len(candles)<201: raise ValueError(f'Coinbase candles: only {len(candles)} completed daily bars (need 201)')
    close=[float(x[4]) for x in candles]
    volume=[float(x[5]) for x in candles]
    avg30=sum(volume[-31:-1])/30
    return {'last_close':close[-1], 'sma50':sum(close[-50:])/50,
            'sma200':sum(close[-200:])/200, 'return_7d':(close[-1]/close[-8]-1)*100,
            'relative_volume':volume[-1]/avg30 if avg30 else None,
            'as_of':datetime.fromtimestamp(candles[-1][0],timezone.utc).isoformat(),
            'source':'Coinbase Exchange',
            'close_series':[{'date':datetime.fromtimestamp(r[0],timezone.utc).strftime('%Y-%m-%d'),
                             'close':float(r[4])} for r in candles[-120:]]}


def coinbase_recent_simple():
    """Fallback price data if full 200-day candles cannot be retrieved.
    No SMA200 is faked; the engine leaves price/spot scores unscored.
    """
    raw=get_json('https://api.exchange.coinbase.com/products/BTC-USD/candles',{'granularity':86400})
    if not isinstance(raw,list): raise ValueError('Invalid Coinbase data')
    candles=sorted([r for r in raw if isinstance(r,list) and len(r)>=6 and
                    datetime.fromtimestamp(int(r[0]),timezone.utc).date()<now_utc().date()],key=lambda r:r[0])
    if len(candles)<9: raise ValueError('Not enough Coinbase fallback candles')
    close=[float(x[4]) for x in candles]
    return {'last_close':close[-1], 'return_7d':(close[-1]/close[-8]-1)*100,
            'as_of':datetime.fromtimestamp(candles[-1][0],timezone.utc).isoformat(),
            'source':'Coinbase Exchange (short history only)',
            'close_series':[{'date':datetime.fromtimestamp(r[0],timezone.utc).strftime('%Y-%m-%d'),
                            'close':float(r[4])} for r in candles[-120:]]}


def _funding_from_binance():
    rows=get_json('https://fapi.binance.com/fapi/v1/fundingRate',{'symbol':'BTCUSDT','limit':100})
    recent=[r for r in rows if int(r['fundingTime']) >= int((now_utc()-timedelta(days=3)).timestamp()*1000)]
    if not recent: raise ValueError('Funding data are stale')
    return {'funding_3d_avg_pct':sum(float(r['fundingRate'])*100 for r in recent)/len(recent),
            'funding_as_of':datetime.fromtimestamp(int(recent[-1]['fundingTime'])/1000,timezone.utc).isoformat(),
            'funding_venue':'Binance BTCUSDT perpetual'}


def _funding_from_okx():
    obj=get_json('https://www.okx.com/api/v5/public/funding-rate-history',{'instId':'BTC-USDT-SWAP','limit':'100'})
    if obj.get('code')!='0': raise ValueError('OKX returned nonzero status')
    rows=obj.get('data',[])
    recent=[r for r in rows if int(r['fundingTime']) >= int((now_utc()-timedelta(days=3)).timestamp()*1000)]
    if not recent: raise ValueError('OKX funding data are stale')
    return {'funding_3d_avg_pct':sum(float(r['fundingRate'])*100 for r in recent)/len(recent),
            'funding_as_of':datetime.fromtimestamp(max(int(r['fundingTime']) for r in recent)/1000,timezone.utc).isoformat(),
            'funding_venue':'OKX BTC-USDT-SWAP'}


def _oi_from_binance():
    rows=get_json('https://fapi.binance.com/futures/data/openInterestHist',{'symbol':'BTCUSDT','period':'1d','limit':12})
    points=sorted([r for r in rows if datetime.fromtimestamp(int(r['timestamp'])/1000,timezone.utc).date()<now_utc().date()],
                  key=lambda r:int(r['timestamp']))
    if len(points)<8: raise ValueError('Fewer than 8 Binance OI observations')
    base=float(points[-8]['sumOpenInterestValue']); current=float(points[-1]['sumOpenInterestValue'])
    if base<=0: raise ValueError('Invalid OI baseline')
    return {'oi_change_7d_pct':(current/base-1)*100,
            'oi_as_of':datetime.fromtimestamp(int(points[-1]['timestamp'])/1000,timezone.utc).isoformat(),
            'oi_venue':'Binance BTCUSDT perpetual (USD notional)'}


def _oi_from_bybit():
    obj=get_json('https://api.bybit.com/v5/market/open-interest',
                 {'category':'linear','symbol':'BTCUSDT','intervalTime':'1d','limit':12})
    if obj.get('retCode')!=0: raise ValueError('Bybit returned nonzero status')
    points=sorted(obj.get('result',{}).get('list',[]),key=lambda r:int(r['timestamp']))
    points=[r for r in points if datetime.fromtimestamp(int(r['timestamp'])/1000,timezone.utc).date()<now_utc().date()]
    if len(points)<8: raise ValueError('Fewer than 8 Bybit OI observations')
    base=float(points[-8]['openInterest']); current=float(points[-1]['openInterest'])
    if base<=0: raise ValueError('Invalid OI baseline')
    return {'oi_change_7d_pct':(current/base-1)*100,
            'oi_as_of':datetime.fromtimestamp(int(points[-1]['timestamp'])/1000,timezone.utc).isoformat(),
            'oi_venue':'Bybit BTCUSDT perpetual (contract OI units)'}


def derivatives():
    result={}; issues=[]
    for label, choices in [('funding',[_funding_from_binance,_funding_from_okx]),
                           ('OI',[_oi_from_binance,_oi_from_bybit])]:
        worked=False
        for fn in choices:
            try:
                result.update(fn()); worked=True; break
            except (ValueError, TypeError, KeyError, requests.RequestException) as e:
                issues.append(f'{label}/{getattr(fn, '__name__', 'feed')}: {type(e).__name__} {str(e)[:90]}')
        if not worked:
            issues.append(f'{label} unavailable on all configured exchanges')
    if not result: raise ValueError('; '.join(issues))
    stamps=[result.get('funding_as_of'),result.get('oi_as_of')]
    result['as_of']=max(s for s in stamps if s)
    result['source']='; '.join([result[k] for k in ('funding_venue','oi_venue') if k in result])
    result['warnings']=[i for i in issues if i.endswith('all configured exchanges')]
    return result


_DATE_FARSIDE=re.compile(r'^\s*(\d{1,2}\s+[A-Za-z]{3}\s+20\d\d)\s*$')
_DATE_TFTC=re.compile(r'^\s*([A-Za-z]{3}\s+\d{1,2},?\s+20\d\d)\s*$')


def _flow_number(text):
    """Return None for pending cells, not zero. Parentheses are negative."""
    s=unescape(text).strip().replace('\u2212','-').replace('−','-').replace('$','').replace(',','')
    s=re.sub(r'\s*(?:US\$|USD|M|m)\s*$', '', s)
    if s in ('','-','—','N/A','*'): return None
    negative=s.startswith('(') and s.endswith(')')
    if negative: s=s[1:-1].strip()
    try: return float(s)*(-1 if negative else 1)
    except ValueError: return None


def _parse_farside_html(html):
    soup=BeautifulSoup(html,'html.parser')
    result={}
    for tr in soup.find_all('tr'):
        cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])]
        if len(cells)<3:continue
        date_match=_DATE_FARSIDE.match(cells[0]);
        if not date_match:continue
        day=datetime.strptime(date_match.group(1),'%d %b %Y').date()
        # A pending Farside row may report 0.0 in Total while every fund is '-'.
        fund_values=[_flow_number(s) for s in cells[1:-1]]
        if not any(v is not None for v in fund_values):continue
        total=_flow_number(cells[-1])
        if total is None:continue
        result[day]=round(total,3)
    return result


def _parse_tftc_html(html):
    soup=BeautifulSoup(html,'html.parser')
    result={}
    for tr in soup.find_all('tr'):
        cells=[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])]
        if len(cells)<3:continue
        m=_DATE_TFTC.match(cells[0])
        if not m:continue
        try:day=datetime.strptime(m.group(1).replace(',',''),'%b %d %Y').date()
        except ValueError:continue
        values=[_flow_number(x) for x in cells[1:-1]]
        total=_flow_number(cells[-1])
        if total is None or not any(v is not None for v in values):continue
        result[day]=round(total,3)
    return result


def _aggregate_flows(rows, source_url, label):
    if len(rows)<5: raise ValueError(f'{label}: fewer than five completed trading days')
    ordered=sorted(rows.items())
    last_date=ordered[-1][0]
    if last_date>now_utc().date(): raise ValueError('Future-dated ETF observations')
    # US market holidays and weekends are normal. 6 days allows extended weekends.
    if (now_utc().date()-last_date).days>6:
        raise ValueError(f'{label}: latest date {last_date} is stale')
    recent5=ordered[-5:]
    week_start=last_date-timedelta(days=6)
    recent_calendar=[(d,v) for d,v in ordered if d>=week_start]
    return {'net_5d_usdm':round(sum(v for _,v in recent5),2),
            'net_7calendar_usdm':round(sum(v for _,v in recent_calendar),2),
            'as_of':datetime.combine(last_date,datetime.min.time(),tzinfo=timezone.utc).isoformat(),
            'source':f'{label} ({source_url})',
            'recent_sessions':[{'date':d.isoformat(),'flow_usdm':v} for d,v in ordered[-12:]],
            'last_completed_session':last_date.isoformat(),
            'last_5_session_count':len(recent5),'last_7calendar_session_count':len(recent_calendar)}


def etf_flows():
    errors=[]
    for label, url, parser in [
        ('Farside Investors','https://farside.co.uk/btc/',_parse_farside_html),
        ('TFTC tracker','https://www.tftc.io/bitcoin-etf-flows',_parse_tftc_html)]:
        try:
            page=get_response(url).text
            return _aggregate_flows(parser(page), url, label)
        except (requests.RequestException, ValueError, TypeError) as e:
            errors.append(f'{label}: {type(e).__name__} ({str(e)[:100]})')
    raise ValueError('ETF data unavailable: '+'; '.join(errors))


def glassnode_onchain(api_key):
    """Try premium metrics separately so one restricted series does not erase another.

    Glassnode subscription/access restrictions differ by metric. Caller is told
    if no premium metric could be retrieved; partial results remain available.
    """
    endpoints={'asopr':'indicators/sopr_adjusted','whale':'entities/supply_balance_1k_10k'}
    gathered={}; stamps=[]; warnings=[]
    for name,path in endpoints.items():
        try:
            rows=get_json('https://api.glassnode.com/v1/metrics/'+path,
                          {'a':'BTC','i':'24h','api_key':api_key})
            rows=[x for x in rows if isinstance(x.get('v'),(int,float))]
            needed=32 if name=='whale' else 3
            if len(rows)<needed:raise ValueError('Insufficient Glassnode history for '+name)
            rows.sort(key=lambda r:r['t']);gathered[name]=rows
            stamps.append(int(rows[-1]['t']))
        except (requests.RequestException,ValueError,TypeError,KeyError) as exc:
            warnings.append(f'{name}: {type(exc).__name__}')
    if not gathered:
        raise ValueError('No premium Glassnode metrics accessible: '+', '.join(warnings))
    result={'as_of':datetime.fromtimestamp(min(stamps),timezone.utc).isoformat(),
            'source':'Glassnode API ('+', '.join(gathered.keys())+')', 'warnings':warnings}
    if 'asopr' in gathered:
        result['asopr']=float(gathered['asopr'][-1]['v'])
    if 'whale' in gathered:
        series=gathered['whale']
        base=float(series[-31]['v'])
        if base>0:result['whale_1k_10k_30d_pct']=(float(series[-1]['v'])/base-1)*100
    return result


def fetch_all(glassnode_key=''):
    """Fetch independent connectors concurrently; fail closed with per-module diagnostics.

    No source can silently become a zero or inherit data from another module.
    """
    market={};extra={};errors=[];statuses={}
    checks=[('price',coinbase_daily),('derivatives',derivatives),
            ('etf',etf_flows),('macro',macro_public),
            ('onchain',onchain_community),('spot_cvd',automatic_spot_cvd)]
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures={pool.submit(fn):name for name,fn in checks}
        for future in as_completed(futures):
            name=futures[future]
            try:
                data=future.result()
                if name=='spot_cvd':extra.update(data)
                elif name in ('etf','macro','onchain'):extra[name]=data
                else:market[name]=data
                status='CONNECTED'
                if name=='macro' and data.get('macro_quality',0)<.99:status='PARTIAL'
                if name=='onchain' and not all(k in data for k in ('mvrv','sopr','whale_1k_10k_30d_pct')):status='PARTIAL'
                if name=='spot_cvd' and data.get('spot_cvd_quality',0)<.99:status='PARTIAL'
                if name=='spot_cvd':
                    detail=(f"7D {data['spot_cvd_7d_usd']/1e6:+,.2f}M USDT; "
                            f"buy share {data['spot_cvd_buy_share_pct']:.1f}%; "
                            f"mode={data.get('spot_cvd_mode','unknown')}; "
                            f"{data.get('spot_cvd_note','')}")
                    source=data['spot_cvd_source']; stamp=data['spot_cvd_as_of']
                else:
                    detail=data.get('explanation','Provider returned usable data.')
                    if name in ('macro','onchain') and data.get('issues'):
                        detail+=' | Other component errors: '+'; '.join(data.get('issues',[]))[:300]
                    source=data.get('source','');stamp=data.get('as_of','')
                statuses[name]={'status':status,'source':source,
                                'as_of':stamp,'detail':detail}
            except (requests.RequestException, ValueError, TypeError, KeyError, ZeroDivisionError,
                    OSError) as exc:
                status=f'{type(exc).__name__}: {str(exc)[:420]}'
                statuses[name]={'status':'ERROR','source':'','as_of':'','detail':status}
                errors.append(f'{name}: {status}')

    # Price rescue only applies to price; never serves as spot aggressor CVD.
    if 'price' not in market:
        try:
            px=coinbase_recent_simple();market['price']=px
            statuses['price']={'status':'PARTIAL','source':px['source'],'as_of':px['as_of'],
                               'detail':'Price available; full SMA history unavailable.'}
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            errors.append(f'price fallback: {type(exc).__name__}: {str(exc)[:120]}')

    if glassnode_key:
        try:
            premium=glassnode_onchain(glassnode_key)
            old=extra.get('onchain',{})
            merged={**old,**premium}
            merged['source']=(old.get('source','')+'; '+premium['source']).strip('; ')
            merged['as_of']=min(filter(None,[old.get('as_of',''),premium['as_of']]))
            extra['onchain']=merged
            statuses['onchain']={'status':'PARTIAL','source':merged['source'],
                                 'as_of':merged['as_of'],
                                 'detail':'Community + authenticated premium signals. Full whale/exchange coverage requires verification.'}
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            statuses.setdefault('onchain',{'status':'ERROR','source':'','as_of':'','detail':''})
            statuses['onchain']['detail']+=' | Glassnode premium failed: '+type(exc).__name__
    return market,extra,errors,statuses
