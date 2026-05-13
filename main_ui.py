import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
import sys
import json
import os
import requests
import io
import xml.etree.ElementTree as ET
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# --- 💡 강력한 주식 검색 엔진 (에러 방지용) ---
try:
    import FinanceDataReader as fdr
    FDR_INSTALLED = True
except ImportError:
    FDR_INSTALLED = False

# --- KST (한국 표준시) 설정 ---
KST = timezone(timedelta(hours=9))

# --- 실행 방식 체크 ---
if __name__ == "__main__":
    if not st.runtime.exists():
        print("\n" + "=" * 50)
        print("🚨 실행 오류: 이 프로그램은 'python' 명령어로 실행할 수 없습니다.")
        print("아래 명령어를 터미널에 복사해서 입력하세요:")
        print(f"\n   streamlit run {sys.argv[0].split('/')[-1].split('\\')[-1]}")
        print("=" * 50 + "\n")
        sys.exit()

# 1. 페이지 설정
st.set_page_config(page_title="박스 모멘텀 프로 시스템", layout="wide")

# --- 📱 모바일 UI 최적화 CSS ---
st.markdown("""
<style>
@media (max-width: 768px) {
    html, body, [class*="st-"] { font-size: 14px !important; }
    h1 { font-size: 1.5rem !important; }
    h2 { font-size: 1.25rem !important; }
    h3 { font-size: 1.1rem !important; }
    [data-testid="stMetricValue"] { font-size: 1.4rem !important; }
    .block-container { padding: 2rem 1rem !important; }
}
</style>
""", unsafe_allow_html=True)

# --- 🔐 멀티 유저 로그인 시스템 ---
if 'username' not in st.session_state:
    st.title("🛡️ 박스 모멘텀 프로 시스템")
    st.write("환영합니다! 개인별 포트폴리오 저장을 위해 나만의 닉네임을 입력해주세요.")
    
    with st.form("login_form"):
        username_input = st.text_input("👤 사용자 닉네임 (영문/숫자/한글 자유롭게 입력)", placeholder="예: 워런버핏, 주식고수123")
        submit_btn = st.form_submit_button("시스템 접속하기 🚀")
        
        if submit_btn:
            if username_input.strip() == "":
                st.error("닉네임을 입력해야 접속할 수 있습니다.")
            else:
                st.session_state['username'] = username_input.strip()
                st.rerun()
    st.stop()

current_user = st.session_state['username']

# --- 💾 파이어베이스(Firebase) 영구 저장 로직 ---
@st.cache_resource
def init_firebase():
    if not firebase_admin._apps:
        raw_keys = st.secrets["FIREBASE_JSON"]
        key_dict = json.loads(raw_keys, strict=False)
        if "private_key" in key_dict:
            key_dict["private_key"] = key_dict["private_key"].replace('\\n', '\n')
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

try:
    db = init_firebase()
except Exception as e:
    st.error(f"데이터베이스 연결 실패: {e}")
    st.stop()

def get_doc_ref(username):
    return db.collection('investing').document(username)

def load_portfolio(username):
    doc_ref = get_doc_ref(username)
    default_data = {
        "005930.KS": {"price": 0.0, "qty": 0, "target": 0.0, "name": "삼성전자", "note": "", "types": ["general"], "in_account": False}
    }
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            if data:
                migrated_data = {}
                need_update = False
                for k, v in data.items():
                    base_sym = k.split('_')[0] if '_' in k else k
                    if base_sym != k: need_update = True 
                    if base_sym not in migrated_data:
                        migrated_data[base_sym] = v.copy()
                        migrated_data[base_sym]['types'] = []
                        if 'type' in v and v['type']: migrated_data[base_sym]['types'].append(v['type'])
                    else:
                        if 'type' in v and v['type'] not in migrated_data[base_sym]['types']:
                            migrated_data[base_sym]['types'].append(v['type'])
                    if 'types' in v:
                        for t in v['types']:
                            if t not in migrated_data[base_sym]['types']:
                                migrated_data[base_sym]['types'].append(t)
                    
                    if 'in_account' not in migrated_data[base_sym]:
                        migrated_data[base_sym]['in_account'] = False
                        need_update = True

                if need_update: doc_ref.set(migrated_data)
                return migrated_data
        doc_ref.set(default_data)
        return default_data
    except:
        return default_data

def save_portfolio(username, data):
    doc_ref = get_doc_ref(username)
    try:
        doc_ref.set(data)
    except:
        pass

portfolio = load_portfolio(current_user)
user_tickers = list(portfolio.keys())

FALLBACK_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오", "035420": "NAVER", "005380": "현대차"
}

# --- 🇰🇷 100% 확실한 한글 종목명 변환기 (속도 저하 완벽 차단 로직 적용) ---
@st.cache_data(ttl=86400, show_spinner=False)
def get_krx_names():
    result = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://m.stock.naver.com/'
    }
    
    # 1순위: 네이버 증권 API (JSON 형태로 가장 빠름, 타임아웃 15초로 넉넉하게 연장)
    try:
        for market in ['KOSPI', 'KOSDAQ']:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=3000'
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for stock in data.get('stocks', []):
                    result[stock['itemCode']] = stock['stockName']
        if len(result) > 1000: 
            return result
    except:
        pass

    # 2순위: FDR 라이브러리
    if FDR_INSTALLED:
        try:
            df_krx = fdr.StockListing('KRX')
            if not df_krx.empty:
                result = dict(zip(df_krx['Code'], df_krx['Name']))
                if len(result) > 1000:
                    return result
        except:
            pass

    # 실패 시 캐시를 지우지 않습니다. (키보드 칠 때마다 15초씩 멈추는 무한 로딩 방지)
    return result

# --- 💡 스크리너 전용: 코스피/코스닥 전 종목 수집기 (버벅임 차단) ---
@st.cache_data(ttl=86400, show_spinner=False)
def get_market_tickers():
    fast_list = []
    all_list = []  
    fallback_fast = [
        '005930.KS', '000660.KS', '373220.KS', '207940.KS', '005380.KS', '051910.KS', '000270.KS', '068270.KS', '005490.KS', '035420.KS',
        '105560.KS', '055550.KS', '032830.KS', '012330.KS', '033780.KS', '003550.KS', '086790.KS', '015760.KS', '034020.KS', '018260.KS',
        '247540.KQ', '086520.KQ', '028300.KQ', '091990.KQ', '277810.KQ', '066970.KQ', '022100.KQ', '068240.KQ', '196170.KQ', '041510.KQ'
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://m.stock.naver.com/'
    }
    
    # 1순위: 네이버 증권 API
    try:
        for market, suffix in [('KOSPI', '.KS'), ('KOSDAQ', '.KQ')]:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=3000'
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                for i, stock in enumerate(data.get('stocks', [])):
                    ticker = stock['itemCode'] + suffix
                    if ticker not in all_list: 
                        all_list.append(ticker)
                        if i < 50: fast_list.append(ticker)
        if len(all_list) > 1000:
            return fast_list, all_list
    except:
        pass

    # 2순위: FDR 라이브러리
    if FDR_INSTALLED:
        try:
            df_krx = fdr.StockListing('KRX')
            if not df_krx.empty:
                for _, row in df_krx.iterrows():
                    code = row['Code']
                    market = row['Market']
                    if market == 'KOSPI':
                        all_list.append(code + '.KS')
                    elif market in ['KOSDAQ', 'KOSDAQ GLOBAL']:
                        all_list.append(code + '.KQ')
                
                if len(all_list) > 1000:
                    fast_list = all_list[:100] 
                    return fast_list, all_list
        except:
            pass

    # 💡 매우 중요: 실패 시 캐시를 지우지 않아 타자 칠 때마다 멈추는 끔찍한 현상 완벽 방지
    return fallback_fast, fallback_fast

# --- 💡 타임존(한국시간) 변환 헬퍼 함수 ---
def convert_to_kst(df):
    if df is None or df.empty:
        return df
    if getattr(df.index, 'tz', None) is None:
        df.index = df.index.tz_localize('UTC').tz_convert(KST)
    else:
        df.index = df.index.tz_convert(KST)
    return df

# --- 💡 무적의 네이버 공식 차트 직접 호출기 ---
def get_naver_history(code, interval, period_days):
    timeframe = 'day'
    count = period_days
    if interval == '1wk':
        timeframe = 'week'
        count = period_days // 7 + 1
    elif interval in ['1mo', '3mo']:
        timeframe = 'month'
        count = period_days // 30 + 1
        
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe={timeframe}&count={count}&requestType=0"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            data = []
            for item in root.findall('.//item'):
                d_str = item.get('data')
                date, op, hp, lp, cp, vol = d_str.split('|')
                data.append({
                    'Date': pd.to_datetime(date),
                    'Open': float(op),
                    'High': float(hp),
                    'Low': float(lp),
                    'Close': float(cp),
                    'Volume': float(vol)
                })
            if data:
                df = pd.DataFrame(data).set_index('Date')
                if getattr(df.index, 'tz', None) is None:
                    df.index = df.index.tz_localize(KST)
                return df
    except:
        pass
    return pd.DataFrame()

