"""BTC Decision Intelligence v0.6 - tablet responsive / Streamlit Cloud.
No trade execution. All decision model thresholds are research heuristics.
"""
from __future__ import annotations
import html
import json
import os
from datetime import date, datetime, timezone
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
APP_BUILD_ID = 'v0.7.0-20261024-r1'
st.set_page_config(page_title='BTC Atlas · Decision Intelligence',page_icon='₿',layout='wide',initial_sidebar_state='collapsed')
try:
    import engine as _engine_module
    import sources as _sources_module
    import spot_flow as _spot_module
    import macro_onchain as _macro_module
    from engine import WEIGHTS, build_pillars, evaluate, coverage_breakdown
    from sources import fetch_all
except (ImportError, SyntaxError) as startup_error:
    st.error('DEPLOYMENT INCOMPLETE — missing or invalid modules.')
    st.code(f'{type(startup_error).__name__}: {startup_error}')
    st.warning('Upload all six v0.7 files in GitHub repository root on deployed branch, then Reboot app.')
    st.stop()

# For multi-file GitHub updates: refuse misleading mixed-version execution.
_MODULE_BUILDS = {'engine.py':getattr(_engine_module,'BUILD_ID','OLD/UNKNOWN'),
                  'sources.py':getattr(_sources_module,'BUILD_ID','OLD/UNKNOWN'),
                  'spot_flow.py':getattr(_spot_module,'BUILD_ID','OLD/UNKNOWN'),
                  'macro_onchain.py':getattr(_macro_module,'BUILD_ID','OLD/UNKNOWN')}
_mismatched={k:v for k,v in _MODULE_BUILDS.items() if v!=APP_BUILD_ID}
if _mismatched:
    st.error('FILE VERSION MISMATCH — dashboard tidak boleh dijalankan dengan modul campuran.')
    st.code('Expected: '+APP_BUILD_ID+'\n'+'\n'.join(f'{k}: {v}' for k,v in _MODULE_BUILDS.items()))
    st.warning('Upload ulang SEMUA file .py v0.7 ke root repository GitHub pada branch deploy, kemudian reboot aplikasi di Streamlit Cloud.')
    st.stop()

COLORS={'green':'#35D3A1','amber':'#FFC36A','red':'#FA747A','blue':'#60B5FF',
        'violet':'#A99CFF','text':'#E9F2FB','muted':'#91A5B7','panel':'#142435','stroke':'#263C4F'}

