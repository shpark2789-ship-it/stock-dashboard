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
    
    # 1. 최우선: FinanceDataReader (가장 확실함, IP 차단 없음)
    if FDR_INSTALLED:
        try:
            df_krx = fdr.StockListing('KRX') # 코스피, 코스닥, 코넥스 전종목
            result = dict(zip(df_krx['Code'], df_krx['Name']))
            if len(result) > 1000:
                return result
        except:
            pass

    # 2. 차선책: 네이버 API 우회
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

# --- 데이터 수집 및 분석 함수 ---
@st.cache_data(ttl=3600)
def get_market_data():
    try:
        kospi = yf.Ticker("^KS11")
        df = kospi.history(period="1y")
        if df.empty: return None
        return df
    except:
        return None

@st.cache_data(ttl=300, show_spinner=False)
def get_chart_data(ticker, tf_option):
    tf_map = {
        "30분": ("60d", "30m"), "1시간": ("730d", "1h"), "일봉": ("2y", "1d"),
        "주봉": ("5y", "1wk"), "월봉": ("10y", "1mo"), "분기봉": ("max", "3mo"), "년봉": ("max", "1mo") 
    }
    period, interval = tf_map.get(tf_option, ("2y", "1d"))
    
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, interval=interval)
        if df.empty: return None
        if tf_option == "년봉":
            df = df.resample('YE').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
            
        df['MA20'] = ta.sma(df['Close'], length=20)
        df['MA50'] = ta.sma(df['Close'], length=50)
        df['MA150'] = ta.sma(df['Close'], length=150)
        df['MA200'] = ta.sma(df['Close'], length=200)
        return df
    except:
        return None

def get_enhanced_data(ticker, market_df):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        if df.empty or len(df) < 200: return None, None

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

        if market_df is not None:
            combined = pd.concat([df['Close'], market_df['Close']], axis=1, keys=['stock', 'market']).dropna()
            stock_perf = (df['Close'] / df['Close'].shift(50)) - 1
            market_perf = (market_df['Close'] / market_df['Close'].shift(50)) - 1
            df['RS_Rating'] = stock_perf - market_perf

        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        info = stock.info
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
    
    today, yest, prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    
    if yest['Close'] < yest['Open'] and today['Close'] > today['Open'] and today['Open'] <= yest['Close'] and today['Close'] >= yest['Open']:
        patterns.append("🟢 **[상승 장악형 (Bullish Engulfing)]**\n\n📊 **모양:** `[직전: 얇은 파란 기둥] ➔ [최근: 두꺼운 빨간 기둥]`\n\n💡 **의미:** 직전의 하락을 덮어버리는 거대한 매수세가 터졌습니다. 바닥권일 경우 강력한 반등 시그널입니다.")
    elif yest['Close'] > yest['Open'] and today['Close'] < today['Open'] and today['Open'] >= yest['Close'] and today['Close'] <= yest['Open']:
        patterns.append("🔴 **[하락 장악형 (Bearish Engulfing)]**\n\n📊 **모양:** `[직전: 얇은 빨간 기둥] ➔ [최근: 두꺼운 파란 기둥]`\n\n💡 **의미:** 상승을 짓누르는 거대한 매도 폭탄이 쏟아졌습니다. 고점일 경우 강한 매도 시그널입니다.")
        
    if today['Close'] > today['Open'] and yest['Close'] > yest['Open'] and prev['Close'] > prev['Open'] and today['Close'] > yest['Close'] and yest['Close'] > prev['Close']:
        patterns.append("🔥 **[적삼병 (Three White Soldiers)]**\n\n📊 **모양:** `[📈빨강] ➔ [📈더 높은 빨강] ➔ [📈더 높은 빨강]`\n\n💡 **의미:** 3연속 양봉이 출현했습니다. 세력이 주가를 본격적으로 밀어올리는 대세 상승 확률이 높습니다.")
    elif today['Close'] < today['Open'] and yest['Close'] < yest['Open'] and prev['Close'] < prev['Open'] and today['Close'] < yest['Close'] and yest['Close'] < prev['Close']:
        patterns.append("❄️ **[흑삼병 (Three Black Crows)]**\n\n📊 **모양:** `[📉파랑] ➔ [📉더 낮은 파랑] ➔ [📉더 낮은 파랑]`\n\n💡 **의미:** 3연속 음봉이 출현했습니다. 매도세가 강해 추가 하락에 대비해야 합니다.")
        
    if 'MA20' in df.columns and 'MA50' in df.columns and not pd.isna(yest['MA20']):
        if yest['MA20'] <= yest['MA50'] and today['MA20'] > today['MA50']:
            patterns.append("🌟 **[골든 크로스 (Golden Cross)]**\n\n📊 **모양:** `단기 20선 ↗️ 상승 돌파 🟢 중기 50선`\n\n💡 **의미:** 단기 생명선이 중기 추세선을 뚫고 올라갔습니다. 본격적인 상승랠리가 기대됩니다.")
        elif yest['MA20'] >= yest['MA50'] and today['MA20'] < today['MA50']:
            patterns.append("🚨 **[데드 크로스 (Dead Cross)]**\n\n📊 **모양:** `단기 20선 ↘️ 하락 이탈 🟢 중기 50선`\n\n💡 **의미:** 단기 생명선이 꺾였습니다. 즉각적인 리스크 관리가 필요합니다.")
            
    body = abs(today['Close'] - today['Open'])
    lower_tail = today['Open'] - today['Low'] if today['Close'] > today['Open'] else today['Close'] - today['Low']
    upper_tail = today['High'] - today['Close'] if today['Close'] > today['Open'] else today['High'] - today['Open']
    
    if body > 0:
        if lower_tail > body * 2 and upper_tail < body * 0.5:
            patterns.append("🔨 **[망치형 캔들 (Hammer)]**\n\n📊 **모양:** `[위: 짧은 몸통] ➕ [아래: 매우 긴 꼬리]`\n\n💡 **의미:** 강력한 저가 매수세가 하락을 이겨내고 끌어올렸습니다. 지지선 형성 확률이 높습니다.")
        elif upper_tail > body * 2 and lower_tail < body * 0.5:
            patterns.append("☄️ **[유성형 캔들 (Shooting Star)]**\n\n📊 **모양:** `[위: 매우 긴 꼬리] ➕ [아래: 짧은 몸통]`\n\n💡 **의미:** 상승하다 대규모 매도 물량을 맞고 억눌린 형태입니다. 단기 고점일 수 있습니다.")
            
    if not patterns:
        patterns.append("⚪ **[특이 패턴 없음]**\n\n📊 현재 이 주기에서는 뚜렷한 반전 캔들이나 이평선 크로스가 없습니다. 기존 추세가 이어집니다.")
        
    return patterns