# --- 💡 혁신적인 무결점 데이터 수집 엔진 ---
def get_robust_history(ticker, period_days, interval, is_intraday=False):
    if is_intraday:
        try:
            df = yf.Ticker(ticker).history(period=f"{period_days}d", interval=interval)
            return convert_to_kst(df.dropna(subset=['Close']))
        except:
            return pd.DataFrame()
            
    is_korean = ticker.endswith('.KS') or ticker.endswith('.KQ')
    
    if is_korean:
        code = ticker.replace('.KS', '').replace('.KQ', '')
        
        df = get_naver_history(code, interval, period_days)
        if not df.empty:
            if interval == '3mo':
                df = df.resample('QE').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
            return df
            
        if FDR_INSTALLED:
            start_str = (datetime.now(KST) - timedelta(days=period_days)).strftime('%Y-%m-%d')
            try:
                df = fdr.DataReader(code, start_str)
                if not df.empty:
                    df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
                    if interval == '1wk':
                        df = df.resample('W-FRI').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
                    elif interval == '1mo':
                        df = df.resample('ME').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
                    elif interval == '3mo':
                        df = df.resample('QE').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
                        
                    if getattr(df.index, 'tz', None) is None:
                        df.index = df.index.tz_localize(KST)
                    return df
            except Exception:
                pass 
            
    try:
        yf_interval = '1mo' if interval == '3mo' else interval
        df = yf.Ticker(ticker).history(period=f"{period_days}d", interval=yf_interval)
        if not df.empty:
            df = df.dropna(subset=['Close'])
            df = convert_to_kst(df)
            if interval == '3mo':
                df = df.resample('QE').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
            return df
    except:
        pass
        
    return pd.DataFrame()

# --- 데이터 수집 및 분석 함수 ---
@st.cache_data(ttl=3600)
def get_market_data():
    try:
        k_df, q_df = None, None
        
        k_df = get_naver_history('KOSPI', '1d', 365)
        q_df = get_naver_history('KOSDAQ', '1d', 365)
        
        if not k_df.empty and not q_df.empty:
            return k_df, q_df

        start_str = (datetime.now(KST) - timedelta(days=365)).strftime('%Y-%m-%d')
        if FDR_INSTALLED:
            try:
                if k_df.empty:
                    k_df = fdr.DataReader('KS11', start_str)
                    if not k_df.empty:
                        k_df = k_df[['Open', 'High', 'Low', 'Close', 'Volume']]
                        if getattr(k_df.index, 'tz', None) is None: k_df.index = k_df.index.tz_localize(KST)
                if q_df.empty:
                    q_df = fdr.DataReader('KQ11', start_str)
                    if not q_df.empty:
                        q_df = q_df[['Open', 'High', 'Low', 'Close', 'Volume']]
                        if getattr(q_df.index, 'tz', None) is None: q_df.index = q_df.index.tz_localize(KST)
                return k_df, q_df
            except: pass
            
        kospi = yf.Ticker("^KS11").history(period="1y")
        kosdaq = yf.Ticker("^KQ11").history(period="1y")
        k_df = convert_to_kst(kospi.dropna(subset=['Close'])) if not kospi.empty else None
        q_df = convert_to_kst(kosdaq.dropna(subset=['Close'])) if not kosdaq.empty else None
        return k_df, q_df
    except:
        return None, None

@st.cache_data(ttl=300, show_spinner=False)
def get_chart_data(ticker, tf_option):
    tf_map = {
        "30분": (60, "30m", True), 
        "1시간": (730, "1h", True), 
        "일봉": (730, "1d", False),
        "주봉": (1825, "1wk", False), 
        "월봉": (3650, "1mo", False), 
        "분기봉": (3650, "3mo", False), 
        "년봉": (3650, "1mo", False) 
    }
    period_days, interval, is_intraday = tf_map.get(tf_option, (730, "1d", False))
    
    try:
        df = get_robust_history(ticker, period_days, interval, is_intraday)
        if df is None or df.empty: return None
        
        if tf_option == "년봉":
            df = df.resample('YE').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
            
        df['MA20'] = ta.sma(df['Close'], length=20)
        df['MA50'] = ta.sma(df['Close'], length=50)
        df['MA150'] = ta.sma(df['Close'], length=150)
        df['MA200'] = ta.sma(df['Close'], length=200)
        
        df['RSI'] = ta.rsi(df['Close'], length=14)
        bbands = ta.bbands(df['Close'], length=20, std=2)
        if bbands is not None: df = pd.concat([df, bbands], axis=1)
        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        df['Trading_Value'] = df['Close'] * df['Volume']
        
        return df
    except:
        return None

def get_enhanced_data(ticker, market_df):
    try:
        df = get_robust_history(ticker, period_days=365, interval="1d", is_intraday=False)
        if df is None or df.empty or len(df) < 50: return None, None

        df['MA20'] = ta.sma(df['Close'], length=20)
        df['MA50'] = ta.sma(df['Close'], length=50)
        df['MA150'] = ta.sma(df['Close'], length=150)
        df['MA200'] = ta.sma(df['Close'], length=200)

        bbands = ta.bbands(df['Close'], length=20, std=2)
        if bbands is not None: df = pd.concat([df, bbands], axis=1)

        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['OBV'] = ta.obv(df['Close'], df['Volume'])
        adx = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        if adx is not None: df = pd.concat([df, adx], axis=1)
        
        df['Trading_Value'] = df['Close'] * df['Volume']

        if market_df is not None and not market_df.empty:
            market_close = market_df['Close'].reindex(df.index, method='ffill')
            stock_perf = (df['Close'] / df['Close'].shift(50)) - 1
            market_perf = (market_close / market_close.shift(50)) - 1
            df['RS_Rating'] = (stock_perf - market_perf).fillna(0)

        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
        except:
            info = {}
            
        code = ticker.split('.')[0]
        kor_name = krx_map.get(code, FALLBACK_NAMES.get(code, info.get('shortName', info.get('longName', ticker))))

        fundamentals = {
            'name': kor_name, 'roe': info.get('returnOnEquity', 0),
            'op_margin': info.get('operatingMargins', 0), 'sales_growth': info.get('revenueGrowth', 0),
            'eps_growth': info.get('earningsQuarterlyGrowth', 0), 'debt_ratio': info.get('debtToEquity', 0),
            'low_52w': df['Low'].min(), 'high_52w': df['High'].max()
        }
        return df, fundamentals
    except:
        return None, None

def safe_val(val, default=0):
    try:
        return float(val) if pd.notna(val) and val is not None else float(default)
    except:
        return float(default)