st.markdown('''<style>
/* One consistent dark financial terminal, larger touch targets */
.stApp, [data-testid="stAppViewContainer"] {background:#091522;color:#E9F2FB;}
.block-container{max-width:1280px;padding:1.1rem 1.2rem 3rem;}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stSidebar"]{background:#101E2D;}
h1,h2,h3{letter-spacing:-.025em;color:#F1F7FB!important;}
h3{margin-top:1.4rem;}
p, label{color:#CBDAE7;}
div[data-testid="stMetric"]{background:linear-gradient(155deg,#152738,#0F2031);border:1px solid #294252;
 border-radius:17px;padding:16px 17px;box-shadow:0 10px 26px #030C1455;}
[data-testid="stMetricLabel"]{color:#91A7BC;font-weight:600;}
[data-testid="stMetricValue"]{color:#F0F7FC;font-size:1.6rem;}
[data-testid="stTabs"] button{min-height:54px;font-weight:600;border-radius:10px;padding:.5rem 1rem;}
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{min-height:47px;border-radius:10px;
 border:1px solid #376176;font-weight:650;}
button[kind="primary"]{background:#177F70;color:#F8FFFE;border:0;}
[data-testid="stExpander"]{border:1px solid #2A4154!important;border-radius:14px!important;background:#101F2E;}
[data-testid="stDataFrame"]{border:1px solid #263E51;border-radius:10px;}
.headerbar {background:radial-gradient(circle at 80% -20%,#1C726B99,transparent 45%),
 linear-gradient(110deg,#142638,#143C45 70%,#102F43);
 border:1px solid #2B5C63;border-radius:23px;padding:20px 23px;margin-bottom:17px;}
.brandline{color:#8EE6CE;letter-spacing:.15em;text-transform:uppercase;font-weight:800;font-size:11px;}
.headerbar h1{font-size:2.2rem;line-height:1.1;margin:10px 0 10px;color:#FCFFFD!important;}
.headerbar p{font-size:.87rem;color:#B8D9DB;margin:0;}
.eyebrow{font-size:.7rem;font-weight:800;letter-spacing:.12em;color:#98AEBE;text-transform:uppercase;}
.decision{border:1px solid #33566B;border-radius:17px;background:linear-gradient(125deg,#172D3D,#102534);
 padding:17px 19px;min-height:156px;}
.decision h2{font-size:1.55rem;margin:.4rem 0 .7rem;}
.pill {display:inline-block;border-radius:20px;padding:4px 11px;font-weight:750;font-size:.74rem;}
.pill-positive{background:#164F44;color:#67EFC7;}.pill-negative{background:#562E3A;color:#FFA4A3;}
.pill-neutral{background:#5A4429;color:#FFCD77;}.pill-unavailable{background:#32465A;color:#AFC6D8;}
.minitext{color:#9DB1C3;font-size:.79rem;line-height:1.5;}
.signalrow{background:#122335;border:1px solid #243D50;border-radius:14px;padding:13px 15px;margin:6px 0;}
.sig-top{display:flex;align-items:center;justify-content:space-between;font-weight:700;gap:8px;}
.sig-track{background:#294253;border-radius:10px;height:7px;overflow:hidden;margin-top:11px;}
.sig-fill{height:7px;border-radius:10px;}
.callout{padding:12px 15px;background:#142938;border-left:3px solid #46BAAB;border-radius:10px;color:#D9EAF5;font-size:.88rem;}
.graph-title{color:#E7F5FA;font-size:1.05rem;font-weight:780;margin-top:8px;}
@media(max-width:780px){.block-container{padding:.65rem .75rem 2rem;}
 .headerbar{padding:17px;}.headerbar h1{font-size:1.6rem;}
 [data-testid="stHorizontalBlock"]{gap:.6rem;}
 [data-testid="stMetricValue"]{font-size:1.28rem;}
 div[data-testid="stMetric"]{padding:11px;}
 [data-testid="stTabs"] button{min-height:49px;padding:.25rem .55rem;font-size:.83rem;}
 .decision{min-height:135px;padding:14px;}}
</style>''',unsafe_allow_html=True)


def esc(s):return html.escape(str(s),quote=True)
def nowutc():return datetime.now(timezone.utc)
def stamp(d):return d.isoformat()+'T12:00:00+00:00'
def secret():
    try:return st.secrets.get('GLASSNODE_API_KEY','') or os.environ.get('GLASSNODE_API_KEY','')
    except (FileNotFoundError,OSError,KeyError):return os.environ.get('GLASSNODE_API_KEY','')

@st.cache_data(ttl=1200,show_spinner=False)
def live_fetch(key):return fetch_all(key)

def badge(status):
    k='positive' if status=='CONNECTED' else ('neutral' if status in ('PARTIAL','INPUT AVAILABLE','MANUAL') else 'negative' if status=='ERROR' else 'unavailable')
    return f'<span class="pill pill-{k}">{esc(status)}</span>'

def plot_style(fig,h=300):
    fig.update_layout(template='plotly_dark',height=h,margin=dict(t=15,b=30,l=7,r=7),
        paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family='Arial',color='#C4D4E1',size=11),hovermode='x unified',
        xaxis=dict(showgrid=False,zeroline=False),
        yaxis=dict(showgrid=True,gridcolor='#22394B',zeroline=False),
        showlegend=True,legend=dict(orientation='h',y=1.16,x=0))
    return fig

def chart_plot(fig,key):st.plotly_chart(fig,use_container_width=True,config={'displaylogo':False,'scrollZoom':False},key=key)

def signal_color(p):
    if p.score is None:return COLORS['muted']
    if p.score>.25:return COLORS['green']
    if p.score<-.25:return COLORS['red']
    return COLORS['amber']