def calculate_score(df, fund):
    today = df.iloc[-1]
    dist_high = ((fund['high_52w'] - today['Close']) / fund['high_52w']) * 100
    dist_low = ((today['Close'] - fund['low_52w']) / fund['low_52w']) * 100
    vol_ratio = (today['Volume'] / today['Vol_Avg']) * 100 if today['Vol_Avg'] > 0 else 0
    adx_val = today.get('ADX_14', 0)

    score_details = {
        f"[N] 신고가 5% 이내 (-{dist_high:.1f}%)": 1 if dist_high < 5 else 0,
        f"[S] 거래량 150% 폭발 ({vol_ratio:.0f}%)": 1 if vol_ratio > 150 else 0,
        f"[Trend] 일봉 이평선 정배열 (50>150>200)": 1 if (today['MA50'] > today['MA150'] > today['MA200']) else 0,
        f"[L] RS 강도 우상향 (시장대비 우위)": 1 if (today.get('RS_Rating', 0) > 0) else 0,
        f"[I] OBV 누적거래량 우상향 (매집)": 1 if (len(df) > 5 and today['OBV'] > df['OBV'].iloc[-5]) else 0,
        f"[Trend] ADX 추세강화 25↑ ({adx_val:.1f})": 1 if adx_val > 25 else 0,
        f"[Trend] 신저가 대비 30%↑ 회복 (+{dist_low:.0f}%)": 1 if dist_low > 30 else 0,
        f"[Quality] ROE 15%↑ ({fund['roe']*100:.1f}%)": 1 if (fund['roe'] and fund['roe'] >= 0.15) else 0,
        f"[A] 매출 성장 20%↑ ({fund['sales_growth']*100:.1f}%)": 1 if (fund['sales_growth'] and fund['sales_growth'] >= 0.20) else 0,
        f"[C] 이익 성장 20%↑ ({fund['eps_growth']*100:.1f}%)": 1 if (fund['eps_growth'] and fund['eps_growth'] >= 0.20) else 0
    }
    return sum(score_details.values()), score_details, dist_high, vol_ratio

