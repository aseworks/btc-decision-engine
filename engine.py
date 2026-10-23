"""Transparent BTC decision framework (research prototype, not investment advice).
All thresholds and weights are editable hypotheses, NOT backtested strategy parameters.
"""
from __future__ import annotations
BUILD_ID = "v0.7.1-20261024-r2"

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

WEIGHTS = {'On-chain': 25, 'Spot demand':20, 'Derivatives':20,
           'Macro':15, 'Fund flows':10, 'Price structure':10}

@dataclass
class Pillar:
    name: str
    score: Optional[float]
    quality: float
    evidence: str
    source: str
    as_of: str
    weight: float = 0

    def as_json(self):
        return asdict(self)


def clip(x, lo=-2, hi=2):
    return max(lo, min(hi, x))


def _age_days(stamp, now):
    if not stamp:
        return 999
    try:
        when = datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (now-when).total_seconds()/86400
    except (ValueError, TypeError):
        return 999


def _fresh_factor(stamp, max_days, now):
    age = _age_days(stamp, now)
    return 1.0 if -1 <= age <= max_days else 0.0


def build_pillars(market, extra=None, now=None):
    """market: fetched dict (may be empty). extra: user or API-sourced supplemental signals.
    Source and as_of dates must accompany all non-live inputs.
    """
    extra=extra or {}
    now=now or datetime.now(timezone.utc)
    results=[]

    # Daily price candles are *completed* UTC daily candles.
    px=market.get('price', {})
    price_score=None; price_e='Tidak ada OHLCV valid.'
    price_asof=px.get('as_of','')
    if px.get('last_close') and _fresh_factor(price_asof, 2, now):
        close=px['last_close']; sma50=px.get('sma50'); sma200=px.get('sma200')
        if sma50 and sma200:
            score = (1 if close>sma50 else -1) + (1 if sma50>sma200 else -1)
            price_score=float(score)
            price_e=f'Harga penutupan ${close:,.0f}; SMA50 ${sma50:,.0f}; SMA200 ${sma200:,.0f}'
    results.append(Pillar('Price structure',price_score, 1 if price_score is not None else 0,price_e,
                          'Coinbase Exchange BTC-USD daily candles',price_asof))

    # Spot demand: price/volume is a low-grade proxy; verified CVD improves source quality.
    spot_score=None; quality=0; evidence='Spot CVD tidak tersedia; volume proxy tidak tersedia.'
    spot_asof=px.get('as_of','')
    spot_source='Coinbase Exchange volume proxy'
    if px.get('relative_volume') is not None and px.get('return_7d') is not None and _fresh_factor(spot_asof,2,now):
        rel=px['relative_volume']; ret=px['return_7d']
        spot_score = (1 if ret>2 else (-1 if ret<-2 else 0)) * (1.5 if rel>=1.2 else 0.5)
        quality=.45
        evidence=f'7D price return {ret:+.1f}%; volume/30D average {rel:.2f}× (PROXY, bukan CVD)'
    cvd=extra.get('spot_cvd_7d_usd')
    cvd_date=extra.get('spot_cvd_as_of')
    if cvd is not None and _fresh_factor(cvd_date,3,now):
        cvd_score=(1 if cvd>0 else (-1 if cvd<0 else 0))
        spot_score=(0.3*spot_score if spot_score is not None else 0)+1.7*cvd_score
        quality=float(max(0,min(1,extra.get('spot_cvd_quality',1.0))))
        evidence+=f' | 7D venue spot taker CVD {cvd/1e6:+,.1f}M USDT (executed trade-side quote volume in USDT, bukan OHLC proxy)'
        spot_source=extra.get('spot_cvd_source','User-sourced spot CVD')
        spot_asof=cvd_date
    results.append(Pillar('Spot demand',clip(spot_score) if spot_score is not None else None, quality, evidence,spot_source,spot_asof))

    # Derivatives use independent freshness per metric; failed OI must not erase funding.
    dv=market.get('derivatives',{})
    dscore=None; dquality=0; de=[]; comps=[]
    funding_date=dv.get('funding_as_of',dv.get('as_of',''))
    oi_date=dv.get('oi_as_of',dv.get('as_of',''))
    if dv.get('funding_3d_avg_pct') is not None and _fresh_factor(funding_date,2,now):
        f=dv['funding_3d_avg_pct']
        comps.append(-1 if f>.035 else (.5 if -.015<=f<=.01 else 0))
        de.append(f'Funding/event {f:+.4f}% ({dv.get("funding_venue","unknown venue")})')
    if (dv.get('oi_change_7d_pct') is not None and px.get('return_7d') is not None
            and _fresh_factor(oi_date,2,now)):
        oi=dv['oi_change_7d_pct']; ret=px['return_7d']
        comps.append(-1 if oi>8 and ret<-2 else (-.75 if oi>8 and ret>4 else (.5 if oi<-5 and ret<-2 else 0)))
        de.append(f'OI 7D {oi:+.1f}% ({dv.get("oi_venue","unknown venue")})')
    if comps:
        dscore=clip(sum(comps));dquality=len(comps)/2
    results.append(Pillar('Derivatives',dscore,dquality,'; '.join(de) if de else 'Funding/OI tidak tersedia atau kadaluarsa',
                          dv.get('source','Binance/OKX/Bybit public APIs'),dv.get('as_of','')))

    # On-chain: real metrics only. Paid whale data is NOT replaceable by tx counts.
    o=extra.get('onchain',{})
    oc=[]; ev=[]; quality=0.0
    stamp=o.get('as_of','')
    if _fresh_factor(stamp,7,now):
        if o.get('whale_1k_10k_30d_pct') is not None:
            z=float(o['whale_1k_10k_30d_pct'])
            oc.append((1 if z>.5 else (-1 if z<-.5 else 0),.35));quality+=.35
            ev.append(f'Entity-adjusted whale supply 30D {z:+.2f}%')
        if o.get('asopr') is not None:
            z=float(o['asopr'])
            oc.append((1 if z>1.01 else (-1 if z<.99 else 0),.20));quality+=.20
            ev.append(f'aSOPR {z:.4f}')
        if o.get('exchange_netflow_7d_btc') is not None:
            z=float(o['exchange_netflow_7d_btc'])
            oc.append((1 if z<-2500 else (-1 if z>2500 else 0),.15));quality+=.15
            ev.append(f'Exchange netflow 7D {z:+,.0f} BTC')
        if o.get('mvrv') is not None:
            z=float(o['mvrv'])
            oc.append((1 if z<1.5 else (-1 if z>3.5 else 0),.25));quality+=.25
            ev.append(f'MVRV {z:.3f} (valuation proxy, not whale holdings)')
        if o.get('sopr') is not None:
            z=float(o['sopr'])
            oc.append((1 if 1.00<z<1.05 else (-1 if z<.98 else 0),.15));quality+=.15
            ev.append(f'Raw SOPR {z:.4f} (not aSOPR)')
        if o.get('active_address_7d_pct') is not None:
            z=float(o['active_address_7d_pct'])
            # Activity is secondary and lower conviction than entity-clustered holdings.
            oc.append((.3 if z>5 else (-.3 if z<-5 else 0),.1));quality+=.10
            ev.append(f'Active addresses 7D vs prior 14D {z:+.1f}% (activity only)')
        if o.get('chain_tx_7d_pct') is not None:
            z=float(o['chain_tx_7d_pct'])
            # Transactions do not establish buying or accumulation; cap its influence.
            oc.append((0,.06));quality+=.06
            ev.append(f'BTC transaction count trend {z:+.1f}% (context only, no directional score)')
    oscore=1.6*sum(val*w for val,w in oc)/sum(w for _,w in oc) if oc else None
    results.append(Pillar('On-chain',clip(oscore) if oscore is not None else None,min(1,quality),
                          '; '.join(ev) if ev else 'No fresh ledger metric; premium whale data not connected',
                          o.get('source','Tidak terhubung'),stamp))

    # Fund flow: 5 trading days, known observation date and source mandatory.
    ef=extra.get('etf',{})
    ef_score=None; ef_quality=0
    if ef.get('net_5d_usdm') is not None and ef.get('source') and _fresh_factor(ef.get('as_of'),7,now):
        v=ef['net_5d_usdm']; ef_score=1.5 if v>=500 else (.5 if v>=100 else (-1.5 if v<=-500 else (-.5 if v<=-100 else 0)))
        ef_quality=1
    results.append(Pillar('Fund flows',ef_score,ef_quality,
                          (f'5 sesi ETF ${ef["net_5d_usdm"]:+,.1f}M; '+(f'7 hari kalender ${ef["net_7calendar_usdm"]:+,.1f}M; ' if ef.get('net_7calendar_usdm') is not None else '7 hari kalender belum tersedia; ')+f'sumber {ef.get("source","")}' if ef_score is not None else 'Sumber ETF belum terhubung, data kedaluwarsa, atau belum terverifikasi'),
                          ef.get('source','Tidak terhubung'),ef.get('as_of','')))

    # Macro sources: official US Treasury yield changes + optional FRED USD/credit.
    macro=extra.get('macro',{})
    mm=macro.get('regime')
    valid=mm in ('Easing','Neutral','Tightening') and macro.get('source') and _fresh_factor(macro.get('as_of'),10,now)
    if valid:
        macro_score=macro.get('macro_signal_score')
        if macro_score is None:
            macro_score={'Easing':1.5,'Neutral':0,'Tightening':-1.5}[mm]
        macro_quality=float(macro.get('macro_quality',1))
        macro_e=macro.get('explanation') or f'Rezim input terverifikasi: {mm}'
    else:macro_score=None;macro_quality=0;macro_e='Macro feeds tidak tersedia / stale'
    results.append(Pillar('Macro',macro_score,macro_quality,macro_e,
                          macro.get('source','Tidak terhubung'),macro.get('as_of','')))
    for p in results:
        p.weight=WEIGHTS[p.name]
    return results