def score_chart(score):
    v=float(score if score is not None else 0)
    color=COLORS['green'] if v>=64 else COLORS['red'] if v<=36 else COLORS['amber']
    f=go.Figure(go.Indicator(mode='gauge+number',value=v,number={'suffix':' / 100','font':{'size':29,'color':'#F1F8FF'}},
        gauge={'axis':{'range':[0,100],'tickwidth':0,'showticklabels':False},
               'bar':{'color':color,'thickness':.22},'bgcolor':'#213748','borderwidth':0,
               'steps':[{'range':[0,36],'color':'#492933'},
                        {'range':[36,64],'color':'#55442A'},
                        {'range':[64,100],'color':'#174F48'}]}))
    f.update_layout(height=230,margin=dict(t=16,l=22,r=22,b=18),paper_bgcolor='rgba(0,0,0,0)',font_color='white')
    return f


def price_chart(rows,sma50=None,sma200=None,n=90):
    df=pd.DataFrame(rows).tail(n)
    if df.empty:return None
    x=df['date']; y=df['close']
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=x,y=y,name='BTC daily close',mode='lines',line={'color':COLORS['green'],'width':3},
                             fill='tozeroy',fillcolor='rgba(53,211,161,0.065)'))
    # SMA indicators are displayed as current reference lines, not fabricated history.
    if sma50:
        fig.add_hline(y=sma50,line_dash='dot',line_color=COLORS['amber'],
                      annotation_text='SMA50 (latest)',annotation_position='top right')
    if sma200:
        fig.add_hline(y=sma200,line_dash='dot',line_color=COLORS['violet'],
                      annotation_text='SMA200 (latest)',annotation_position='bottom right')
    fig=plot_style(fig,310)
    fig.update_yaxes(tickprefix='$',tickformat=',.0f',rangemode='normal')
    return fig


def etf_chart(rows):
    df=pd.DataFrame(rows)
    if df.empty:return None
    fig=go.Figure(go.Bar(x=df['date'],y=df['flow_usdm'],marker_color=[COLORS['green'] if x>=0 else COLORS['red'] for x in df['flow_usdm']],
                         hovertemplate='%{x}: $%{y:+,.1f}M<extra></extra>'))
    fig=plot_style(fig,260)
    fig.update_layout(showlegend=False)
    fig.update_yaxes(ticksuffix='M',zeroline=True,zerolinecolor='#6A8293')
    return fig


def two_axis_lines(series, labels, title):
    fig=go.Figure()
    palette=[COLORS['blue'],COLORS['amber'],COLORS['violet']]
    for i,(name,data) in enumerate(series.items()):
        if not data:continue
        fig.add_trace(go.Scatter(x=[d['date'] for d in data],y=[d['value'] for d in data],
                                 name=labels.get(name,name),line=dict(color=palette[i%3],width=2)))
    return plot_style(fig,245)


def parse_upload(file,expected=list):
    try:
        text=file.getvalue().decode('utf8')
        obj=json.loads(text) if text.lstrip().startswith('[') or expected is dict else [json.loads(x) for x in text.splitlines() if x.strip()]
        if not isinstance(obj,expected):raise ValueError(f'Expected {expected.__name__}')
        return obj
    except (UnicodeDecodeError,ValueError,TypeError) as exc:
        st.error(f'Upload gagal: {exc}');return None

if 'manual' not in st.session_state:st.session_state.manual={}
if 'history' not in st.session_state:st.session_state.history=[]

st.markdown('''<div class="headerbar"><div class="brandline">₿ &nbsp; BTC ATLAS / MARKET INTELLIGENCE &nbsp; • &nbsp; v0.7</div>
<h1>Decision Intelligence</h1><p>Evidence over narrative &nbsp; · &nbsp; On-chain + Macro + Spot + Derivatives + ETF &nbsp; · &nbsp; Tablet Edition</p></div>''',unsafe_allow_html=True)

l,r=st.columns([1,3],vertical_alignment='center')
with l:
    if st.button('↻ Refresh live data',use_container_width=True,type='primary'):
        live_fetch.clear();st.rerun()
with r:st.caption('Public data refreshed on demand · cache 20 min · All timestamps UTC · no trade execution')

market, extra, errors, statuses=live_fetch(secret())
extra=dict(extra)
manual=dict(st.session_state.manual)
# v0.6 disallows legacy manual CVD overriding genuine venue execution data.
for _key in ('spot_cvd_7d_usd','spot_cvd_source','spot_cvd_as_of'):
    manual.pop(_key,None)