def detect_patterns(df):
    patterns = []
    if len(df) < 5: return patterns
    
    today = df.iloc[-1]
    yest = df.iloc[-2]
    prev = df.iloc[-3]
    
    body = abs(today['Close'] - today['Open'])
    total_range = today['High'] - today['Low']
    if total_range == 0: total_range = 0.001 
    
    lower_tail = today['Open'] - today['Low'] if today['Close'] > today['Open'] else today['Close'] - today['Low']
    upper_tail = today['High'] - today['Close'] if today['Close'] > today['Open'] else today['High'] - today['Open']

    if 'Trading_Value' in df.columns:
        recent_df = df.tail(10)
        bullish_candles = recent_df[(recent_df['Close'] > recent_df['Open']) & ((recent_df['Close'] - recent_df['Open']) / recent_df['Open'] >= 0.08)]
        if not bullish_candles.empty:
            max_tv_day = bullish_candles.loc[bullish_candles['Trading_Value'].idxmax()]
            tv_100m = safe_val(max_tv_day['Trading_Value']) / 100000000 
            
            if tv_100m >= 10: 
                pct_val = ((max_tv_day['Close'] - max_tv_day['Open']) / max_tv_day['Open']) * 100
                candle_title = "🔥 초강력 장대 양봉 (15%↑)" if pct_val >= 15.0 else "💰 기준 장대 양봉 (8%↑)"
                
                try:
                    if max_tv_day.name.hour == 0 and max_tv_day.name.minute == 0:
                        date_str = max_tv_day.name.strftime('%Y-%m-%d')
                    else:
                        date_str = max_tv_day.name.strftime('%Y-%m-%d %H:%M')
                    day_str = "오늘" if hasattr(max_tv_day.name, 'date') and hasattr(today.name, 'date') and max_tv_day.name.date() == today.name.date() else f"최근({date_str})"
                except:
                    date_str = str(max_tv_day.name)[:16]
                    day_str = f"최근({date_str})"
                    
                patterns.append(f"**[{day_str} {candle_title} & 거래대금 폭발]**\n\n📊 **상태:** `+{pct_val:.1f}% 급등` (터진 거래대금: 약 {tv_100m:,.0f}억 원)\n\n💡 **의미:** 엄청난 자금({tv_100m:,.0f}억원)이 유입되며 세력 개입이 확실시되는 매우 강력 장대양봉이 탄생했습니다. 이 캔들의 시가 또는 절반 가격을 절대 지지선으로 삼고 매매하세요.")

    if body <= total_range * 0.1 and total_range > (today['Close'] * 0.01):
        patterns.append("➕ **[도지형 캔들 (Doji)]**\n\n📊 **모양:** `[ 십자가 ➕ 형태 ]`\n\n💡 **의미:** 매수세와 매도세가 팽팽하게 맞서고 있습니다. 하락/상승 추세가 곧 바뀔 수 있는 중요한 변곡점입니다.")
        
    if yest['Close'] < yest['Open'] and today['Close'] > today['Open'] and today['Open'] <= yest['Close'] and today['Close'] >= yest['Open']:
        patterns.append("🟢 **[상승 장악형 (Bullish Engulfing)]**\n\n📊 **모양:** `[직전: 얇은 파란 기둥] ➔ [최근: 두꺼운 빨간 기둥]`\n\n💡 **의미:** 직전의 하락을 완전히 덮어버리는 강력 매수세가 터졌습니다. 바닥권 출현 시 강력한 반등 시그널입니다.")
    elif yest['Close'] > yest['Open'] and today['Close'] < today['Open'] and today['Open'] >= yest['Close'] and today['Close'] <= yest['Open']:
        patterns.append("🔴 **[하락 장악형 (Bearish Engulfing)]**\n\n📊 **모양:** `[직전: 얇은 빨간 기둥] ➔ [최근: 두꺼운 파란 기둥]`\n\n💡 **의미:** 상승을 짓누르는 거대한 매도 폭탄이 쏟아졌습니다. 고점 돌파에 실패하고 추세가 꺾일 위험이 큰 시그널입니다.")
        
    if today['Close'] > today['Open'] and yest['Close'] > yest['Open'] and prev['Close'] > prev['Open'] and today['Close'] > yest['Close'] and yest['Close'] > prev['Close']:
        patterns.append("🔥 **[적삼병 (Three White Soldiers)]**\n\n📊 **모양:** `[📈빨강] ➔ [📈더 높은 빨강] ➔ [📈더 높은 빨강]`\n\n💡 **의미:** 3연속 양봉 출현. 시장의 확신이 차있으며 대세 상승세로 진입할 확률이 높습니다.")
    elif today['Close'] < today['Open'] and yest['Close'] < yest['Open'] and prev['Close'] < prev['Open'] and today['Close'] < yest['Close'] and yest['Close'] < prev['Close']:
        patterns.append("❄️ **[흑삼병 (Three Black Crows)]**\n\n📊 **모양:** `[📉파랑] ➔ [📉더 낮은 파랑] ➔ [📉더 낮은 파랑]`\n\n💡 **의미:** 3연속 음봉 출현. 매도 심리가 지배적이며 바닥을 알 수 없으니 관망해야 합니다.")
        
    if body > 0:
        if lower_tail > body * 2.5 and upper_tail < body * 0.5:
            patterns.append("🔨 **[망치형 캔들 (Hammer)]**\n\n📊 **모양:** `[위: 짧은 몸통] ➕ [아래: 매우 긴 꼬리]`\n\n💡 **의미:** 장중 큰 폭락이 있었지만 저가에서 매수세가 끌어올렸습니다. 지지선이 될 확률이 높습니다.")
        elif upper_tail > body * 2.5 and lower_tail < body * 0.5:
            patterns.append("☄️ **[유성형 / 역망치형 (Shooting Star)]**\n\n📊 **모양:** `[위: 매우 긴 꼬리] ➕ [아래: 짧은 몸통]`\n\n💡 **의미:** 상승 시도 후 대규모 매물에 밀려버린 형태입니다. 고점에서 출현 시 위험합니다.")

    t_ma20 = safe_val(today.get('MA20'))
    t_ma50 = safe_val(today.get('MA50'))
    t_ma150 = safe_val(today.get('MA150'))
    y_ma20 = safe_val(yest.get('MA20'))
    y_ma50 = safe_val(yest.get('MA50'))

    if t_ma20 > 0 and t_ma50 > 0 and y_ma20 > 0 and y_ma50 > 0:
        if y_ma20 <= y_ma50 and t_ma20 > t_ma50:
            patterns.append("🌟 **[골든 크로스 (Golden Cross)]**\n\n📊 **모양:** `단기 20선 ↗️ 상향 돌파 🟢 중기 50선`\n\n💡 **의미:** 단기 모멘텀이 중장기 흐름을 이겨냈습니다. 전형적인 상승장 초입 시그널입니다.")
        elif y_ma20 >= y_ma50 and t_ma20 < t_ma50:
            patterns.append("🚨 **[데드 크로스 (Dead Cross)]**\n\n📊 **모양:** `단기 20선 ↘️ 하향 이탈 🟢 중기 50선`\n\n💡 **의미:** 단기 모멘텀이 꺾였습니다. 즉각적인 리스크 관리가 필요합니다.")
            
        if t_ma150 > 0 and today['Close'] > t_ma20 > t_ma50 > t_ma150:
            patterns.append("🎢 **[이평선 완벽 정배열 (Perfect Up-trend)]**\n\n📊 **모양:** `현재가 > 20선 > 50선 > 150선`\n\n💡 **의미:** 완벽한 우상향 고속도로를 달리고 있습니다. 눌림목이 가장 좋은 매수 타이밍입니다.")

    rsi_col = 'RSI_14' if 'RSI_14' in df.columns else 'RSI' if 'RSI' in df.columns else None
    if rsi_col:
        t_rsi = safe_val(today.get(rsi_col))
        if t_rsi >= 70:
            patterns.append(f"⚠️ **[RSI 과열/과매수]**\n\n📊 **상태:** `RSI 수치 {t_rsi:.1f}` (70 이상 위험)\n\n💡 **의미:** 단기간에 사람들이 너무 많이 샀습니다. 곧 차익 실현 물량이 쏟아져 조정받을 수 있습니다.")
        elif t_rsi > 0 and t_rsi <= 30:
            patterns.append(f"🛒 **[RSI 침체/과매도]**\n\n📊 **상태:** `RSI 수치 {t_rsi:.1f}` (30 이하 저평가)\n\n💡 **의미:** 공포 심리에 의해 너무 많이 팔렸습니다. 기술적 반등이 들어올 수 있는 기회입니다.")

    bb_upper = [c for c in df.columns if c.startswith('BBU_')]
    bb_lower = [c for c in df.columns if c.startswith('BBL_')]
    if bb_upper and bb_lower:
        u_col, l_col = bb_upper[0], bb_lower[0]
        if today['Close'] > safe_val(today.get(u_col)):
            patterns.append("🚀 **[볼린저 밴드 상단 돌파]**\n\n📊 **상태:** `주가가 밴드 천장을 찢고 올라감`\n\n💡 **의미:** 강한 상승 에너지가 터졌습니다! 다시 밴드 안으로 회귀할 가능성도 높으니 수익 실현을 준비하세요.")
        elif today['Close'] < safe_val(today.get(l_col)):
            patterns.append("📉 **[볼린저 밴드 하단 이탈]**\n\n📊 **상태:** `주가가 밴드 바닥을 찢고 내려감`\n\n💡 **의미:** 극단적인 투매가 나왔습니다. 다시 밴드 안으로 들어오는 강한 반등 확률이 높습니다.")

    if not patterns:
        patterns.append("⚪ **[현재 특별한 돌파/특이 패턴 없음]**\n\n📊 캔들 모양, 이평선, 보조지표 모두 극단적인 요동 없이 안정적입니다.\n\n💡 **의미:** 현재 진행 중인 추세가 묵묵히 이어질 것이라 판단하는 것이 좋습니다.")
        
    return patterns