def process_tickers(ticker_list):
    results = []
    need_save = False
    for symbol in ticker_list:
        df, fund = get_enhanced_data(symbol, market_df)
        if df is not None:
            if symbol in portfolio and portfolio[symbol].get('name') != fund['name']:
                portfolio[symbol]['name'] = fund['name']
                need_save = True

            score, details, dist_high, vol_ratio = calculate_score(df, fund)
            patterns = detect_patterns(df) 
            
            results.append({
                'symbol': symbol, 'name': fund['name'], 'score': score,
                'score_details': details, 'patterns': patterns, 'df': df, 'fund': fund,
                'today': df.iloc[-1], 'dist_high': dist_high, 'vol_ratio': vol_ratio,
                'types': portfolio.get(symbol, {}).get('types', []) 
            })
    if need_save: save_portfolio(current_user, portfolio)
    return results

def draw_advanced_chart(df, name):
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#0066ff' for _, row in df.iterrows()]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    
    if len(df) > 0 and (df.index[0].hour > 0 or df.index[0].minute > 0):
        x_labels = df.index.strftime('%y-%m-%d %H:%M') 
    else:
        x_labels = df.index.strftime('%y-%m-%d') 

    fig.add_trace(go.Candlestick(x=x_labels, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='주가', increasing_line_color='#ff3333', decreasing_line_color='#0066ff'), row=1, col=1)
    
    if 'MA20' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA20'], line=dict(color='orange', width=1.5), name='20선 (단기)'), row=1, col=1)
    if 'MA50' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA50'], line=dict(color='green', width=1.5), name='50선 (중기)'), row=1, col=1)
    if 'MA150' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA150'], line=dict(color='purple', width=1.5), name='150선 (장기)'), row=1, col=1)
    
    fig.add_trace(go.Bar(x=x_labels, y=df['Volume'], marker_color=colors, name='거래량'), row=2, col=1)
    
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

market_df = get_market_data()
krx_map = get_krx_names()
combined_stocks = {**FALLBACK_NAMES, **krx_map}
search_list = [f"{name} ({code})" for code, name in combined_stocks.items()]
search_list.sort()

with st.sidebar:
    st.success(f"👤 **{current_user}**님 접속 중")
    if st.button("🚪 로그아웃"):
        del st.session_state['username']
        st.rerun()
        
    st.markdown("---")
    st.header("⚙️ 내 관심/보유 종목 관리")

    # 💡 1단계: 필수 엔진 설치 경고창 (라이브러리 없을 경우 표출)
    if not FDR_INSTALLED:
        st.error("🚨 **[필수 조치]** 완벽한 종목 검색을 위해 깃허브의 `requirements.txt` 파일 맨 아래에 `finance-datareader` 를 꼭 추가해주세요!")
    elif len(krx_map) < 100:
        st.warning("⚠️ 거래소 서버 통신 지연으로 일부 종목만 검색됩니다.")

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

                        portfolio[target_ticker] = {"price": 0.0, "qty": 0, "target": 0.0, "name": stock_name, "note": "", "types": [cat_val]}
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
    min_score = st.slider("스크리닝 최소 점수 필터 (⭐)", 0, 12, 6)

interest_results = process_tickers(user_tickers)
general_results = [r for r in interest_results if 'general' in r['types']]
recom_results = [r for r in interest_results if 'recommended' in r['types']]

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 대시보드", "🎯 종목 스크리너", "🔍 심층 분석", "💡 이유성 추천!!", "🧮 내 계좌 관리", "📖 투자 마스터 클래스"])

with tab1:
    if market_df is not None:
        m_today, m_prev = market_df.iloc[-1]['Close'], market_df.iloc[-2]['Close']
        m_trend = "상승장 (M조건 충족)" if market_df.iloc[-1]['Close'] > market_df['Close'].rolling(20).mean().iloc[-1] else "하락/조정장 (보수적 접근)"
        st.metric("KOSPI 지수", f"{m_today:,.2f}", f"{m_today - m_prev:,.2f}")
        now_kst = datetime.now(timezone(timedelta(hours=9)))
        is_market_open = now_kst.weekday() < 5 and (9 <= now_kst.hour < 15 or (now_kst.hour == 15 and now_kst.minute <= 30))
        st.caption(f"**현재 시장 방향성:** {m_trend} &nbsp;|&nbsp; **시장 상태:** {'🟢 장중' if is_market_open else '🔴 장 마감'}")

    st.subheader("🏆 관심 종목 하이라이트")
    best_picks = [r for r in interest_results if r['score'] >= 8]
    if best_picks:
        for idx, pick in enumerate(best_picks):
            with st.container():
                icons = "💡" if 'recommended' in pick['types'] else ""
                icons += "🔍" if 'general' in pick['types'] else ""
                st.success(f"{icons} **{pick['name']}**")
                st.metric("종합 점수", f"{pick['score']} / 12", f"{'⭐' * pick['score']}")
    else:
        st.info("현재 관심 종목 중 8점 이상의 강력한 주도주 신호가 없거나, 등록된 종목이 없습니다.")

    st.markdown("---")
    st.subheader("📋 관심 종목 실시간 현황")
    if interest_results:
        for i, res in enumerate(interest_results):
            with st.container():
                icons = "💡" if 'recommended' in res['types'] else ""
                icons += "🔍" if 'general' in res['types'] else ""
                st.markdown(f"{icons} **{res['name']}** ({res['symbol']})")
                st.write(f"점수: {'⭐' * res['score']}")
                st.progress(res['score'] / 12)
                st.write(f"현재가: **{res['today']['Close']:,.0f}원**")
                st.write("---")