for k,v in manual.items():
    if k in ('etf','macro','onchain') and k in extra and isinstance(v,dict):
        # On-chain complementary inputs merge with community; preserve freshest value per metric only through user intent.
        if k=='onchain':
            auto=extra['onchain']
            extra[k]={**auto,**v,'source':(auto.get('source','')+'; manual '+v.get('source','')).strip('; ')}
            extra[k]['as_of']=min(filter(None,[auto.get('as_of',''),v.get('as_of','')]))
        elif str(v.get('as_of','')) >= str(extra[k].get('as_of','')):
            extra[k]=v
        else:continue
    else:extra[k]=v
    statuses[k]={'status':'INPUT AVAILABLE','source':v.get('source','Manual') if isinstance(v,dict) else 'Manual',
                 'as_of':v.get('as_of','') if isinstance(v,dict) else '',
                 'detail':'Manual data supplement (must verify independently)'}

pillars=build_pillars(market,extra)
result=evaluate(pillars,st.session_state.history)
audit=result["coverage_audit"]
score=result['score'];coverage=result['coverage'];gates=result['gates']
onchain=next(p for p in pillars if p.name=='On-chain')
macro=next(p for p in pillars if p.name=='Macro')
px=market.get('price',{});etf=extra.get('etf',{})

# Use separate content views; inactive chart-heavy views are not rendered.
current=st.segmented_control('Navigation', ['Overview','Signals','Macro + On-chain','Data Studio','Settings'],default='Overview',
                             label_visibility='collapsed',key='page')