def calculate_score(df, fund):
    today = df.iloc[-1]
    
    high_52w = safe_val(fund.get('high_52w'))
    low_52w = safe_val(fund.get('low_52w'))
    
    dist_high = ((high_52w - today['Close']) / high_52w * 100) if high_52w > 0 else 999
    dist_low = ((today['Close'] - low_52w) / low_52w * 100) if low_52w > 0 else 0
    
    vol_avg = safe_val(today.get('Vol_Avg'))
    vol_ratio = (today['Volume'] / vol_avg * 100) if vol_avg > 0 else 0
    
    tv_val = safe_val(today.get('Trading_Value'))
    tv_100m = tv_val / 100000000 
    
    adx_val = safe_val(today.get('ADX_14'))
    roe = safe_val(fund.get('roe'))
    sales_growth = safe_val(fund.get('sales_growth'))
    eps_growth = safe_val(fund.get('eps_growth'))
    
    ma50 = safe_val(today.get('MA50'))
    ma150 = safe_val(today.get('MA150'))
    ma200 = safe_val(today.get('MA200'))
    
    rs_rating = safe_val(today.get('RS_Rating'))
    
    obv_current = safe_val(today.get('OBV'))
    obv_prev = safe_val(df['OBV'].iloc[-5]) if len(df) > 5 else 0

    checks = [
        {
            "label": "[N] 신고가 5% 이내", 
            "value": f"(-{dist_high:.1f}%)" if dist_high != 999 else "(데이터 없음)", 
            "pass": dist_high < 5, 
            "desc": "최근 1년(52주) 최고가에 근접했는지 확인합니다. 신고가 근처에 있는 주식이 시장을 이끄는 '주도주'입니다."
        },
        {
            "label": "[S] 거래대금 & 거래량 폭발", 
            "value": f"({vol_ratio:.0f}%, 당일 {tv_100m:,.0f}억원)", 
            "pass": vol_ratio > 150 and tv_100m >= 10,
            "desc": "최근 20일 평균보다 오늘 거래량이 크게 늘고 거래대금이 터졌는지 봅니다. 세력이나 기관의 대규모 자금 유입을 뜻합니다."
        },
        {
            "label": "[Trend] 이동평균선 정배열", 
            "value": "(50선>150선>200선)", 
            "pass": bool(ma50 > 0 and ma150 > 0 and ma200 > 0 and ma50 > ma150 > ma200), 
            "desc": "중장기 이평선이 차례대로 우상향하는지 확인합니다. 상승장에 진입했음을 알리는 강력한 신호입니다."
        },
        {
            "label": "[L] RS 강도 우상향", 
            "value": "(시장 대비 우위)", 
            "pass": bool(rs_rating > 0), 
            "desc": "코스피/코스닥 지수보다 이 종목이 더 빠르고 강하게 오르고 있는 '대장주'인지 판단합니다."
        },
        {
            "label": "[I] OBV 매집 지표", 
            "value": "(우상향 중)", 
            "pass": bool(len(df) > 5 and obv_current > obv_prev), 
            "desc": "하락일의 거래량보다 상승일의 거래량이 많은지(세력이 물량을 모으고 있는지) 확인합니다."
        },
        {
            "label": "[Trend] ADX 추세 강도", 
            "value": f"({adx_val:.1f})", 
            "pass": adx_val > 25, 
            "desc": "주가의 상승 또는 하락 방향성이 얼마나 확고한지 나타냅니다. 25 이상이면 추세가 훌륭하다는 뜻입니다."
        },
        {
            "label": "[Trend] 바닥권 탈출", 
            "value": f"(+{dist_low:.0f}%)", 
            "pass": dist_low > 30, 
            "desc": "52주 최저가 대비 30% 이상 상승하여 확실하게 바닥을 다지고 올라왔는지 확인합니다."
        },
        {
            "label": "[Quality] ROE 15% 이상", 
            "value": f"({roe*100:.1f}%)", 
            "pass": roe >= 0.15, 
            "desc": "기업이 자기 자본으로 얼마나 효율적으로 돈을 잘 버는지(알짜 수익성) 봅니다."
        },
        {
            "label": "[A] 매출 성장 20% 이상", 
            "value": f"({sales_growth*100:.1f}%)", 
            "pass": sales_growth >= 0.20, 
            "desc": "작년 대비 기업의 전체 매출 덩치가 눈에 띄게 커지고 있는지(폭발적 성장성) 확인합니다."
        },
        {
            "label": "[C] 이익 성장 20% 이상", 
            "value": f"({eps_growth*100:.1f}%)", 
            "pass": eps_growth >= 0.20, 
            "desc": "작년 대비 기업의 실제 순이익이 크게 증가하여 실적이 주가를 뒷받침하는지 봅니다."
        }
    ]
    
    score = sum([1 for c in checks if c['pass']])
    return score, checks, dist_high, vol_ratio

def process_tickers(ticker_list, kospi_df_for_calc, progress_bar=None, status_text=None):
    results = []
    need_save = False
    total = len(ticker_list)
    
    for i, symbol in enumerate(ticker_list):
        if status_text:
            status_text.write(f"⏳ 실시간 분석 중... ({i+1}/{total}) : {symbol}")
            
        df, fund = get_enhanced_data(symbol, kospi_df_for_calc)
        if df is not None:
            if symbol in portfolio and portfolio[symbol].get('name') != fund['name']:
                portfolio[symbol]['name'] = fund['name']
                need_save = True

            score, checks, dist_high, vol_ratio = calculate_score(df, fund)
            patterns = detect_patterns(df) 
            
            results.append({
                'symbol': symbol, 'name': fund['name'], 'score': score,
                'checks': checks, 'patterns': patterns, 'df': df, 'fund': fund,
                'today': df.iloc[-1], 'dist_high': dist_high, 'vol_ratio': vol_ratio,
                'types': portfolio.get(symbol, {}).get('types', []) 
            })
            
        if progress_bar:
            progress_bar.progress(min((i + 1) / total, 1.0))
            
    if need_save: save_portfolio(current_user, portfolio)
    return results

def draw_advanced_chart(df, name, tf_option="일봉"):
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#0066ff' for _, row in df.iterrows()]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    
    if tf_option in ["30분", "1시간"]:
        x_labels = df.index.strftime('%Y-%m-%d %H:%M') 
    else:
        x_labels = df.index.strftime('%Y-%m-%d') 

    customdata_tv = (df['Trading_Value'] / 100000000).fillna(0) if 'Trading_Value' in df.columns else [0]*len(df)

    fig.add_trace(go.Candlestick(
        x=x_labels, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], 
        name='주가', increasing_line_color='#ff3333', decreasing_line_color='#0066ff',
        customdata=customdata_tv,
        hovertemplate='<b>%{x}</b><br>시가: %{open:,.0f}원<br>고가: %{high:,.0f}원<br>저가: %{low:,.0f}원<br>종가: %{close:,.0f}원<br><br><b>💰 거래대금: %{customdata:,.0f}억 원</b><extra></extra>'
    ), row=1, col=1)
    
    if 'MA20' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA20'], line=dict(color='orange', width=1.5), name='20선 (단기)'), row=1, col=1)
    if 'MA50' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA50'], line=dict(color='green', width=1.5), name='50선 (중기)'), row=1, col=1)
    if 'MA150' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA150'], line=dict(color='purple', width=1.5), name='150선 (장기)'), row=1, col=1)
    
    fig.add_trace(go.Bar(
        x=x_labels, y=df['Volume'], marker_color=colors, name='거래량',
        customdata=customdata_tv,
        hovertemplate='<b>%{x}</b><br>거래량: %{y:,.0f}주<br><b>💰 거래대금: %{customdata:,.0f}억 원</b><extra></extra>'
    ), row=2, col=1)
    
    fig.update_layout(
        title=dict(text=f"📈 {name}", font=dict(size=14)), yaxis_title=dict(text="주가 (원)", font=dict(size=11)),
        yaxis2_title=dict(text="거래량", font=dict(size=11)),
        xaxis=dict(type='category', nticks=10), xaxis2=dict(type='category', nticks=10), xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=40, b=10), height=400, showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        font=dict(size=11)
    )
    return fig

# --- UI 레이아웃 시작 ---
st.title("🛡️ 박스 모멘텀 프로: 실전 투자 시스템")

# --- 💡 필수 라이브러리 검사 경고창 (대화면 표출) ---
if not FDR_INSTALLED:
    st.error("""
    🚨 **[필수 조치 필요] 한국 주식 2,700개 정밀 검색 엔진이 연결되지 않았습니다!**
    
    1. 깃허브 `requirements.txt`에 부품을 추가했다면, **반드시 서버를 '재부팅(Reboot)'** 해야 부품이 조립됩니다!
    2. 화면 우측 하단의 **[Manage app]** (또는 톱니바퀴) 클릭 ➔ **[Reboot app]** 을 눌러주세요.
    3. 서버 재부팅이 완료되면 2,700개 종목 한글 검색이 100% 정상 작동합니다.
    """)

# 시장 데이터 로드 (코스피, 코스닥)
kospi_df, kosdaq_df = get_market_data()

# 💡 에러 원인 해결: 종목명 맵핑 변수(krx_map, combined_stocks) 복구 완료!
krx_map = get_krx_names()
combined_stocks = {**FALLBACK_NAMES, **krx_map}
search_list = sorted([f"{name} ({code})" for code, name in combined_stocks.items()])

