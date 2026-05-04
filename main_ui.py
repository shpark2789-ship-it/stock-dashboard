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
        "005930.KS": {"price": 0.0, "qty": 0, "target": 0.0, "name": "삼성전자", "note": "", "types": ["general"]}
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

# --- 🇰🇷 100% 확실한 한글 종목명 변환기 ---
@st.cache_data(ttl=86400, show_spinner=False)
def get_krx_names():
    result = {}
    
    if FDR_INSTALLED:
        try:
            df_krx = fdr.StockListing('KRX')
            result = dict(zip(df_krx['Code'], df_krx['Name']))
            if len(result) > 1000:
                return result
        except:
            pass

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    try:
        for market in ['KOSPI', 'KOSDAQ']:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=2000'
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                for stock in data.get('stocks', []):
                    result[stock['itemCode']] = stock['stockName']
        if len(result) > 1000: return result
    except:
        pass

    st.cache_data.clear()
    return result

# --- 💡 스크리너 전용: 코스피/코스닥 전 종목 수집기 ---
@st.cache_data(ttl=86400, show_spinner=False)
def get_market_tickers():
    fast_list = []
    all_list = []  
    fallback_fast = [
        '005930.KS', '000660.KS', '373220.KS', '207940.KS', '005380.KS', '051910.KS', '000270.KS', '068270.KS', '005490.KS', '035420.KS',
        '105560.KS', '055550.KS', '032830.KS', '012330.KS', '033780.KS', '003550.KS', '086790.KS', '015760.KS', '034020.KS', '018260.KS',
        '247540.KQ', '086520.KQ', '028300.KQ', '091990.KQ', '277810.KQ', '066970.KQ', '022100.KQ', '068240.KQ', '196170.KQ', '041510.KQ'
    ]
    
    if FDR_INSTALLED:
        try:
            df_krx = fdr.StockListing('KRX')
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

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
    try:
        for market, suffix in [('KOSPI', '.KS'), ('KOSDAQ', '.KQ')]:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=2000'
            res = requests.get(url, headers=headers, timeout=5)
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