if current=='Overview':
    a,b,c,d=st.columns(4)
    a.metric('BTC DAILY CLOSE', f'${px["last_close"]:,.0f}' if px.get('last_close') else 'Unavailable')
    b.metric('MODEL SCORE', f'{score:.1f} / 100' if score is not None else 'N/A')
    c.metric('DATA COVERAGE',f'{coverage:.0f}%')
    d.metric('ETF · 5 SESSIONS',f'${etf["net_5d_usdm"]:+,.1f}M' if etf.get('net_5d_usdm') is not None else 'Unavailable')
    left,right=st.columns([1,1.25],gap='medium')
    with left:
        st.markdown('<div class="graph-title">Market composite</div>',unsafe_allow_html=True)
        chart_plot(score_chart(score),key='score-main')
    with right:
        tone='positive' if result['ready'] and result['decision'].startswith(('ACCUMULATE','DCA')) else ('negative' if result['ready'] else 'neutral')
        st.markdown(f'''<div class="decision"><div class="eyebrow">DECISION • RULE BASED</div>
            <h2>{esc(result['decision'])}</h2>
            <span class="pill pill-{tone}">{'CONFIRMED PAPER SIGNAL' if result['ready'] else 'EVIDENCE GATES ACTIVE'}</span>
            <p class="minitext" style="margin-top:12px">Market regime: {esc(result['regime'])}</p></div>''',unsafe_allow_html=True)
        if gates:
            with st.expander(f'Why WAIT? · {len(gates)} risk/data gates',expanded=False):
                for gate in gates:st.write('• '+gate)
        if not result['ready'] and result['candidate'] not in ('WAIT / HOLD','TIDAK ADA KEPUTUSAN'):
            st.caption('Blocked preliminary candidate: '+result['candidate'])
    # Give concrete and mathematically grounded reasons for WAIT; do not lower
    # the 70% gate just to emit buy/sell signals.
    st.markdown('### Coverage & decision readiness')
    st.progress(min(1.0,coverage/100),text=f'Data completeness {coverage:.1f}% · research gate 70% (heuristic, not backtested)')
    if audit['gap']>0:
        st.info(f'Coverage kurang **{audit["gap"]:.1f} poin tertimbang** untuk menyentuh ambang 70%. Ini belum menyelesaikan syarat on-chain, CVD, dan konfirmasi waktu.')
    else:
        st.success('Ambang total coverage telah tercapai. Periksa syarat kualitas masing-masing pilar dan riwayat konfirmasi.')
    if not result['ready'] and result['candidate'] not in ('WAIT / HOLD','TIDAK ADA KEPUTUSAN'):
        st.markdown(f'**Research-only market bias:** `{result["candidate"]}` — **belum boleh dibaca sebagai sinyal aksi.**')
    with st.expander('Audit coverage: dari mana angka ini berasal?',expanded=True):
        _audit_rows=[]
        for _d in audit['pillars']:
            _audit_rows.append({'Pilar':_d['pillar'], 'Bobot maks (%)':_d['weight'],
                                'Kualitas data (%)':round(100*_d['quality'],1),
                                'Kontribusi coverage (poin)':round(_d['contribution'],2),
                                'Potensi tambahan (poin)':round(_d['potential_gain'],2)})
        st.dataframe(pd.DataFrame(_audit_rows),use_container_width=True,hide_index=True)
        st.caption('Jumlah kontribusi = coverage total. Kualitas data mengukur ketersediaan metrik, bukan kepastian benar.')
        if audit['unmet_data_requirements']:
            for _r in audit['unmet_data_requirements']:
                st.write('• '+_r['label'])
        if audit['opportunities']:
            _priorities=', '.join(f'{d["pillar"]} (+{d["possible_additional_points"]:.1f} maksimum)' for d in audit['opportunities'][:3])
            st.caption('Ruang perbaikan terbesar: '+_priorities)
        st.caption('On-chain dasar dari Coin Metrics dapat CONNECTED tetapi tidak cukup untuk validasi whale. Data premium *entity-adjusted* tidak boleh ditiru oleh transaction count.')
    st.markdown('### Market indicators')
    cols=st.columns(2)
    for i,p in enumerate(pillars):
        with cols[i%2]:
            val='NO DATA' if p.score is None else ('POSITIVE' if p.score>.25 else 'NEGATIVE' if p.score<-.25 else 'NEUTRAL')
            width=max(0,min(100,p.quality*100)); col=signal_color(p)
            st.markdown(f'''<div class="signalrow"><div class="sig-top"><span>{esc(p.name)}</span><span style="color:{col}">{val}</span></div>
                <div class="sig-track"><div class="sig-fill" style="width:{width:.1f}%;background:{col}"></div></div>
                <div class="minitext">Coverage {width:.0f}% &nbsp; | &nbsp; Weight {p.weight:.0f}%</div></div>''',unsafe_allow_html=True)
    st.markdown('### Price action')
    if px.get('close_series'):
        interval=st.segmented_control('Chart window',['30D','90D','120D'],default='90D',key='price_range')
        chart_plot(price_chart(px['close_series'],px.get('sma50'),px.get('sma200'),int(interval[:-1])),key='price-main')
        st.caption('Daily Coinbase candles. SMA reference lines represent latest values, not entire historical SMA curves.')
    else:st.info('Price history unavailable. Open Data Studio for connection diagnostics.')
    st.markdown('### Spot BTC ETF flows')
    if etf.get('recent_sessions'):
        chart_plot(etf_chart(etf['recent_sessions']),key='etf-main')
        st.caption(f'5 sessions ${etf.get("net_5d_usdm",0):+,.1f}M · 7 calendar days ${etf.get("net_7calendar_usdm",0):+,.1f}M · as of {etf.get("as_of","")} · Source {etf.get("source","")}')
    else:st.warning('ETF source unavailable. Missing data is not zero flow.')
    st.markdown('### Spot order flow · 7D (automatic)')
    if extra.get('spot_cvd_7d_usd') is not None:
        _cvd=extra['spot_cvd_7d_usd']
        _c1,_c2,_c3=st.columns(3)
        _c1.metric('TAKER SPOT CVD · 7D',f'{_cvd/1e6:+,.1f}M USDT')
        _c2.metric('TAKER SPOT CVD · 24H',f'{extra.get("spot_cvd_24h_usd",0)/1e6:+,.1f}M USDT' if extra.get('spot_cvd_24h_usd') is not None else 'N/A')
        _c3.metric('TAKER BUY SHARE',f'{extra["spot_cvd_buy_share_pct"]:.1f}%' if extra.get('spot_cvd_buy_share_pct') is not None else 'N/A')
        if extra.get('spot_cvd_series'):
            _rows=extra['spot_cvd_series']
            _fig=go.Figure(go.Bar(x=[r['date'] for r in _rows],y=[r['delta_usd']/1e6 for r in _rows],
                                  marker_color=[COLORS['green'] if r['delta_usd']>=0 else COLORS['red'] for r in _rows],
                                  hovertemplate='%{x} · %{y:+,.1f}M USDT<extra></extra>'))
            _fig.update_layout(showlegend=False)
            _fig.update_yaxes(title_text='USDT millions')
            chart_plot(plot_style(_fig,260),key='spot-cvd-chart')
        st.caption('Source: '+extra.get('spot_cvd_source','')+' · '+extra.get('spot_cvd_as_of','')+
                   ' · '+extra.get('spot_cvd_note','')+
                   ' · Single-venue BTCUSDT, not global BTC CVD; positive = more aggressive buys.')
        if extra.get('spot_cvd_quality',1)<.99:
            st.warning('CVD fallback memakai venue dengan likuiditas lebih kecil. Bobot sinyal otomatis diturunkan.')
    else:
        st.warning('Spot taker CVD unavailable. Open Data Studio for API diagnostics; missing ≠ zero.')
    st.markdown('<div class="callout">⚠ ETF flow ≠ verified smart money. Spot CVD is a single-exchange aggressor-side measure, not proof of institutional accumulation.</div>',unsafe_allow_html=True)