def coverage_breakdown(pillars, target=70.0, onchain_target=.66, spot_target=.95):
    """Audit the weighted *data availability*, NOT predictive accuracy.

    Only non-stale directional observations count; ``quality`` is estimated
    input completeness and should not be confused with calibration.
    """
    if not 0 <= target <= 100:
        raise ValueError('coverage target must be between 0 and 100')
    detail=[]
    total=0.0
    for p in pillars:
        usable=p.score is not None and p.quality>0
        quality=min(1.0,max(0.0,float(p.quality))) if usable else 0.0
        contributed=round(p.weight*quality,3)
        missed=round(p.weight-contributed,3)
        total+=contributed
        detail.append({'pillar':p.name,'weight':p.weight,'quality':round(quality,4),
                       'contribution':contributed,'potential_gain':missed,
                       'observed':usable,'source':p.source,'as_of':p.as_of})
    total=round(total,2)
    gap=round(max(0.0,target-total),2)
    onchain=next((d for d in detail if d['pillar']=='On-chain'),None)
    spot=next((d for d in detail if d['pillar']=='Spot demand'),None)
    gains=[]
    if gap>0:
        for d in sorted(detail,key=lambda d:d['potential_gain'],reverse=True):
            if d['potential_gain']>0.01:
                gains.append({'pillar':d['pillar'],'possible_additional_points':d['potential_gain'],
                              'can_clear_total_gap_alone':d['potential_gain']>=gap})
    requirements=[]
    if gap>0:
        requirements.append({'code':'coverage','label':f'Perlu +{gap:.1f} poin coverage tertimbang menuju {target:.0f}%',
                             'need':gap})
    if onchain and onchain['quality']<onchain_target:
        d=round((onchain_target-onchain['quality'])*onchain['weight'],2)
        requirements.append({'code':'onchain','label':f'On-chain {onchain["quality"]:.0%} dari target {onchain_target:.0%} (ekuivalen +{d:.1f} poin coverage total)',
                             'need':round(onchain_target-onchain['quality'],3)})
    if spot and spot['quality']<spot_target:
        requirements.append({'code':'spot','label':f'Spot CVD {spot["quality"]:.0%} dari target {spot_target:.0%}; arsip tertunda ≠ konfirmasi live',
                             'need':round(spot_target-spot['quality'],3)})
    return {'coverage':total,'target':target,'gap':gap,'pillars':detail,'opportunities':gains,
            'unmet_data_requirements':requirements,
            'interpretation':'coverage = completeness of weighted fresh metrics; NOT probability, confidence interval, or model accuracy'}


