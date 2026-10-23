"""BTC Decision Engine v0.3 — tablet-first and read-only research dashboard.
Deploy from GitHub to Streamlit Community Cloud with streamlit_app.py as entry point.
All paper decisions require verified external data; no live exchange orders.
"""
from __future__ import annotations
import io
import json
import os
from datetime import date, datetime, timezone
import pandas as pd
import streamlit as st
from engine import build_pillars, evaluate
from sources import fetch_all

st.set_page_config(page_title="BTC Decision Engine · Tablet", page_icon="₿", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
.block-container {padding-top:1rem!important; padding-bottom:3rem; max-width:1150px;}
[data-testid="stMetric"] {border:1px solid #64748b44; border-radius:12px; padding:12px; background:rgba(123,139,159,.065);}
[data-testid="stMetricLabel"] {font-size:.88rem;}
[data-testid="stMetricValue"] {font-size:1.5rem;}
.hero {padding:1.15rem 1.35rem;background:linear-gradient(110deg,#122D3A,#17483F); border-radius:18px; color:#f4faf9; margin:0 0 1.15rem;}
.hero .over {font-size:.71rem;letter-spacing:.14em;opacity:.82;font-weight:700;}
.hero h1 {font-size:1.7rem;margin:.3rem 0;color:#fff!important;line-height:1.2;}
.hero p {font-size:.85rem;margin:0;opacity:.88;}
.tag {padding:.25rem .6rem;border-radius:99px;font-size:.78rem;display:inline-block;background:#365d57;color:white;}
[data-testid="stTabs"] button {font-size:.95rem;min-height:48px;}
.stButton>button, .stDownloadButton>button {min-height:47px;}
@media (max-width: 700px) {
 .block-container {padding-left:.7rem!important; padding-right:.7rem!important;}
 .hero {padding:1rem;}.hero h1 {font-size:1.3rem;}
 [data-testid="stMetric"] {padding:8px;}
 [data-testid="stMetricValue"] {font-size:1.2rem;}
 [data-testid="stHorizontalBlock"] {gap:.42rem;}
 [data-testid="stTabs"] button {font-size:.85rem;padding:.4rem .6rem;}
}
</style>
""", unsafe_allow_html=True)
st.markdown("""<div class="hero"><span class="over">BTC INTELLIGENCE · V0.3 · TABLET EDITION</span>
<h1>₿ Decision Engine</h1>
<p>On-chain · Spot · Derivatives · Macro · ETF · Price | Read-only, no exchange trading</p></div>""",unsafe_allow_html=True)


def utc_now():
    return datetime.now(timezone.utc)


def stamp(d):
    return d.isoformat()+'T12:00:00+00:00'


def safe_secret():
    try:
        return st.secrets.get('GLASSNODE_API_KEY','') or os.getenv('GLASSNODE_API_KEY','')
    except (FileNotFoundError, OSError, KeyError):
        return os.getenv('GLASSNODE_API_KEY','')


@st.cache_data(ttl=900, show_spinner=False)
def market_fetch(key):
    return fetch_all(key)


def parse_json_object(file):
    if file is None:return None
    try:
        data=json.loads(file.getvalue().decode('utf-8'))
        if not isinstance(data,dict):raise ValueError('Harus berupa objek JSON.')
        return data
    except (ValueError,UnicodeDecodeError) as e:
        st.error(f'File input gagal dibaca: {e}')
        return None


def parse_journal(file):
    if file is None:return []
    try:
        text=file.getvalue().decode('utf-8').strip()
        if not text:return []
        parsed=json.loads(text) if text.lstrip().startswith('[') else [json.loads(t) for t in text.splitlines() if t.strip()]
        if not isinstance(parsed,list):raise ValueError('Harus berupa JSONL atau JSON array.')
        valid=[x for x in parsed if isinstance(x,dict) and x.get('timestamp') and x.get('candidate')]
        return valid[-500:]
    except (ValueError,UnicodeDecodeError) as e:
        st.error(f'Jurnal gagal dibaca: {e}')
        return []


if 'extras' not in st.session_state:st.session_state.extras={}
if 'history' not in st.session_state:st.session_state.history=[]

key=safe_secret()
col_a,col_b=st.columns([1,2],vertical_alignment='center')
with col_a:
    if st.button('↻ Refresh data live', use_container_width=True):
        market_fetch.clear()
        st.rerun()
with col_b:
    st.caption('Harga Coinbase · Derivatives Binance/OKX/Bybit · ETF Farside/TFTC · cache 15 menit')
market, fetched_extras, errors, source_statuses=market_fetch(key)

extras=dict(fetched_extras)
# Manual entries may override only when they are at least as recent as the automated
# observation. This avoids stale session entries hiding a fresh ETF update.
for k,v in st.session_state.extras.items():
    if k in ('etf','onchain') and k in extras and isinstance(v,dict):
        if v.get('as_of','') >= extras[k].get('as_of',''):
            extras[k]=v
            source_statuses[k]={'status':'INPUT OVERRIDE','source':v.get('source','User'),
                                'as_of':v.get('as_of',''),'detail':'Input pengguna menggantikan data otomatis karena tanggal sama/lebih baru.'}
    else:
        extras[k]=v
for status_key,extra_key in [('etf','etf'),('onchain','onchain'),('macro','macro')]:
    if extra_key in extras and source_statuses.get(status_key,{}).get('status') in ('NOT CONNECTED','MANUAL','ERROR'):
        source_statuses[status_key]={'status':'INPUT AVAILABLE','source':extras[extra_key].get('source','User'),
                                   'as_of':extras[extra_key].get('as_of',''), 'detail':'Ada input dari pengguna.'}
if extras.get('spot_cvd_7d_usd') is not None:
    source_statuses['spot_cvd']={'status':'INPUT AVAILABLE','source':extras.get('spot_cvd_source','User'),
                                 'as_of':extras.get('spot_cvd_as_of',''),'detail':'Ada input CVD bersumber.'}
pillars=build_pillars(market,extras)
result=evaluate(pillars,st.session_state.history)
score=result['score']; coverage=result['coverage']

with st.expander('🔌 Status koneksi data · Connected / Partial / Missing',expanded=bool(errors)):
    display=[]
    for name in ('price','derivatives','etf','onchain','spot_cvd','macro'):
        v=source_statuses.get(name,{})
        display.append({'Feed':name,'Status':v.get('status','NOT CONNECTED'),
                        'Observasi':v.get('as_of','—'),'Sumber':v.get('source','—'),
                        'Keterangan':v.get('detail','')})
    st.dataframe(pd.DataFrame(display),hide_index=True,use_container_width=True)
    st.caption('NOT CONNECTED / MANUAL tidak sama dengan ERROR. Data coverage bukan persentase keberhasilan prediksi.')
    if errors:
        with st.expander('Detail error untuk troubleshooting'):
            for msg in errors:st.write('• '+msg)


t1,t2,t3,t4=st.tabs(['🏠 Ringkasan','📊 Indikator','✍️ Update Data','⚙️ Panduan'])

with t1:
    top1,top2=st.columns(2)
    with top1:st.metric('Model Score',f'{score:.1f}/100' if score is not None else 'N/A')
    with top2:st.metric('Data Coverage',f'{coverage:.0f}%')
    regime=result['regime']
    st.markdown('##### Kondisi pasar')
    st.info(regime)
    st.markdown('##### Keputusan model')
    if result['ready']:
        st.success(result['decision']+' · gate konfirmasi terpenuhi (paper signal)')
    else:
        st.warning(result['decision']+' · belum ada bukti cukup untuk tindakan terkonfirmasi')
        if result['candidate'] not in ('WAIT / HOLD','TIDAK ADA KEPUTUSAN'):
            st.caption('Kandidat awal: **'+result['candidate']+'** — DIBLOKIR oleh risk/data gates.')
    if result['gates']:
        with st.expander('Kenapa belum terkonfirmasi?',expanded=True):
            for g in result['gates']:st.write('• '+g)
    st.divider()
    px=market.get('price',{})
    st.markdown('##### Harga & trend (daily close)')
    if px.get('close_series'):
        s=pd.DataFrame(px['close_series']).set_index('date')
        st.line_chart(s, height=245, use_container_width=True)
        a,b=st.columns(2)
        a.metric('Close BTC-USD',f'${px["last_close"]:,.0f}')
        b.metric('SMA 50',f'${px["sma50"]:,.0f}')
        st.caption(f'Sumber Coinbase · candle selesai: {px.get("as_of","")} · bukan ticker intraday.')
    else:st.info('Harga belum tersedia. Periksa koneksi atau pembatasan API di hosting.')
    st.divider()
    st.markdown('##### US Spot BTC ETF Fund Flow')
    etf=extras.get('etf',{})
    if etf.get('net_5d_usdm') is not None:
        ef1,ef2=st.columns(2)
        ef1.metric('5 sesi perdagangan terakhir',f'${etf["net_5d_usdm"]:+,.1f}M')
        if etf.get('net_7calendar_usdm') is not None:
            ef2.metric('7 hari kalender',f'${etf["net_7calendar_usdm"]:+,.1f}M')
        else:ef2.metric('7 hari kalender','—')
        st.caption(f'Observasi terbaru: {etf.get("last_completed_session",etf.get("as_of",""))} · {etf.get("source","unspecified")}')
        if etf.get('recent_sessions'):
            df=pd.DataFrame(etf['recent_sessions'])
            df=df.rename(columns={'date':'Tanggal','flow_usdm':'Net Flow (USD juta)'})
            st.bar_chart(df.set_index('Tanggal')['Net Flow (USD juta)'],height=180,use_container_width=True)
            with st.expander('Audit transaksi ETF harian'):
                st.dataframe(df.iloc[::-1],hide_index=True,use_container_width=True)
        st.caption('ETF net creation ≠ bukti smart money; angka positif dan negatif dapat saling menetralkan.')
    else:
        st.warning('ETF belum tersedia dari Farside/TFTC. Cek status koneksi atau gunakan update bersumber.')
    st.divider()
    st.markdown('##### Bukti per kelompok')
    for p in pillars:
        direction='⚪ N/A' if p.score is None else ('🟢 Positif' if p.score>.25 else ('🔴 Negatif' if p.score<-.25 else '🟡 Netral'))
        st.markdown(f'**{p.name}** — {direction} · coverage {p.quality*100:.0f}%')
        st.caption(p.evidence)
    st.caption('Penilaian ilustratif berbasis aturan, belum backtested. Tidak mengeksekusi perdagangan.')

with t2:
    st.subheader('Audit indikator')
    st.caption('Metrik yang tidak tersedia atau sudah kedaluwarsa tidak mendapat skor. Arah bukan jaminan return.')
    rows=[]
    for p in pillars:
        rows.append({'Indikator':p.name,'Skor -2..2':p.score,'Bobot':f'{p.weight:.0f}%',
                     'Kualitas':f'{p.quality*100:.0f}%','Tanggal':p.as_of or '—','Sumber':p.source})
    st.dataframe(pd.DataFrame(rows),hide_index=True,use_container_width=True)
    for p in pillars:
        with st.expander(f'{p.name} · detail & sumber'):
            st.write(p.evidence)
            st.caption(f'Sumber: {p.source} | Observasi: {p.as_of or "tidak ada"}')
    st.divider()
    st.markdown('##### Simpan hasil untuk perbandingan')
    st.caption('Jurnal tersimpan **di browser hanya selama sesi**. Unduh file JSONL agar tidak hilang saat app sleep atau refresh.')
    if st.button('＋ Catat snapshot sekarang',type='primary',use_container_width=True):
        now=utc_now().isoformat()
        if st.session_state.history and (now[:16]==st.session_state.history[-1].get('timestamp','')[:16]):
            st.warning('Sudah ada snapshot pada menit yang sama. Tunggu observasi berikutnya.')
        else:
            st.session_state.history.append({'timestamp':now,**result})
            st.success('Snapshot masuk jurnal sesi ini. Unduh journal agar tersimpan permanen.')
    if st.session_state.history:
        rows=[{'Waktu UTC':h.get('timestamp',''),'Skor':h.get('score'),
               'Coverage':h.get('coverage'),'Status':h.get('decision')} for h in st.session_state.history]
        st.dataframe(pd.DataFrame(rows[-12:][::-1]),hide_index=True,use_container_width=True)
        journal_data='\n'.join(json.dumps(x,ensure_ascii=False) for x in st.session_state.history)+'\n'
        st.download_button('⬇ Simpan Journal (.jsonl)', journal_data,'btc_journal.jsonl',mime='application/x-ndjson',use_container_width=True)
    st.download_button('⬇ Unduh laporan sekarang (.json)',json.dumps({'timestamp':utc_now().isoformat(),**result},indent=2,ensure_ascii=False),
                       'btc_report.json','application/json',use_container_width=True)

with t3:
    st.subheader('Tambahkan data terverifikasi')
    st.caption('ETF sekarang otomatis. Tab ini hanya untuk melengkapi indikator premium atau memperbaiki data yang tidak tersedia.')
    left,right=st.columns(2)
    with left:
        up=st.file_uploader('Import indikator JSON',type='json',key='import_extra',help='File input berisi ETF, makro, CVD, on-chain. Contoh tersedia di paket.')
        if up is not None and st.button('Gunakan file indikator',use_container_width=True):
            obj=parse_json_object(up)
            if obj is not None:
                allowed={'etf','macro','onchain','spot_cvd_7d_usd','spot_cvd_as_of','spot_cvd_source'}
                st.session_state.extras={k:v for k,v in obj.items() if k in allowed}
                st.success('Data indikator berhasil diimpor.');st.rerun()
    with right:
        journal_file=st.file_uploader('Import jurnal sebelumnya (.jsonl)',type=['jsonl','json'],key='import_journal')
        if journal_file is not None and st.button('Pulihkan jurnal',use_container_width=True):
            past=parse_journal(journal_file)
            st.session_state.history=past
            st.success(f'{len(past)} snapshot dipulihkan.');st.rerun()
    with st.expander('ETF flows · fallback jika otomatis gagal'):
        with st.form('etf_form'):
            val=st.number_input('Net inflow/outflow 5 hari perdagangan (USD juta)',value=0.0,step=50.0)
            src=st.text_input('Sumber & referensi',placeholder='Contoh: Farside Bitcoin ETF flows')
            day=st.date_input('Tanggal laporan ETF',value=date.today(),max_value=date.today())
            saved=st.form_submit_button('Simpan ETF flow',use_container_width=True)
            if saved:
                if not src.strip():st.error('Cantumkan sumber terlebih dahulu.')
                else:st.session_state.extras['etf']={'net_5d_usdm':val,'source':src.strip(),'as_of':stamp(day)};st.rerun()
    with st.expander('Spot CVD · bukan price volume'):
        with st.form('spot_form'):
            val=st.number_input('Spot CVD 7D (USD juta, negatif = aggressive sells)',step=10.0,value=0.0)
            src=st.text_input('Exchange / sumber CVD',placeholder='Contoh: agregat exchange / vendor CVD')
            day=st.date_input('Tanggal CVD',value=date.today(),max_value=date.today())
            saved=st.form_submit_button('Simpan CVD',use_container_width=True)
            if saved:
                if not src.strip():st.error('Cantumkan sumber terlebih dahulu.')
                else:
                    st.session_state.extras.update(spot_cvd_7d_usd=val*1e6,spot_cvd_source=src.strip(),spot_cvd_as_of=stamp(day));st.rerun()
    with st.expander('Macro & liquidity · interpretasi manual'):
        with st.form('macro_form'):
            rg=st.selectbox('Kondisi makro',['Neutral','Easing','Tightening'])
            src=st.text_input('Dasar & sumber',placeholder='FedWatch + Treasury 10Y + DXY')
            day=st.date_input('Tanggal penilaian makro',value=date.today(),max_value=date.today())
            saved=st.form_submit_button('Simpan macro',use_container_width=True)
            if saved:
                if not src.strip():st.error('Cantumkan sumber terlebih dahulu.')
                else:st.session_state.extras['macro']={'regime':rg,'source':src.strip(),'as_of':stamp(day)};st.rerun()
    with st.expander('On-chain · entity-adjusted'):
        st.caption('Isi hanya indikator yang benar-benar tersedia, jangan isi nilai fiktif. Koneksi Glassnode API key opsional dari Settings → Secrets hosting.')
        with st.form('onchain_form'):
            src=st.text_input('Penyedia data',placeholder='Glassnode / CryptoQuant dan seri metrik')
            day=st.date_input('Tanggal on-chain',value=date.today(),max_value=date.today())
            use_w=st.checkbox('Saya punya perubahan whale 1K–10K BTC (30D)')
            wh=st.number_input('Whale supply 30D (%)',value=0.0,step=.25)
            use_a=st.checkbox('Saya punya aSOPR')
            aso=st.number_input('aSOPR',min_value=0.0,max_value=5.0,value=1.0,step=.005,format='%.4f')
            use_n=st.checkbox('Saya punya exchange netflow 7D')
            net=st.number_input('Netflow 7D (BTC; negatif = outflow)',value=0.0,step=500.0)
            saved=st.form_submit_button('Simpan on-chain',use_container_width=True)
            if saved:
                if not src.strip() or not any([use_w,use_a,use_n]):st.error('Sumber dan setidaknya satu metrik wajib diisi.')
                else:
                    data={'source':src.strip(),'as_of':stamp(day)}
                    if use_w:data['whale_1k_10k_30d_pct']=wh
                    if use_a:data['asopr']=aso
                    if use_n:data['exchange_netflow_7d_btc']=net
                    st.session_state.extras['onchain']=data;st.rerun()
    st.divider()
    st.markdown('##### Backup input agar tidak perlu diulang')
    st.download_button('⬇ Simpan input indikator (.json)',json.dumps(st.session_state.extras,ensure_ascii=False,indent=2),
                       'btc_indicator_inputs.json','application/json',use_container_width=True)
    if st.button('Hapus seluruh input manual',use_container_width=True):
        st.session_state.extras={};st.rerun()

with t4:
    st.subheader('Cara pakai di tablet')
    st.markdown('''
**Pertama kali:** Buat repository GitHub, upload **4 file** `streamlit_app.py`, `engine.py`, `sources.py`, `requirements.txt` langsung di root repo. Lalu ke [Streamlit Community Cloud](https://share.streamlit.io), **Create app**, pilih repo dan `streamlit_app.py`, kemudian deploy.

**Setiap hari:** Buka link aplikasi di Chrome atau Safari → tab **Ringkasan** → tombol **Refresh data live**. Jika mau melengkapi sinyal, gunakan tab **Update Data**.

**Jika ingin mempertahankan history:** tab **Indikator** → **Catat snapshot** → **Simpan Journal**. Di kunjungan berikutnya, import file journal melalui **Update Data**. Penyimpanan server gratis tidak permanen.

**API premium (opsional):** Tambahkan `GLASSNODE_API_KEY = "isi_api_key_anda"` di **App settings → Secrets** pada Streamlit Cloud. Jangan taruh API key di GitHub atau upload ke chat.

**Model:** skor adalah heuristik, **bukan probabilitas return**. `Data Coverage` bukan probabilitas prediksi benar. Tombol ini tidak akan pernah memasang order di exchange.

**Tidak muncul data:** endpoint Binance dapat diblokir menurut negara/hosting; lihat panel peringatan di atas. Ketiadaan data secara sengaja menurunkan coverage.
    ''')
    st.info('Mode penelitian/paper decision. Versi ini tidak otomatis menyimpan database, mengirim alert Telegram, atau mengeksekusi transaksi.')