elif current=='Signals':
    st.subheader('Signal audit · methodology')
    st.caption('70% overall and 66% on-chain are uncalibrated policy thresholds. Keeping them unchanged avoids manufacturing a BUY signal.')
    st.caption('Score range −2 to +2. Confidence/coverage measures observation availability, NOT the probability of a profitable prediction.')
    rows=[]
    for p in pillars:
        rows.append({'Pillar':p.name,'Direction score':round(p.score,2) if p.score is not None else None,
                     'Effective coverage':f'{p.quality*100:.0f}%','Weight':f'{p.weight:.0f}%',
                     'Latest':p.as_of or 'Missing','Source':p.source})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    for p in pillars:
        with st.expander(f'{p.name} · {"MISSING" if p.score is None else f"score {p.score:+.2f}"} · {p.quality:.0%} coverage'):
            st.write(p.evidence)
            st.caption(f'Observation: {p.as_of or "No date"} · Source: {p.source}')
    st.divider()
    st.markdown('**Cara melewati confirmation gate:** simpan snapshot di Signals, ekspor journal, lalu impor kembali melalui Settings pada sesi berikutnya. Minimal dua snapshot dengan arah sama, berjarak 12 jam hingga 7 hari. Repeated refresh tidak dihitung sebagai observasi baru. Data storage otomatis permanen belum tersedia.')
    st.subheader('Research journal')
    st.caption('Stored only in current browser session; download it to keep history across hosting restarts.')
    if st.button('＋ Record snapshot',type='primary'):
        tick=nowutc().isoformat()
        if st.session_state.history and tick[:16]==st.session_state.history[-1].get('timestamp','')[:16]:
            st.warning('A snapshot for this minute already exists.')
        else:
            st.session_state.history.append({'timestamp':tick,**result})
            st.success('Snapshot recorded; export to save permanently.')
    if st.session_state.history:
        st.dataframe(pd.DataFrame([{'UTC':h.get('timestamp'),'Decision':h.get('decision'),
                                   'Score':h.get('score'),'Coverage':h.get('coverage')} for h in st.session_state.history[-25:][::-1]]),hide_index=True)
        journal='\n'.join(json.dumps(x,ensure_ascii=False) for x in st.session_state.history)+'\n'
        st.download_button('↓ Download journal .jsonl',journal,'btc_decision_journal.jsonl','application/x-ndjson')
    st.download_button('↓ Current evidence JSON',json.dumps({'timestamp':nowutc().isoformat(),**result},indent=2,ensure_ascii=False),
                       'btc_current_report.json','application/json')