def evaluate(pillars, history=None, now=None):
    history=history or []
    now=now or datetime.now(timezone.utc)
    observed=[p for p in pillars if p.score is not None and p.quality>0]
    covered_weight=sum(p.weight*p.quality for p in observed)
    weighted=sum(p.weight*p.quality*p.score for p in observed)
    score=round(max(0,min(100,50+25*(weighted/covered_weight))),1) if covered_weight else None
    coverage=round(covered_weight,1)
    pos=sum(p.score>0.25 for p in observed); neg=sum(p.score < -.25 for p in observed)
    conflict=pos>=2 and neg>=2
    onchain=next(p for p in pillars if p.name=='On-chain')
    spot=next(p for p in pillars if p.name=='Spot demand')
    macro=next(p for p in pillars if p.name=='Macro')
    price=next(p for p in pillars if p.name=='Price structure')
    if score is None:
        regime='NO DATA'; candidate='TIDAK ADA KEPUTUSAN'
    elif score>=64:
        regime='POTENTIAL ACCUMULATION / MARKUP'
        candidate='ACCUMULATE BERTAHAP' if score>=72 else 'DCA TERBATAS'
    elif score<=36:
        regime='DISTRIBUTION / MARKDOWN RISK'
        candidate='DISTRIBUTE BERTAHAP' if score<=28 else 'REDUCE RISK'
    else:
        regime='TRANSITION / RANGE'
        candidate='WAIT / HOLD'
    # Hard-gates: preliminary scores never become executable directions without broad evidence.
    gates=[]
    if coverage<70: gates.append('Cakupan data di bawah 70%')
    if onchain.quality < .66: gates.append('Cakupan on-chain belum cukup untuk sinyal aksi (target >=66%)')
    if spot.quality < .95: gates.append('Spot CVD belum terkonfirmasi; volume harga hanya proxy')
    if conflict: gates.append('Indikator saling bertentangan')
    if candidate.startswith(('ACCUMULATE','DCA')) and macro.score is not None and macro.score<0:
        gates.append('Veto: rezim makro pengetatan')
    if candidate.startswith(('ACCUMULATE','DCA')) and price.score is not None and price.score<=-1:
        gates.append('Veto: struktur harga bearish')
    # Confirmation based on TWO prior committed snapshots of same direction, and min 12h.
    old=sorted([h for h in history if h.get('candidate')==candidate and h.get('score') is not None and h.get('timestamp')], key=lambda x:x['timestamp'])
    if candidate!='WAIT / HOLD' and candidate!='TIDAK ADA KEPUTUSAN':
        if not old:
            gates.append('Belum ada konfirmasi berulang (2 snapshot terpisah >=12 jam)')
        else:
            # Only a distinct earlier observation counts; repeats within 12h do not.
            elapsed_values=[_age_days(h.get('timestamp'),now) for h in old]
            if not any(.5<=age<=7 for age in elapsed_values):
                gates.append('Perlu observasi sebelumnya berjarak 12 jam–7 hari')
    ready= not gates and candidate not in ('WAIT / HOLD','TIDAK ADA KEPUTUSAN')
    decision= candidate if ready else ('WAIT / HOLD' if score is not None else 'NO DATA')
    audit=coverage_breakdown(pillars)
    return dict(score=score,coverage=coverage,coverage_audit=audit,regime=regime,candidate=candidate,
                decision=decision,ready=ready,gates=gates,positive=pos,negative=neg,
                pillars=[p.as_json() for p in pillars])