with tab2:
    st.subheader("🎯 한국 시장 우량주 자동 스크리너")
    SCREEN_LIST = ['005930.KS', '000660.KS', '035720.KS', '035420.KS', '005380.KS', '000270.KS', '068270.KS', '051910.KS', '006400.KS', '005490.KS']
    if st.button("🚀 전체 시장 스크리닝 시작"):
        with st.spinner("우량주를 분석 중입니다..."):
            st.session_state.screened_results = process_tickers(SCREEN_LIST)
            st.session_state.screener_run = True

    if st.session_state.get('screener_run', False):
        filtered = sorted([r for r in st.session_state.screened_results if r['score'] >= min_score], key=lambda x: x['score'], reverse=True)
        if filtered:
            st.write(f"✅ 총 **{len(filtered)}**개의 유망 종목이 발견되었습니다!")
            for idx, res in enumerate(filtered):
                with st.expander(f"[{res['score']}점] {res['name']} ({res['symbol']})"):
                    st.metric("현재가", f"{res['today']['Close']:,.0f}원")
                    st.write(", ".join([k for k, v in res['score_details'].items() if v == 1][:4]) + " 등")
                    if st.button("➕ 내 리스트에 추가 (일반 종목으로)", key=f"add_screen_{res['symbol']}_{idx}"):
                        if res['symbol'] not in portfolio:
                            portfolio[res['symbol']] = {"price": 0.0, "qty": 0, "target": 0.0, "name": res['name'], "note": "", "types": ["general"]}
                            save_portfolio(current_user, portfolio)
                            st.toast(f"✅ {res['name']}이(가) 추가되었습니다!")
                            st.rerun()
                        elif "general" not in portfolio[res['symbol']].get('types', []):
                            portfolio[res['symbol']].setdefault('types', []).append("general")
                            save_portfolio(current_user, portfolio)
                            st.toast(f"✅ 일반 분석에 추가되었습니다!")
                            st.rerun()

with tab3:
    st.subheader("🔍 심층 분석 (일반 관심 종목)")
    if not general_results: st.info("현재 일반 관심 종목이 없습니다.")
    for idx, res in enumerate(general_results):
        with st.expander(f"🔍 {res['name']} ({res['symbol']}) 분석 리포트", expanded=False):
            tf_option = st.radio("⏱️ 차트 시간 주기", ["30분", "1시간", "일봉", "주봉", "월봉", "분기봉", "년봉"], horizontal=True, index=2, key=f"tf_gen_{res['symbol']}_{idx}")
            chart_df = get_chart_data(res['symbol'], tf_option)
            if chart_df is not None:
                display_count = 120 if len(chart_df) > 120 else len(chart_df)
                st.plotly_chart(draw_advanced_chart(chart_df.tail(display_count), f"{res['name']} ({tf_option} 차트)"), use_container_width=True, key=f"chart_tab3_{res['symbol']}_{idx}")
                st.markdown("---")
                st.write(f"**[ 📊 AI {tf_option} 캔들 & 차트 패턴 분석 ]**")
                for pattern in detect_patterns(chart_df): st.info(pattern)
            else: st.warning("데이터를 불러올 수 없습니다.")

            st.markdown("---")
            st.write("**[ 기본적 분석 (Fundamental) ]**")
            st.metric("ROE", f"{res['fund']['roe'] * 100:.1f}%" if res['fund']['roe'] else "N/A")
            st.write("**[ 체크리스트 ]**")
            for label, val in res['score_details'].items():
                if "[C]" in label or "[A]" in label or "[N]" in label or "[S]" in label or "[L]" in label or "[I]" in label:
                    st.markdown(f"{'✅' if val else '❌'} **{label}**")