elif current=='Macro + On-chain':
    st.subheader('Macro & on-chain diagnostics')
    u,v=st.columns(2)
    with u:
        st.markdown(f'**Macro** &nbsp; {badge(statuses.get("macro",{}).get("status","MISSING"))}',unsafe_allow_html=True)
        st.metric('Macro coverage',f'{macro.quality:.0%}')
        st.write(macro.evidence)
        m=extra.get('macro',{})
        if m.get('components'):
            tab=[]
            for k,a in m['components'].items():
                tab.append({'Series':k,'Last':round(a['now'],3),'~20 obs ago':round(a['past'],3),'Date':a['as_of']})
            st.dataframe(pd.DataFrame(tab),hide_index=True)
            series={k:val['series'] for k,val in m['components'].items()}
            # Charts separately because units differ; avoid one-scale misleading chart.
            choice=st.selectbox('Chart macro metric',list(series.keys()))
            chart_plot(two_axis_lines({choice:series[choice]},{} ,'FRED'),key='fred-chart')
        st.caption('Macro combines U.S. Treasury yields (official or labelled mirror) with optional FRED dollar/credit series. Partial coverage is expected if FRED is unreachable.')
    with v:
        st.markdown(f'**On-chain** &nbsp; {badge(statuses.get("onchain",{}).get("status","MISSING"))}',unsafe_allow_html=True)
        st.metric('On-chain coverage',f'{onchain.quality:.0%}')
        st.write(onchain.evidence)
        oc=extra.get('onchain',{})
        st.caption('Community network activity and MVRV do not identify whale institutions. Entity-adjusted whales/aSOPR/exchange netflow are separate paid/manual fields.')
        if oc.get('series'):
            name=st.selectbox('On-chain community chart',list(oc['series'].keys()))
            chart_plot(two_axis_lines({name:oc['series'][name]}, {},'Coin Metrics'),key='chain-chart')
        elif oc.get('activity_series'):
            chart_plot(two_axis_lines({'tx':oc['activity_series']},{'tx':'On-chain BTC tx count'},'Blockchain.com'),key='tx-chart')
    st.markdown('### What is needed for full on-chain conviction?')
    st.markdown('''1. **Entity-adjusted whale balance** (wallet clustering, not simple large-address count).
2. **aSOPR or other realized-profit/cost basis metric** with observation date.
3. **Exchange netflow** corrected for known internal custody transfers.

A free network transaction count is *not* a replacement for these metrics. Therefore a partially connected on-chain source can still block an Accumulate/Distribute instruction.''')

elif current=='Data Studio':
    st.subheader('Live data connector health')
    st.caption('App build: '+APP_BUILD_ID+' · Module builds: '+', '.join(f'{k}={v}' for k,v in _MODULE_BUILDS.items()))
    st.caption('Connected = source returned data. Partial = limited indicators. Error = source unavailable, never converted to zero.')
    for name in ('price','derivatives','etf','macro','onchain','spot_cvd'):
        d=statuses.get(name,{})
        a,b=st.columns([1,2],vertical_alignment='center')
        with a:st.markdown(f'**{name.upper()}** &nbsp; {badge(d.get("status","MISSING"))}',unsafe_allow_html=True)
        with b:st.caption(f'{d.get("source","—")} · {d.get("as_of","—")}')
        if d.get('detail'):st.caption(d['detail'])
        st.divider()
    if errors:
        with st.expander(f'Network / parser errors ({len(errors)})'):
            for e in errors:st.write('• '+e)
    st.info('Provider dapat terblokir dari Streamlit Cloud. Jika semua sumber benar-benar gagal, ERROR adalah hasil yang benar; jangan isi angka palsu.')
    diagnostic={'app_build':APP_BUILD_ID,'modules':_MODULE_BUILDS,'statuses':statuses,'errors':errors,
                'fetched_at_utc':datetime.now(timezone.utc).isoformat(),'coverage_audit':audit,'gates':gates}
    st.download_button('↓ Download diagnostic JSON',json.dumps(diagnostic,ensure_ascii=False,indent=2),
                       'btc_diagnostics_v07.json','application/json')
    st.subheader('Explore sources')
    st.markdown('[U.S. Treasury](https://home.treasury.gov/resource-center/data-chart-center/interest-rates) · [FRED](https://fred.stlouisfed.org/) · [Binance public market-data documentation](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md) · [Coin Metrics](https://community-api.coinmetrics.io/) · [Farside](https://farside.co.uk/btc/)')