with st.sidebar:
    st.success(f"👤 **{current_user}**님 접속 중")
    if st.button("🚪 로그아웃"):
        del st.session_state['username']
        st.rerun()
        
    st.markdown("---")
    st.header("⚙️ 내 관심/보유 종목 관리")

    if not FDR_INSTALLED or len(krx_map) < 100:
        st.warning("⚠️ 거래소 서버 차단 방지를 위해 비상용 목록이 활성화되었습니다. 우측의 에러 메시지를 확인하여 서버를 재부팅해주세요.")

    if st.button("🔄 최신 주가 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

    with st.form("add_stock_form", clear_on_submit=True):
        st.write("🔍 **새 종목 추가**")
        stock_category = st.radio("종목 분류 선택", ["🔍 일반 분석 (내가 찾은 종목)", "💡 이유성 추천 (VIP 종목)"], horizontal=True)
        selected_stock = st.selectbox("회사명으로 검색", options=search_list, index=None, placeholder="예: 삼성전자 (초성 및 일부 검색 가능)")
        manual_ticker = st.text_input("또는 종목코드 직접 입력 (미국 주식 등)", placeholder="예: AAPL, TSLA, 005380")
        submitted = st.form_submit_button("➕ 종목 추가")
        
        if submitted:
            target_ticker = ""
            stock_name = ""
            if selected_stock:
                target_ticker = selected_stock.split('(')[-1].replace(')', '').strip()
                stock_name = selected_stock.split('(')[0].strip()
            elif manual_ticker:
                target_ticker = manual_ticker.strip().upper()
                stock_name = target_ticker

            if target_ticker:
                with st.spinner("종목 검색 및 추가 중..."):
                    if len(target_ticker) == 6 and target_ticker.isdigit():
                        try:
                            if not yf.Ticker(target_ticker + ".KS").history(period="1d").empty: target_ticker += ".KS"
                            else: target_ticker += ".KQ"
                        except:
                            target_ticker += ".KS"

                    cat_val = 'recommended' if '추천' in stock_category else 'general'

                    if target_ticker in portfolio:
                        if cat_val not in portfolio[target_ticker].get('types', []):
                            portfolio[target_ticker].setdefault('types', []).append(cat_val)
                            save_portfolio(current_user, portfolio)
                            cat_korean = '이유성 추천' if cat_val == 'recommended' else '일반 분석'
                            st.success(f"[{cat_korean}] 분류가 {portfolio[target_ticker].get('name', target_ticker)}에 추가되었습니다!")
                            st.rerun()
                        else:
                            st.warning("이미 해당 분류에 등록된 종목입니다.")
                    else:
                        if not selected_stock:
                            code_only = target_ticker.split('.')[0]
                            if code_only in combined_stocks: stock_name = combined_stocks[code_only]
                            else:
                                try: stock_name = yf.Ticker(target_ticker).info.get('shortName', target_ticker)
                                except: stock_name = target_ticker

                        portfolio[target_ticker] = {"price": 0.0, "qty": 0, "target": 0.0, "name": stock_name, "note": "", "types": [cat_val], "in_account": False}
                        save_portfolio(current_user, portfolio)
                        st.success(f"{stock_name} 추가 완료!")
                        st.rerun()
            else:
                st.warning("종목을 검색하거나 코드를 입력해주세요.")

    st.markdown("---")
    st.write("### 📂 현재 등록된 리스트")
    for sym in list(portfolio.keys()):
        col1, col2 = st.columns([4, 1])
        name = portfolio[sym].get('name', '')
        types = portfolio[sym].get('types', [])
        icons = ""
        if 'recommended' in types: icons += "💡"
        if 'general' in types: icons += "🔍"
        col1.write(f"• {icons} **{name}** ({sym})")
        if col2.button("❌", key=f"del_sidebar_{sym}"):
            del portfolio[sym]
            save_portfolio(current_user, portfolio)
            st.rerun()

    st.markdown("---")
    min_score = st.slider("스크리닝 최소 점수 필터 (⭐)", 0, 10, 5)

# 전체 관심종목 프로세싱
interest_results = process_tickers(user_tickers, kospi_df)
general_results = [r for r in interest_results if 'general' in r['types']]
recom_results = [r for r in interest_results if 'recommended' in r['types']]

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 대시보드 요약", "🎯 종목 스크리너", "🔍 심층 분석", "💡 이유성 추천!!", "🧮 내 계좌 관리", "📖 투자 마스터 클래스"])

# --- 💡 탭 1: 대폭 업그레이드된 실전 트레이더 대시보드 ---
with tab1:
    st.markdown("### 📊 나의 자산 및 시장 종합 요약")
    
    total_invested_all = 0
    total_current_all = 0
    portfolio_items = []
    action_alerts = []

    for res in interest_results:
        sym = res['symbol']
        p_data = portfolio.get(sym, {})
        qty = int(p_data.get('qty', 0))
        price = float(p_data.get('price', 0))
        target = float(p_data.get('target', 0))
        curr_price = res['today']['Close'] if pd.notna(res['today']['Close']) else 0
        
        if qty > 0:
            invested = qty * price
            curr_val = qty * curr_price
            total_invested_all += invested
            total_current_all += curr_val
            portfolio_items.append({'name': res['name'], 'value': curr_val})
            
            stop_loss = price * 0.93
            if target > 0 and curr_price >= target:
                action_alerts.append(f"🎯 **{res['name']}**: 목표가({target:,.0f}원) 도달! 익절을 고려하세요.")
            elif price > 0 and curr_price <= stop_loss:
                action_alerts.append(f"🚨 **{res['name']}**: 손절가({stop_loss:,.0f}원) 이탈! 원칙적인 리스크 관리가 필요합니다.")

    c1, c2, c3, c4 = st.columns(4)
    if kospi_df is not None and not kospi_df.empty:
        k_t, k_y = kospi_df.iloc[-1]['Close'], kospi_df.iloc[-2]['Close']
        c1.metric("📉 KOSPI 지수", f"{k_t:,.2f}", f"{k_t - k_y:,.2f}")
    if kosdaq_df is not None and not kosdaq_df.empty:
        kq_t, kq_y = kosdaq_df.iloc[-1]['Close'], kosdaq_df.iloc[-2]['Close']
        c2.metric("📈 KOSDAQ 지수", f"{kq_t:,.2f}", f"{kq_t - kq_y:,.2f}")
        
    total_profit_all = total_current_all - total_invested_all
    total_roi_all = (total_profit_all / total_invested_all * 100) if total_invested_all > 0 else 0
    c3.metric("💰 내 총 자산 (평가금액)", f"{total_current_all:,.0f}원")
    c4.metric("📊 총 평가 손익", f"{total_profit_all:,.0f}원", f"{total_roi_all:.2f}%")

    st.markdown("---")
    
    col_chart, col_alert = st.columns([1, 1])
    with col_chart:
        st.subheader("🍩 포트폴리오 비중")
        if portfolio_items:
            fig_pie = go.Figure(data=[go.Pie(
                labels=[item['name'] for item in portfolio_items], 
                values=[item['value'] for item in portfolio_items], 
                hole=.4,
                marker_colors=['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899']
            )])
            fig_pie.update_layout(margin=dict(t=20, b=20, l=0, r=0), height=300, showlegend=True)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("보유 중인 주식이 없습니다. 왼쪽 메뉴에서 종목을 추가하고 계좌 관리에 매수 정보를 입력하세요.")

    with col_alert:
        st.subheader("🚨 AI 실시간 매매 알림")
        if action_alerts:
            for alert in action_alerts:
                if "목표가" in alert: st.success(alert)
                else: st.error(alert)
        else:
            if portfolio_items:
                st.info("현재 목표가 도달이나 손절가 이탈 종목이 없습니다. 평온하게 추세를 즐기세요.")
            else:
                st.info("매수 내역이 없어 분석할 알림이 없습니다.")
                
    st.markdown("---")
    st.subheader("🌟 오늘의 톱 픽 (내 관심종목 중 AI 최고점)")
    best_picks = [r for r in interest_results if r['score'] >= 7]
    if best_picks:
        cols = st.columns(min(len(best_picks), 4))
        for idx, pick in enumerate(best_picks[:4]):
            with cols[idx]:
                st.success(f"**{pick['name']}**")
                st.write(f"점수: **{'⭐' * pick['score']}** ({pick['score']}/10)")
                st.caption(f"현재가: {pick['today']['Close']:,.0f}원")
    else:
        st.info("현재 관심 종목 리스트에 7점 이상의 강력 주도주 신호가 없거나, 등록된 종목이 없습니다.")

with tab2:
    st.subheader("🎯 한국 시장 우량주 & 전 종목 자동 스크리너")
    st.write("시장을 실시간으로 스캔하여 최적 매수 후보를 발굴합니다.")
    
    fast_tickers, all_tickers = get_market_tickers()
    
    scan_option = st.radio(
        "🔎 스크리닝 범위 선택 (2가지 모드 지원)", 
        [f"⚡ 빠른 검색 (코스피/코스닥 대형 우량주 100종목 - 약 1~2분 소요)", 
         f"🕵️ 정밀 검색 (한국 시장 전체 {len(all_tickers):,}여 종목 - 약 15~30분 소요 ⚠️)"],
        horizontal=True
    )

    if st.button("🚀 스크리닝 시작"):
        st.session_state.screener_run = False 
        
        target_list = fast_tickers if "빠른 검색" in scan_option else all_tickers
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        with st.spinner("방대한 주식 데이터를 스캔하고 있습니다. 잠시만 기다려주세요..."):
            st.session_state.screened_results = process_tickers(target_list, kospi_df, progress_bar, status_text)
            st.session_state.screener_run = True
            
        status_text.success("✅ 스크리닝이 완벽하게 완료되었습니다!")
        progress_bar.empty()

    if st.session_state.get('screener_run', False):
        filtered = sorted([r for r in st.session_state.screened_results if r['score'] >= min_score], key=lambda x: x['score'], reverse=True)
        
        st.info("💡 **스캔된 결과는 임시 저장되어 탭을 다녀와도 지워지지 않습니다.** 종목을 클릭하시면 탭 이동 없이 즉시 **차트를 확인**하실 수 있습니다!")
        
        if filtered:
            st.write(f"✅ 총 **{len(filtered)}**개의 유망 종목이 발견되었습니다!")
            for idx, res in enumerate(filtered):
                with st.expander(f"[{res.get('score', 0)}점] {res.get('name', '')} ({res.get('symbol', '')})"):
                    
                    if 'df' in res and res['df'] is not None:
                        chart_fig = draw_advanced_chart(res['df'].tail(120), f"{res.get('name', '')} (일봉)", "일봉")
                        st.plotly_chart(chart_fig, use_container_width=True, key=f"chart_screener_{res['symbol']}_{idx}")
                        
                    safe_curr_price = res['today']['Close'] if pd.notna(res['today']['Close']) else 0
                    st.metric("현재가", f"{safe_curr_price:,.0f}원")
                    
                    checks_data = res.get('checks', [])
                    if not checks_data and 'score_details' in res:
                        if isinstance(res['score_details'], dict):
                            checks_data = [{'label': k, 'pass': bool(v)} for k, v in res['score_details'].items()]
                        elif isinstance(res['score_details'], list):
                            checks_data = res['score_details']
                    
                    passed_labels = [c.get('label', '') for c in checks_data if c.get('pass', False)]
                    if passed_labels:
                        st.write(", ".join(passed_labels[:4]) + " 등")
                    else:
                        st.write("통과한 기본 항목 없음")
                    
                    if st.button("➕ 내 리스트에 추가 (일반 종목으로)", key=f"add_screen_{res.get('symbol', idx)}_{idx}"):
                        sym = res['symbol']
                        if sym not in portfolio:
                            portfolio[sym] = {"price": 0.0, "qty": 0, "target": 0.0, "name": res.get('name', sym), "note": "", "types": ["general"], "in_account": False}
                            save_portfolio(current_user, portfolio)
                            st.toast(f"✅ {res.get('name', sym)}이(가) 추가되었습니다!")
                            st.rerun()
                        elif "general" not in portfolio[sym].get('types', []):
                            portfolio[sym].setdefault('types', []).append("general")
                            save_portfolio(current_user, portfolio)
                            st.toast(f"✅ 일반 분석에 추가되었습니다!")
                            st.rerun()
                        else:
                            st.info("이미 내 리스트에 등록된 종목입니다.")
        else:
            st.warning("⚠️ 현재 필터링 점수를 만족하는 종목이 없습니다. 좌측의 '스크리닝 최소 점수 필터'를 조금 낮춰보세요!")

with tab3:
    st.subheader("🔍 심층 분석 (일반 관심 종목)")
    if not general_results: st.info("현재 일반 관심 종목이 없습니다. 왼쪽 메뉴에서 종목을 추가해보세요.")
    
    for idx, res in enumerate(general_results):
        df_res = res['df']
        today_res = res['today']
        
        curr_price = today_res['Close'] if pd.notna(today_res['Close']) else 0
        open_price = today_res['Open'] if pd.notna(today_res['Open']) else 0
        prev_close = df_res.iloc[-2]['Close'] if len(df_res) >= 2 else open_price
        
        change = curr_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close > 0 else 0
        
        if change > 0:
            trend_str = f":red[▲ {abs(change):,.0f}원 (+{pct_change:.2f}%)]"
        elif change < 0:
            trend_str = f":blue[▼ {abs(change):,.0f}원 ({pct_change:.2f}%)]"
        else:
            trend_str = f"보합 0원 (0.00%)"
            
        expander_title = f"🔍 {res['name']} ({res['symbol']}) 분석 리포트 | 현재가: {curr_price:,.0f}원 {trend_str} | 시가: {open_price:,.0f}원"

        with st.expander(expander_title, expanded=False):
            tf_option = st.radio("⏱️ 차트 시간 주기", ["30분", "1시간", "일봉", "주봉", "월봉", "분기봉", "년봉"], horizontal=True, index=2, key=f"tf_gen_{res['symbol']}_{idx}")
            chart_df = get_chart_data(res['symbol'], tf_option)
            if chart_df is not None:
                display_count = 120 if len(chart_df) > 120 else len(chart_df)
                st.plotly_chart(draw_advanced_chart(chart_df.tail(display_count), f"{res['name']} ({tf_option} 차트)", tf_option), use_container_width=True, key=f"chart_tab3_{res['symbol']}_{idx}")
                st.markdown("---")
                st.write(f"**[ 📊 AI {tf_option} 캔들 & 차트 패턴 분석 ]**")
                for pattern in detect_patterns(chart_df): st.info(pattern)
            else: st.warning("데이터를 불러올 수 없습니다.")

            st.markdown("---")
            st.write("**[ 📊 종목 정밀 체크리스트 (CANSLIM & 추세) ]**")
            
            checks_data = res.get('checks', [])
            if not checks_data and 'score_details' in res:
                if isinstance(res['score_details'], dict):
                    checks_data = [{'label': k, 'value': '', 'desc': '', 'pass': bool(v)} for k, v in res['score_details'].items()]
                elif isinstance(res['score_details'], list):
                    checks_data = res['score_details']

            for check in checks_data:
                icon = "✅" if check.get('pass', False) else "❌"
                st.markdown(f"{icon} **{check.get('label', '')}** {check.get('value', '')}")
                if check.get('desc', ''):
                    st.caption(f"↳ {check['desc']}")

            st.markdown("---")
            is_in_account = portfolio.get(res['symbol'], {}).get('in_account', False)
            if not is_in_account:
                if st.button(f"💰 이 종목을 '내 계좌'에 추가하여 실전 관리 시작", key=f"add_acc_{res['symbol']}_{idx}"):
                    portfolio[res['symbol']]['in_account'] = True
                    portfolio[res['symbol']]['price'] = int(curr_price)
                    portfolio[res['symbol']]['target'] = int(curr_price * 1.2)
                    save_portfolio(current_user, portfolio)
                    st.success("내 계좌 관리에 성공적으로 추가되었습니다!")
                    st.rerun()
            else:
                st.success("✅ 현재 '내 계좌 관리' 탭에서 실전 투자 성과를 추적 중인 종목입니다.")

with tab4:
    st.subheader("💡 이유성 추천!! (VIP 추천 종목)")
    total_invested_yoo, total_current_val_yoo = 0, 0
    if not recom_results: st.info("왼쪽 메뉴에서 '💡 이유성 추천 종목'을 선택 후 추가해보세요.")

    for idx, res in enumerate(recom_results):
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0.0, "qty": 0, "target": 0.0, "note": ""})
        
        df_res = res['df']
        today_res = res['today']
        
        curr_price = today_res['Close'] if pd.notna(today_res['Close']) else 0
        open_price = today_res['Open'] if pd.notna(today_res['Open']) else 0
        prev_close = df_res.iloc[-2]['Close'] if len(df_res) >= 2 else open_price
        
        change = curr_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close > 0 else 0
        
        if change > 0:
            trend_str = f":red[▲ {abs(change):,.0f}원 (+{pct_change:.2f}%)]"
        elif change < 0:
            trend_str = f":blue[▼ {abs(change):,.0f}원 ({pct_change:.2f}%)]"
        else:
            trend_str = f"보합 0원 (0.00%)"
            
        expander_title = f"🌟 {res['name']} ({res['symbol']}) - 추천 관리 | 현재가: {curr_price:,.0f}원 {trend_str} | 시가: {open_price:,.0f}원"

        with st.expander(expander_title, expanded=True):
            new_note = st.text_area("✍️ 비고 (이유성 추천 사유 및 코멘트)", value=p_data.get('note', ''), placeholder="추천 사유를 적어주세요!", key=f"y_n_{sym}_{idx}")
            tf_option_rec = st.radio("⏱️ 차트 시간 주기", ["30분", "1시간", "일봉", "주봉", "월봉", "분기봉", "년봉"], horizontal=True, index=2, key=f"tf_rec_{sym}_{idx}")
            chart_df_rec = get_chart_data(res['symbol'], tf_option_rec)
            
            if chart_df_rec is not None:
                st.plotly_chart(draw_advanced_chart(chart_df_rec.tail(120), f"{res['name']} ({tf_option_rec} 차트)", tf_option_rec), use_container_width=True, key=f"chart_tab4_{sym}_{idx}")
                st.markdown("---")
                st.write(f"**[ 📊 AI {tf_option_rec} 캔들 & 차트 패턴 분석 ]**")
                for pattern in detect_patterns(chart_df_rec): st.info(pattern)
            
            st.markdown("---")
            st.write("**[ 📊 종목 정밀 체크리스트 (CANSLIM & 추세) ]**")
            
            checks_data = res.get('checks', [])
            if not checks_data and 'score_details' in res:
                if isinstance(res['score_details'], dict):
                    checks_data = [{'label': k, 'value': '', 'desc': '', 'pass': bool(v)} for k, v in res['score_details'].items()]
                elif isinstance(res['score_details'], list):
                    checks_data = res['score_details']

            for check in checks_data:
                icon = "✅" if check.get('pass', False) else "❌"
                st.markdown(f"{icon} **{check.get('label', '')}** {check.get('value', '')}")
                if check.get('desc', ''):
                    st.caption(f"↳ {check['desc']}")
            
            st.markdown("---")
            
            saved_price_yoo = float(p_data.get('price', 0))
            base_price_yoo = saved_price_yoo if saved_price_yoo > 0 else curr_price
            base_label_yoo = "나의 매수가" if saved_price_yoo > 0 else "현재가"
            
            saved_target_yoo = float(p_data.get('target', 0))
            default_target_yoo = saved_target_yoo if saved_target_yoo > 0 else base_price_yoo * 1.2
            
            st.info(f"🤖 **[AI 매매 기준선 가이드 ({base_label_yoo} 기준)]** 자동 추천 매도가 **{base_price_yoo * 1.2:,.0f}원** (+20%)  |  자동 추천 손절가 **{base_price_yoo * 0.93:,.0f}원** (-7%)")
            
            c1, c2, c3 = st.columns(3)
            new_price = c1.number_input("추천 매수 단가 (원)", value=int(base_price_yoo), step=100, format="%d", key=f"y_p_{sym}_{idx}")
            new_qty = c2.number_input("매수 수량 (주)", value=int(p_data.get('qty', 0)), step=1, format="%d", key=f"y_q_{sym}_{idx}")
            new_target = c3.number_input("목표 매도 단가 (원)", value=int(default_target_yoo), step=100, format="%d", key=f"y_t_{sym}_{idx}")

            if new_qty > 0:
                invested = new_price * new_qty
                curr_val = curr_price * new_qty
                profit = curr_val - invested
                roi = (profit / invested) * 100 if invested > 0 else 0
                
                expected_profit = (new_target - new_price) * new_qty
                expected_roi = (expected_profit / invested) * 100 if invested > 0 else 0
                
                total_invested_yoo += invested
                total_current_val_yoo += curr_val

                st.markdown(f"""
                <div style='background-color: #f0f2f6; padding: 15px; border-radius: 10px; margin: 15px 0;'>
                    <p style='margin: 0; font-size: 16px;'>▶ <b>현재 평가 손익:</b> <span style='color: {"#ff3333" if profit > 0 else "#0066ff"}; font-weight: bold;'>{profit:,.0f}원 ({roi:.2f}%)</span> &nbsp;|&nbsp; 투자금액: {invested:,.0f}원</p>
                    <p style='margin: 8px 0 0 0; font-size: 16px;'>🎯 <b>예상 수익금 (매도시):</b> <span style='color: #e67e22; font-weight: bold;'>{expected_profit:,.0f}원 ({expected_roi:.2f}%)</span></p>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("##### 🛎️ 매매 액션 가이드 (시스템 판정)")
                stop_loss_price = new_price * 0.93
                if new_target > 0 and curr_price >= new_target:
                    st.success(f"🎯 **[목표가 도달]** 축하합니다! 설정하신 매도가({new_target:,.0f}원)를 돌파했습니다. **분할 매도 또는 전량 익절**을 고려하세요.")
                elif new_price > 0 and curr_price <= stop_loss_price:
                    st.error(f"🚨 **[손절가 이탈]** 현재가({curr_price:,.0f}원)가 손절선({stop_loss_price:,.0f}원) 아래로 내려갔습니다. 원칙에 따라 **기계적 손절**을 강력히 권장합니다.")
                elif new_price > 0:
                    if roi > 0:
                        st.info(f"🟢 **[보유 유지 - 수익 중]** 매도가({new_target:,.0f}원)까지 {new_target - curr_price:,.0f}원 남았습니다.")
                    else:
                        st.warning(f"🟡 **[보유 유지 - 손실 중]** 손절선({stop_loss_price:,.0f}원)까지 {curr_price - stop_loss_price:,.0f}원 여유가 있습니다.")

            if st.button("💾 추천 정보 저장", key=f"y_save_{sym}_{idx}"):
                portfolio[sym]['price'] = int(new_price)
                portfolio[sym]['qty'] = int(new_qty)
                portfolio[sym]['target'] = int(new_target)
                portfolio[sym]['note'] = new_note
                portfolio[sym]['name'] = res['name']
                save_portfolio(current_user, portfolio)
                st.success("저장 완료!")
                st.rerun()

    st.markdown("---")
    st.subheader("📊 추천 포트폴리오 성과 현황")
    if total_invested_yoo > 0:
        total_profit_yoo = total_current_val_yoo - total_invested_yoo
        total_roi_yoo = (total_profit_yoo / total_invested_yoo) * 100
        st.metric("총 매수 금액", f"{total_invested_yoo:,.0f}원")
        st.metric("총 평가 금액", f"{total_current_val_yoo:,.0f}원")
        st.metric("총 수익률", f"{total_profit_yoo:,.0f}원", f"{total_roi_yoo:.2f}%")
    else:
        st.info("현재 등록된 추천 종목 매수 이력이 없습니다.")

with tab5:
    st.subheader("🧮 내 계좌 관리 (일반 종목)")
    
    total_invested = 0
    total_current_val = 0
    
    acc_results = [r for r in general_results if portfolio.get(r['symbol'], {}).get('in_account', False)]

    if not acc_results:
        st.info("현재 계좌에서 관리 중인 종목이 없습니다. '심층 분석' 탭 하단에서 실전 투자 종목을 추가해 보세요.")
    
    for idx, res in enumerate(acc_results):
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0, "qty": 0, "target": 0, "note": ""})
        
        df_res = res['df']
        today_res = res['today']
        
        curr_price = today_res['Close'] if pd.notna(today_res['Close']) else 0
        open_price = today_res['Open'] if pd.notna(today_res['Open']) else 0
        prev_close = df_res.iloc[-2]['Close'] if len(df_res) >= 2 else open_price
        
        change = curr_price - prev_close
        pct_change = (change / prev_close) * 100 if prev_close > 0 else 0
        
        if change > 0:
            trend_str = f":red[▲ {abs(change):,.0f}원 (+{pct_change:.2f}%)]"
        elif change < 0:
            trend_str = f":blue[▼ {abs(change):,.0f}원 ({pct_change:.2f}%)]"
        else:
            trend_str = f"보합 0원 (0.00%)"
            
        expander_title = f"💼 {res['name']} ({res['symbol']}) | 현재가: {curr_price:,.0f}원 {trend_str} | 시가: {open_price:,.0f}원"
        
        with st.expander(expander_title, expanded=True):
            
            new_note = st.text_area("✍️ 비고 (나만의 투자 코멘트 및 전략)", value=p_data.get('note', ''), placeholder="이 종목을 매수한 이유나 향후 매매 전략을 자유롭게 기록하세요!", key=f"gen_n_{sym}_{idx}")
            
            saved_price_gen = float(p_data.get('price', 0))
            base_price_gen = saved_price_gen if saved_price_gen > 0 else curr_price
            base_label_gen = "나의 매수가" if saved_price_gen > 0 else "현재가"
            
            saved_target_gen = float(p_data.get('target', 0))
            default_target_gen = saved_target_gen if saved_target_gen > 0 else base_price_gen * 1.2
            
            st.info(f"🤖 **[AI 매매 기준선 가이드 ({base_label_gen} 기준)]** 자동 추천 매도가 **{base_price_gen * 1.2:,.0f}원** (+20%)  |  자동 추천 손절가 **{base_price_gen * 0.93:,.0f}원** (-7%)")
            
            c1, c2, c3 = st.columns(3)
            new_price = c1.number_input("매수 단가 (원)", value=int(base_price_gen), step=100, format="%d", key=f"gen_p_{sym}_{idx}")
            new_qty = c2.number_input("보유 수량 (주)", value=int(p_data.get('qty', 0)), step=1, format="%d", key=f"gen_q_{sym}_{idx}")
            new_target = c3.number_input("목표 매도 단가 (원)", value=int(default_target_gen), step=100, format="%d", key=f"gen_t_{sym}_{idx}")
            
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("💾 이 종목 정보 저장", key=f"gen_save_{sym}_{idx}"):
                    portfolio[sym].update({'price': int(new_price), 'qty': int(new_qty), 'target': int(new_target), 'note': new_note, 'name': res['name']})
                    save_portfolio(current_user, portfolio)
                    st.success("저장 완료!")
                    st.rerun()
            with col_btn2:
                if st.button("🗑️ 계좌 관리에서 제외 (매도 완료)", key=f"gen_rem_{sym}_{idx}"):
                    portfolio[sym]['in_account'] = False
                    save_portfolio(current_user, portfolio)
                    st.rerun()

            if new_qty > 0:
                invested = new_price * new_qty
                curr_val = curr_price * new_qty
                profit = curr_val - invested
                roi = (profit / invested) * 100 if invested > 0 else 0
                
                expected_profit = (new_target - new_price) * new_qty
                expected_roi = (expected_profit / invested) * 100 if invested > 0 else 0
                
                total_invested += invested
                total_current_val += curr_val

                st.markdown(f"""
                <div style='background-color: #f0f2f6; padding: 15px; border-radius: 10px; margin: 15px 0;'>
                    <p style='margin: 0; font-size: 16px;'>▶ <b>현재 평가 손익:</b> <span style='color: {"#ff3333" if profit > 0 else "#0066ff"}; font-weight: bold;'>{profit:,.0f}원 ({roi:.2f}%)</span> &nbsp;|&nbsp; 투자금액: {invested:,.0f}원</p>
                    <p style='margin: 8px 0 0 0; font-size: 16px;'>🎯 <b>예상 수익금 (매도시):</b> <span style='color: #e67e22; font-weight: bold;'>{expected_profit:,.0f}원 ({expected_roi:.2f}%)</span></p>
                </div>
                """, unsafe_allow_html=True)

                st.markdown("##### 🛎️ 매매 액션 가이드 (시스템 판정)")
                stop_loss_price = new_price * 0.93
                if new_target > 0 and curr_price >= new_target:
                    st.success(f"🎯 **[목표가 도달]** 축하합니다! 설정하신 매도가({new_target:,.0f}원)를 돌파했습니다.")
                elif new_price > 0 and curr_price <= stop_loss_price:
                    st.error(f"🚨 **[손절가 이탈]** 현재가({curr_price:,.0f}원)가 손절선({stop_loss_price:,.0f}원) 아래로 내려갔습니다.")
                elif new_price > 0:
                    if roi > 0:
                        st.info(f"🟢 **[보유 유지 - 수익 중]** 매도가({new_target:,.0f}원)까지 {new_target - curr_price:,.0f}원 남았습니다.")
                    else:
                        st.warning(f"🟡 **[보유 유지 - 손실 중]** 손절선({stop_loss_price:,.0f}원)까지 {curr_price - stop_loss_price:,.0f}원 여유가 있습니다.")

    st.markdown("---")
    st.subheader("📊 일반 포트폴리오 성과 현황")
    if total_invested > 0:
        total_profit = total_current_val - total_invested
        total_roi = (total_profit / total_invested) * 100
        st.metric("총 매수 금액", f"{total_invested:,.0f}원")
        st.metric("총 평가 금액", f"{total_current_val:,.0f}원")
        st.metric("총 수익률", f"{total_profit:,.0f}원", f"{total_roi:.2f}%")
    else:
        st.info("등록된 투자 정보가 없습니다. 종목별로 매수 정보를 저장해 보세요.")

# --- 💡 탭 6: 텍스트 내용 대규모 복구 ---
with tab6:
    st.header("📖 박스 모멘텀 투자 마스터 클래스")
    st.markdown("""
    이 대시보드는 **윌리엄 오닐(William O'Neil)의 CANSLIM 기법**과 **마크 미너비니(Mark Minervini)의 VCP 패턴**, 그리고 월스트리트 거장들의 **리스크 관리 원칙**을 하나의 시스템으로 통합한 실전 투자 시스템입니다.

    ---

    ### 🏆 제 1원칙 : 전설들의 주식 발굴법 (CANSLIM)
    주식 시장에서 텐배거(10배 오르는 주식)를 잡기 위해서는 단순한 '가치'가 아니라 '모멘텀'과 '성장'을 보아야 합니다. 
    * **C (Current Earnings):** 최근 분기 주당순이익(EPS)이 전년 동기 대비 20% 이상 크게 증가했는가?
    * **A (Annual Earnings):** 연간 순이익이 최근 3년 이상 꾸준히 성장하고 있는가?
    * **N (New):** 새로운 제품, 새로운 경영진, 혹은 주가가 **신고가(New High)**를 돌파하고 있는가?
    * **S (Supply and Demand):** 주식 수가 적고 거래량이 폭발하는가? (세력과 기관의 매집)
    * **L (Leader):** 시장 평균 지수(코스피/코스닥)를 압도하는 강력한 **시장 주도주**인가?
    * **I (Institutional Sponsorship):** 기관 투자자와 외국인들의 뒷받침이 있는가?
    * **M (Market Direction):** 전체 시장(코스피 지수)이 상승장(Up-trend)인가?

    ---

    ### 🛡️ 제 2원칙 : 리스크와 자금 관리 (절대 원칙)
    아무리 좋은 종목이라도 계좌를 지키지 못하면 시장에서 퇴출당합니다. 아래 원칙을 **기계처럼** 지키십시오.

    #### 1. 손절선은 무조건 -7~8%
    > "손실이 -8%를 넘어가기 전에 무조건 팔아라. 이유를 불문하라."
    * 주식이 내 매수가 대비 **-7% ~ -8%** 에 도달하면 미련 없이 던지세요. 
    * 시스템의 `내 계좌 관리`에 있는 **자동 추천 손절선** 알림이 울리면 곧바로 실행에 옮겨야 합니다.

    #### 2. 최소 1:2의 손익비 (Risk/Reward Ratio)
    * 내가 -7%의 손실을 감수한다면, 기대 수익은 최소 그 2배인 **+15% 이상**이어야 합니다.
    * 10번 싸워서 4번만 이기고 6번 지더라도, (4번 * +15%) - (6번 * -7%) = +18%로 계좌는 우상향합니다.

    #### 3. 수익 실현 (익절) 타이밍
    * 주가가 매수가 대비 **+20% ~ +25%** 상승했을 때 미련 없이 수익을 실현하세요.
    * 만약 주도주라고 판단되어 더 끌고 가고 싶다면, 50%의 물량만 익절하고 나머지 물량은 20일선이 깨질 때까지 보유하는 **'분할 매도'** 전략을 취하십시오.

    ---

    ### 📉 제 3원칙 : 차트의 숨은 뜻 읽기 (마크 미너비니 VCP)
    AI가 찾아내는 패턴에는 세력의 심리가 숨어있습니다.
    * **변동성 축소 패턴 (VCP):** 주가가 크게 요동치다가 점차 등락폭이 작아지고 거래량이 바짝 마를 때가 폭풍 전야입니다. 이때 거래량이 폭발(평균 대비 1.5배 이상)하며 양봉이 터지면 무조건 매수입니다.
    * **거래량 폭발:** 주가가 오를 때는 거래량이 터지고, 주가가 내릴 때는 거래량이 말라야 튼튼한 상승 추세입니다.
    * **이동평균선 정배열:** 50일선이 150일선 위에, 150일선이 200일선 위에 있어야만 진짜 상승 랠리가 시작됩니다. 역배열(200선 아래 주가)인 주식은 아무리 싸보여도 절대 쳐다보지 마십시오.
    """)

st.caption(f"시스템 정상 작동 중 | 마지막 업데이트: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} (KST)")