with tab4:
    st.subheader("💡 이유성 추천!! (VIP 추천 종목)")
    total_invested_yoo, total_current_val_yoo = 0, 0
    if not recom_results: st.info("왼쪽 메뉴에서 '💡 이유성 추천 종목'을 선택 후 추가해보세요.")

    for idx, res in enumerate(recom_results):
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0.0, "qty": 0, "target": 0.0, "note": ""})
        curr_price = res['today']['Close']

        with st.expander(f"🌟 {res['name']} ({res['symbol']}) - 추천 관리 (현재가: {curr_price:,.0f}원)", expanded=True):
            new_note = st.text_area("✍️ 비고 (이유성 추천 사유 및 코멘트)", value=p_data.get('note', ''), placeholder="추천 사유를 적어주세요!", key=f"y_n_{sym}_{idx}")
            tf_option_rec = st.radio("⏱️ 차트 시간 주기", ["30분", "1시간", "일봉", "주봉", "월봉", "분기봉", "년봉"], horizontal=True, index=2, key=f"tf_rec_{sym}_{idx}")
            chart_df_rec = get_chart_data(res['symbol'], tf_option_rec)
            
            if chart_df_rec is not None:
                st.plotly_chart(draw_advanced_chart(chart_df_rec.tail(120), f"{res['name']} ({tf_option_rec} 차트)"), use_container_width=True, key=f"chart_tab4_{sym}_{idx}")
                st.markdown("---")
                st.write(f"**[ 📊 AI {tf_option_rec} 캔들 & 차트 패턴 분석 ]**")
                for pattern in detect_patterns(chart_df_rec): st.info(pattern)
            st.markdown("---")

            new_price = st.number_input("추천 매수 단가 (원)", value=float(p_data.get('price', 0)), step=100.0, key=f"y_p_{sym}_{idx}")
            new_qty = st.number_input("매수 수량 (주)", value=int(p_data.get('qty', 0)), step=1, key=f"y_q_{sym}_{idx}")
            new_target = st.number_input("목표 단가 (원)", value=float(p_data.get('target', new_price * 1.2)), step=100.0, key=f"y_t_{sym}_{idx}")

            if st.button("💾 추천 정보 저장", key=f"y_save_{sym}_{idx}"):
                portfolio[sym]['price'] = new_price
                portfolio[sym]['qty'] = new_qty
                portfolio[sym]['target'] = new_target
                portfolio[sym]['note'] = new_note
                portfolio[sym]['name'] = res['name']
                save_portfolio(current_user, portfolio)
                st.success("저장 완료!")
                st.rerun()

            if new_qty > 0:
                invested = new_price * new_qty
                curr_val = curr_price * new_qty
                total_invested_yoo += invested
                total_current_val_yoo += curr_val

    st.markdown("---")
    st.subheader("📊 추천 포트폴리오 성과 현황")
    if total_invested_yoo > 0:
        st.metric("총 수익률", f"{total_current_val_yoo - total_invested_yoo:,.0f}원", f"{((total_current_val_yoo - total_invested_yoo) / total_invested_yoo) * 100:.2f}%")

with tab5:
    st.subheader("🧮 내 계좌 관리 (일반 종목)")
    for idx, res in enumerate(general_results):
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0.0, "qty": 0, "target": 0.0})
        with st.expander(f"💼 {res['name']} ({res['symbol']}) - 현재가: {res['today']['Close']:,.0f}원", expanded=True):
            new_price = st.number_input("매수 단가 (원)", value=float(p_data['price']), step=100.0, key=f"gen_p_{sym}_{idx}")
            new_qty = st.number_input("보유 수량 (주)", value=int(p_data.get('qty', 0)), step=1, key=f"gen_q_{sym}_{idx}")
            new_target = st.number_input("목표 단가 (원)", value=float(p_data.get('target', new_price * 1.2)), step=100.0, key=f"gen_t_{sym}_{idx}")
            if st.button("💾 이 종목 정보 저장", key=f"gen_save_{sym}_{idx}"):
                portfolio[sym].update({'price': new_price, 'qty': new_qty, 'target': new_target, 'name': res['name']})
                save_portfolio(current_user, portfolio)
                st.success("저장 완료!")
                st.rerun()

with tab6:
    st.header("📖 투자 마스터 클래스")
    st.markdown("CANSLIM과 리스크 관리 원칙을 설명하는 공간입니다.")

st.caption(f"시스템 정상 작동 중 | 마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