elif current=='Settings':
    st.subheader('Source settings & manual evidence')
    st.caption('Useful for premium inputs and fallback only. Never fill a metric with an estimate merely to increase coverage.')
    with st.expander('Import supplementary indicators JSON',expanded=True):
        upload=st.file_uploader('JSON indicators',type=['json'],key='indicators')
        if upload and st.button('Import indicators'):
            incoming=parse_upload(upload,dict)
            if incoming is not None:
                allowed={'etf','onchain','macro'}
                st.session_state.manual={k:v for k,v in incoming.items() if k in allowed}
                st.success('Imported.');st.rerun()
        history_file=st.file_uploader('Restore research journal JSONL',type=['jsonl','json'],key='journal')
        if history_file and st.button('Restore journal'):
            old=parse_upload(history_file,list)
            if old is not None:
                st.session_state.history=[h for h in old if isinstance(h,dict) and h.get('timestamp') and h.get('candidate')][-500:]
                st.success('Restored.');st.rerun()
    with st.expander('Macro override · only with dated source'):
        with st.form('macro_form'):
            condition=st.selectbox('Macro regime',['Neutral','Easing','Tightening'])
            source=st.text_input('Source URL / report name',key='macro_source')
            when=st.date_input('Observation date',value=date.today(),max_value=date.today(),key='macro_date')
            if st.form_submit_button('Save macro reading'):
                if not source.strip():st.error('Provide the original source.')
                else:
                    st.session_state.manual['macro']={'regime':condition,'source':source.strip(),'as_of':stamp(when)}
                    st.rerun()
    with st.expander('Whale / aSOPR / exchange flows (verified only)'):
        with st.form('onchain_form'):
            source=st.text_input('Vendor and metric methodology',key='chain_source')
            when=st.date_input('Observation date',value=date.today(),max_value=date.today(),key='chain_date')
            a=st.checkbox('Entity whale supply % change 30D');x=st.number_input('Whale 1K–10K BTC 30D (%)',value=0.0,step=.1)
            b=st.checkbox('aSOPR');y=st.number_input('aSOPR',min_value=0.,max_value=5.,value=1.,step=.005,format='%.4f')
            c=st.checkbox('Exchange netflow 7D');z=st.number_input('BTC netflow 7D (BTC)',value=0.,step=500.)
            if st.form_submit_button('Save on-chain evidence'):
                if not source.strip() or not (a or b or c):st.error('Select a metric and cite its source.')
                else:
                    v={'source':source.strip(),'as_of':stamp(when)}
                    if a:v['whale_1k_10k_30d_pct']=x
                    if b:v['asopr']=y
                    if c:v['exchange_netflow_7d_btc']=z
                    st.session_state.manual['onchain']=v;st.rerun()
    with st.expander('ETF fallback (opsional)'):
        st.caption('Spot CVD otomatis dari exchange atau arsip Binance tertunda. Tidak perlu input manual.')
        with st.form('etf_form'):
            val=st.number_input('BTC spot ETF net flow 5 trading sessions (USD millions)',value=0.,step=50.)
            src=st.text_input('ETF source')
            when=st.date_input('Latest ETF trading day',value=date.today(),max_value=date.today(),key='etf_date')
            if st.form_submit_button('Save ETF fallback'):
                if not src.strip():st.error('Source required')
                else:
                    st.session_state.manual['etf']={'net_5d_usdm':val,'source':src.strip(),'as_of':stamp(when)};st.rerun()
    st.download_button('↓ Backup supplementary inputs JSON',json.dumps(st.session_state.manual,ensure_ascii=False,indent=2),
                       'btc_supplementary_inputs.json','application/json')
    if st.button('Clear all manual entries'):
        st.session_state.manual={};st.rerun()
    st.divider()
    st.markdown('**Glassnode API:** if subscribed, put `GLASSNODE_API_KEY = "..."` in Streamlit Cloud → App settings → Secrets. Never publish the key in GitHub.')
    st.caption('Prototype / research. Macro signal rules are heuristics and not backtested; no orders, no guarantees.')

st.caption('BTC Atlas v0.7 · Informational / paper-decision prototype · Data observation times in UTC · Missing ≠ bearish · Coverage ≠ model accuracy')