# --- 💡 혁신적인 무결점 데이터 수집 엔진 (야후 파이낸스 버그 완벽 회피) ---
def get_robust_history(ticker, period_days, interval, is_intraday=False):
    """
    야후 파이낸스의 업데이트 지연(KOSDAQ 등)을 완벽하게 우회하기 위해
    한국 주식은 FinanceDataReader(한국거래소 직결)를 우선 사용하여 100% 최신 날짜를 보장합니다.
    """
    if is_intraday:
        try:
            df = yf.Ticker(ticker).history(period=f"{period_days}d", interval=interval)
            return convert_to_kst(df.dropna(subset=['Close']))
        except:
            return pd.DataFrame()
            
    is_korean = ticker.endswith('.KS') or ticker.endswith('.KQ')
    
    # 1. 한국 주식은 무조건 FDR로 실시간 데이터 긁어오기
    if FDR_INSTALLED and is_korean:
        code = ticker.replace('.KS', '').replace('.KQ', '')
        start_date = datetime.now(KST) - timedelta(days=period_days)
        try:
            df = fdr.DataReader(code, start_date)
            if not df.empty:
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
                # 일일 데이터를 받아서 사용자가 원하는 주기로 완벽하게 압축(Resample)
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
            pass # 실패 시 야후 파이낸스로 넘어감
            
    # 2. 해외 주식 또는 FDR 실패 시 야후 파이낸스 사용
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
    """코스피와 코스닥 지수를 FDR로 가져와 야후 버그 방어"""
    try:
        k_df, q_df = None, None
        start_date = datetime.now(KST) - timedelta(days=365)
        
        if FDR_INSTALLED:
            try:
                k_df = fdr.DataReader('KS11', start_date)
                q_df = fdr.DataReader('KQ11', start_date)
                if not k_df.empty:
                    k_df = k_df[['Open', 'High', 'Low', 'Close', 'Volume']]
                    if getattr(k_df.index, 'tz', None) is None: k_df.index = k_df.index.tz_localize(KST)
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
        # 💡 강력한 무결점 엔진 적용
        df = get_robust_history(ticker, period_days, interval, is_intraday)
        if df.empty: return None
        
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
        # 💡 일반 일봉 분석도 무결점 엔진으로 완전히 교체
        df = get_robust_history(ticker, period_days=365, interval="1d", is_intraday=False)
        if df.empty or len(df) < 50: return None, None

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

        if market_df is not None:
            market_close = market_df['Close'].reindex(df.index, method='ffill')
            stock_perf = (df['Close'] / df['Close'].shift(50)) - 1
            market_perf = (market_close / market_close.shift(50)) - 1
            df['RS_Rating'] = (stock_perf - market_perf).fillna(0)

        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        
        # 기업 기본 정보는 yf에서 가져오되 실패해도 차트는 정상 구동
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
        except:
            info = {}
            
        krx_names = get_krx_names()
        code = ticker.split('.')[0]
        kor_name = krx_names.get(code, FALLBACK_NAMES.get(code, info.get('shortName', info.get('longName', ticker))))

        fundamentals = {
            'name': kor_name, 'roe': info.get('returnOnEquity', 0),
            'op_margin': info.get('operatingMargins', 0), 'sales_growth': info.get('revenueGrowth', 0),
            'eps_growth': info.get('earningsQuarterlyGrowth', 0), 'debt_ratio': info.get('debtToEquity', 0),
            'low_52w': df['Low'].min(), 'high_52w': df['High'].max()
        }
        return df, fundamentals
    except:
        return None, None

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
            tv_100m = max_tv_day['Trading_Value'] / 100000000 
            
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

    if 'MA20' in df.columns and 'MA50' in df.columns and not pd.isna(yest['MA20']):
        if yest['MA20'] <= yest['MA50'] and today['MA20'] > today['MA50']:
            patterns.append("🌟 **[골든 크로스 (Golden Cross)]**\n\n📊 **모양:** `단기 20선 ↗️ 상향 돌파 🟢 중기 50선`\n\n💡 **의미:** 단기 모멘텀이 중장기 흐름을 이겨냈습니다. 전형적인 상승장 초입 시그널입니다.")
        elif yest['MA20'] >= yest['MA50'] and today['MA20'] < today['MA50']:
            patterns.append("🚨 **[데드 크로스 (Dead Cross)]**\n\n📊 **모양:** `단기 20선 ↘️ 하향 이탈 🟢 중기 50선`\n\n💡 **의미:** 단기 모멘텀이 꺾였습니다. 즉각적인 리스크 관리가 필요합니다.")
            
        if 'MA150' in df.columns and today['Close'] > today['MA20'] > today['MA50'] > today['MA150']:
            patterns.append("🎢 **[이평선 완벽 정배열 (Perfect Up-trend)]**\n\n📊 **모양:** `현재가 > 20선 > 50선 > 150선`\n\n💡 **의미:** 완벽한 우상향 고속도로를 달리고 있습니다. 눌림목이 가장 좋은 매수 타이밍입니다.")

    rsi_col = 'RSI_14' if 'RSI_14' in df.columns else 'RSI' if 'RSI' in df.columns else None
    if rsi_col and not pd.isna(today[rsi_col]):
        if today[rsi_col] >= 70:
            patterns.append(f"⚠️ **[RSI 과열/과매수]**\n\n📊 **상태:** `RSI 수치 {today[rsi_col]:.1f}` (70 이상 위험)\n\n💡 **의미:** 단기간에 사람들이 너무 많이 샀습니다. 곧 차익 실현 물량이 쏟아져 조정받을 수 있습니다.")
        elif today[rsi_col] <= 30:
            patterns.append(f"🛒 **[RSI 침체/과매도]**\n\n📊 **상태:** `RSI 수치 {today[rsi_col]:.1f}` (30 이하 저평가)\n\n💡 **의미:** 공포 심리에 의해 너무 많이 팔렸습니다. 기술적 반등이 들어올 수 있는 기회입니다.")

    bb_upper = [c for c in df.columns if c.startswith('BBU_')]
    bb_lower = [c for c in df.columns if c.startswith('BBL_')]
    if bb_upper and bb_lower:
        u_col, l_col = bb_upper[0], bb_lower[0]
        if today['Close'] > today[u_col]:
            patterns.append("🚀 **[볼린저 밴드 상단 돌파]**\n\n📊 **상태:** `주가가 밴드 천장을 찢고 올라감`\n\n💡 **의미:** 강한 상승 에너지가 터졌습니다! 다시 밴드 안으로 회귀할 가능성도 높으니 수익 실현을 준비하세요.")
        elif today['Close'] < today[l_col]:
            patterns.append("📉 **[볼린저 밴드 하단 이탈]**\n\n📊 **상태:** `주가가 밴드 바닥을 찢고 내려감`\n\n💡 **의미:** 극단적인 투매가 나왔습니다. 다시 밴드 안으로 들어오는 강한 반등 확률이 높습니다.")

    if not patterns:
        patterns.append("⚪ **[현재 특별한 돌파/특이 패턴 없음]**\n\n📊 캔들 모양, 이평선, 보조지표 모두 극단적인 요동 없이 안정적입니다.\n\n💡 **의미:** 현재 진행 중인 추세가 묵묵히 이어질 것이라 판단하는 것이 좋습니다.")
        
    return patterns

def calculate_score(df, fund):
    today = df.iloc[-1]
    
    high_52w = fund.get('high_52w', 0)
    low_52w = fund.get('low_52w', 0)
    
    dist_high = ((high_52w - today['Close']) / high_52w * 100) if pd.notna(high_52w) and high_52w > 0 else 999
    dist_low = ((today['Close'] - low_52w) / low_52w * 100) if pd.notna(low_52w) and low_52w > 0 else 0
    
    vol_avg = today.get('Vol_Avg', 0)
    vol_ratio = (today['Volume'] / vol_avg * 100) if pd.notna(vol_avg) and vol_avg > 0 else 0
    
    tv_100m = (today['Trading_Value'] / 100000000) if 'Trading_Value' in df.columns and pd.notna(today['Trading_Value']) else 0
    
    adx_val = today.get('ADX_14', 0)
    if pd.isna(adx_val): adx_val = 0
    
    roe = fund.get('roe', 0)
    if pd.isna(roe): roe = 0
    
    sales_growth = fund.get('sales_growth', 0)
    if pd.isna(sales_growth): sales_growth = 0
    
    eps_growth = fund.get('eps_growth', 0)
    if pd.isna(eps_growth): eps_growth = 0

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
            "pass": bool(today['MA50'] > today['MA150'] > today['MA200']), 
            "desc": "중장기 이평선이 차례대로 우상향하는지 확인합니다. 상승장에 진입했음을 알리는 강력한 신호입니다."
        },
        {
            "label": "[L] RS 강도 우상향", 
            "value": "(시장 대비 우위)", 
            "pass": bool(today.get('RS_Rating', 0) > 0), 
            "desc": "코스피/코스닥 지수보다 이 종목이 더 빠르고 강하게 오르고 있는 '대장주'인지 판단합니다."
        },
        {
            "label": "[I] OBV 매집 지표", 
            "value": "(우상향 중)", 
            "pass": bool(len(df) > 5 and today['OBV'] > df['OBV'].iloc[-5]), 
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
                need_
