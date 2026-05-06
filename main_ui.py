import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
import sys
import json
import requests
import io
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# --- 실행 방식 체크 ---
if __name__ == "__main__":
    if not st.runtime.exists():
        print("\n" + "=" * 50)
        print("🚨 실행 오류: 이 프로그램은 'python' 명령어로 실행할 수 없습니다.")
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
                        if 'type' in v and v['type']:
                            migrated_data[base_sym]['types'].append(v['type'])
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
    try:
        get_doc_ref(username).set(data)
    except:
        pass

portfolio = load_portfolio(current_user)
user_tickers = list(portfolio.keys())

# --- 🇰🇷 한글 종목명 변환기 ---
FALLBACK_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오", "035420": "NAVER", "005380": "현대차", 
    "000270": "기아", "068270": "셀트리온", "051910": "LG화학", "006400": "삼성SDI", "005490": "POSCO홀딩스", 
    "105560": "KB금융", "055550": "신한지주", "032830": "삼성생명", "033780": "KT&G", "003550": "LG",
    "000810": "삼성화재", "012330": "현대모비스", "015760": "한국전력", "096770": "SK이노베이션", "086790": "하나금융지주"
}

@st.cache_data(ttl=86400, show_spinner=False)
def get_krx_names():
    result = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        for market in ['KOSPI', 'KOSDAQ']:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=2000'
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                for stock in res.json().get('stocks', []):
                    result[stock['itemCode']] = stock['stockName']
        if len(result) > 1000: return result
    except: pass
    st.cache_data.clear()
    return result

# --- 데이터 수집 및 분석 함수 (🔥캐싱을 통한 속도 극대화🔥) ---
@st.cache_data(ttl=300, show_spinner=False)
def get_market_data():
    try:
        df = yf.Ticker("^KS11").history(period="1y")
        return df if not df.empty else None
    except: return None

@st.cache_data(ttl=300, show_spinner=False)
def get_chart_data(ticker, tf_option):
    tf_map = {
        "30분": ("60d", "30m"), "1시간": ("730d", "1h"), "일봉": ("2y", "1d"),
        "주봉": ("5y", "1wk"), "월봉": ("10y", "1mo"), "분기봉": ("max", "3mo"), "년봉": ("max", "1mo")
    }
    period, interval = tf_map.get(tf_option, ("2y", "1d"))
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
        if df.empty: return None
        if tf_option == "년봉":
            df = df.resample('YE').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna()
        df['MA20'] = ta.sma(df['Close'], length=20)
        df['MA50'] = ta.sma(df['Close'], length=50)
        df['MA150'] = ta.sma(df['Close'], length=150)
        df['MA200'] = ta.sma(df['Close'], length=200)
        return df
    except: return None

# 🔥 핵심 최적화: 매 탭 이동시 연산을 막기 위한 강력한 캐싱 적용
@st.cache_data(ttl=300, show_spinner=False)
def get_enhanced_data(ticker):
    market_df = get_market_data()
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        if df.empty or len(df) < 200: return None, None

        df['MA20'], df['MA50'] = ta.sma(df['Close'], length=20), ta.sma(df['Close'], length=50)
        df['MA150'], df['MA200'] = ta.sma(df['Close'], length=150), ta.sma(df['Close'], length=200)

        bbands = ta.bbands(df['Close'], length=20, std=2)
        if bbands is not None: df = pd.concat([df, bbands], axis=1)

        df['RSI'] = ta.rsi(df['Close'], length=14)
        df['OBV'] = ta.obv(df['Close'], df['Volume'])
        adx = ta.adx(df['High'], df['Low'], df['Close'], length=14)
        if adx is not None: df = pd.concat([df, adx], axis=1)

        if market_df is not None:
            combined = pd.concat([df['Close'], market_df['Close']], axis=1, keys=['stock', 'market']).dropna()
            df['RS_Rating'] = ((df['Close'] / df['Close'].shift(50)) - 1) - ((market_df['Close'] / market_df['Close'].shift(50)) - 1)

        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        info = stock.info
        krx_names = get_krx_names()
        code = ticker.split('.')[0]
        kor_name = krx_names.get(code, FALLBACK_NAMES.get(code, info.get('shortName', info.get('longName', ticker))))

        fundamentals = {
            'name': kor_name,
            'eps_growth': info.get('earningsQuarterlyGrowth', 0),
            'sales_growth': info.get('revenueGrowth', 0),
            'roe': info.get('returnOnEquity', 0),
            'op_margin': info.get('operatingMargins', 0),
            'debt_ratio': info.get('debtToEquity', 0),
            'low_52w': df['Low'].min(),
            'high_52w': df['High'].max()
        }
        return df, fundamentals
    except:
        return None, None

def detect_patterns(df):
    patterns = []
    if len(df) < 5: return patterns
    today, yest, prev = df.iloc[-1], df.iloc[-2], df.iloc[-3]
    
    if yest['Close'] < yest['Open'] and today['Close'] > today['Open'] and today['Open'] <= yest['Close'] and today['Close'] >= yest['Open']:
        patterns.append("🟢 **[상승 장악형 (Bullish Engulfing)]**\n\n📊 **모양:** `[직전: 파란 기둥] ➔ [최근: 덮어버린 빨간 기둥]`\n\n💡 **의미:** 하락을 완전히 덮는 거대한 매수세 출현. 바닥권일 경우 강력한 반등 시그널.")
    elif yest['Close'] > yest['Open'] and today['Close'] < today['Open'] and today['Open'] >= yest['Close'] and today['Close'] <= yest['Open']:
        patterns.append("🔴 **[하락 장악형 (Bearish Engulfing)]**\n\n📊 **모양:** `[직전: 빨간 기둥] ➔ [최근: 덮어버린 파란 기둥]`\n\n💡 **의미:** 상승을 짓누르는 거대한 매도 폭탄. 고점일 경우 하락 시그널.")
        
    if today['Close'] > today['Open'] > yest['Close'] > yest['Open'] > prev['Close'] > prev['Open']:
        patterns.append("🔥 **[적삼병]**\n\n📊 **모양:** `[상승]➔[상승]➔[상승]`\n\n💡 **의미:** 3연속 상승 양봉 출현. 대세 상승 전환 확률 상승.")
    elif today['Close'] < today['Open'] < yest['Close'] < yest['Open'] < prev['Close'] < prev['Open']:
        patterns.append("❄️ **[흑삼병]**\n\n📊 **모양:** `[하락]➔[하락]➔[하락]`\n\n💡 **의미:** 3연속 하락 음봉. 매도세가 몹시 강해 추가 폭락 대비 필요.")
        
    if 'MA20' in df.columns and 'MA50' in df.columns and not pd.isna(yest['MA20']):
        if yest['MA20'] <= yest['MA50'] and today['MA20'] > today['MA50']:
            patterns.append("🌟 **[골든 크로스]**\n\n📊 **모양:** `단기 20선 ↗️ 돌파 🟢 중기 50선`\n\n💡 **의미:** 본격적인 상승랠리 기대 지점.")
        elif yest['MA20'] >= yest['MA50'] and today['MA20'] < today['MA50']:
            patterns.append("🚨 **[데드 크로스]**\n\n📊 **모양:** `단기 20선 ↘️ 이탈 🟢 중기 50선`\n\n💡 **의미:** 즉각적인 리스크 관리(비중축소) 필요.")
            
    body = abs(today['Close'] - today['Open'])
    lower_tail = today['Open'] - today['Low'] if today['Close'] > today['Open'] else today['Close'] - today['Low']
    upper_tail = today['High'] - today['Close'] if today['Close'] > today['Open'] else today['High'] - today['Open']
    
    if body > 0:
        if lower_tail > body * 2 and upper_tail < body * 0.5:
            patterns.append("🔨 **[망치형 캔들]**\n\n📊 **모양:** `[위쪽 짧은 몸통]➕[긴 아래꼬리]`\n\n💡 **의미:** 저가 매수세 유입. 지지선 형성 가능성 높음.")
        elif upper_tail > body * 2 and lower_tail < body * 0.5:
            patterns.append("☄️ **[유성형(역망치) 캔들]**\n\n📊 **모양:** `[긴 윗꼬리]➕[아래쪽 짧은 몸통]`\n\n💡 **의미:** 대규모 매도세 저항. 단기 고점 주의.")
            
    if not patterns:
        patterns.append("⚪ **[특이 패턴 없음]**\n\n📊 현재 뚜렷한 반전 캔들이나 이평선 크로스 패턴 미발견. 기존 추세 지속 중.")
    return patterns

def calculate_score(df, fund):
    today = df.iloc[-1]
    dist_high = ((fund['high_52w'] - today['Close']) / fund['high_52w']) * 100
    dist_low = ((today['Close'] - fund['low_52w']) / fund['low_52w']) * 100
    vol_ratio = (today['Volume'] / today['Vol_Avg']) * 100 if today['Vol_Avg'] > 0 else 0

    score_details = {
        f"[N] 신고가 5% 이내 (-{dist_high:.1f}%)": 1 if dist_high < 5 else 0,
        f"[S] 거래량 150% 폭발 ({vol_ratio:.0f}%)": 1 if vol_ratio > 150 else 0,
        f"[Trend] 일봉 이평선 정배열": 1 if (today['MA50'] > today['MA150'] > today['MA200']) else 0,
        f"[L] RS 강도 우상향": 1 if (today.get('RS_Rating', 0) > 0) else 0,
        f"[I] OBV 우상향 (매집)": 1 if (len(df) > 5 and today['OBV'] > df['OBV'].iloc[-5]) else 0,
        f"[Trend] ADX 25↑ ({today.get('ADX_14', 0):.1f})": 1 if today.get('ADX_14', 0) > 25 else 0,
        f"[Trend] 신저가 대비 30%↑ 반등": 1 if dist_low > 30 else 0,
        f"[Quality] ROE 15%↑": 1 if (fund['roe'] and fund['roe'] >= 0.15) else 0,
        f"[A] 매출성장 20%↑": 1 if (fund['sales_growth'] and fund['sales_growth'] >= 0.20) else 0,
        f"[C] 이익성장 20%↑": 1 if (fund['eps_growth'] and fund['eps_growth'] >= 0.20) else 0,
        f"[Quality] 영업이익률 10%↑": 1 if (fund['op_margin'] and fund['op_margin'] >= 0.10) else 0,
        f"[Quality] 부채비율 150%↓": 1 if (fund['debt_ratio'] and fund['debt_ratio'] <= 150) else 0
    }
    return sum(score_details.values()), score_details, dist_high, vol_ratio

def process_tickers(ticker_list):
    results = []
    need_save = False
    for symbol in ticker_list:
        df, fund = get_enhanced_data(symbol)
        if df is not None:
            if symbol in portfolio and portfolio[symbol].get('name') != fund['name']:
                portfolio[symbol]['name'] = fund['name']
                need_save = True

            score, details, dist_high, vol_ratio = calculate_score(df, fund)
            results.append({
                'symbol': symbol, 'name': fund['name'], 'score': score,
                'score_details': details, 'df': df, 'fund': fund,
                'today': df.iloc[-1], 'dist_high': dist_high, 'vol_ratio': vol_ratio,
                'types': portfolio.get(symbol, {}).get('types', []) 
            })
    if need_save: save_portfolio(current_user, portfolio)
    return results

def draw_advanced_chart(df, name):
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#0066ff' for _, row in df.iterrows()]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    x_labels = df.index.strftime('%y-%m-%d %H:%M') if len(df) > 0 and (df.index[0].hour > 0 or df.index[0].minute > 0) else df.index.strftime('%y-%m-%d')
    
    fig.add_trace(go.Candlestick(x=x_labels, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='주가', increasing_line_color='#ff3333', decreasing_line_color='#0066ff'), row=1, col=1)
    if 'MA20' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA20'], line=dict(color='orange', width=1.5), name='20선'), row=1, col=1)
    if 'MA50' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA50'], line=dict(color='green', width=1.5), name='50선'), row=1, col=1)
    if 'MA150' in df.columns: fig.add_trace(go.Scatter(x=x_labels, y=df['MA150'], line=dict(color='purple', width=1.5), name='150선'), row=1, col=1)
    fig.add_trace(go.Bar(x=x_labels, y=df['Volume'], marker_color=colors, name='거래량'), row=2, col=1)
    
    fig.update_layout(
        title=dict(text=f"📈 {name}", font=dict(size=14)),
        yaxis_title=dict(text="주가 (원)", font=dict(size=11)),
        yaxis2_title=dict(text="거래량", font=dict(size=11)),
        xaxis=dict(type='category', nticks=10), xaxis2=dict(type='category', nticks=10),
        xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10), height=400,
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        font=dict(size=11)
    )
    return fig

# --- UI 레이아웃 시작 ---
st.title("🛡️ 박스 모멘텀 프로: 실전 투자 시스템")

market_df = get_market_data()
combined_stocks = {**FALLBACK_NAMES, **get_krx_names()}
search_list = sorted([f"{name} ({code})" for code, name in combined_stocks.items()])

with st.sidebar:
    st.success(f"👤 **{current_user}**님 접속 중")
    if st.button("🚪 로그아웃", help="다른 닉네임으로 접속"):
        del st.session_state['username']
        st.rerun()
        
    st.markdown("---")
    st.header("⚙️ 내 관심/보유 종목 관리")

    if st.button("🔄 최신 주가 데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

    with st.form("add_stock_form", clear_on_submit=True):
        st.write("🔍 **새 종목 추가**")
        stock_category = st.radio("종목 분류 선택", ["🔍 일반 분석 (내가 찾은 종목)", "💡 이유성 추천 (VIP 종목)"], horizontal=True)
        selected_stock = st.selectbox("회사명 검색", options=search_list, index=None, placeholder="예: 삼성전자")
        manual_ticker = st.text_input("또는 종목코드 직접 입력", placeholder="예: AAPL, TSLA, 005380")
        
        if st.form_submit_button("➕ 종목 추가"):
            target_ticker = selected_stock.split('(')[-1].replace(')', '').strip() if selected_stock else manual_ticker.strip().upper()
            stock_name = selected_stock.split('(')[0].strip() if selected_stock else target_ticker

            if target_ticker:
                with st.spinner("종목 검색 및 추가 중..."):
                    if len(target_ticker) == 6 and target_ticker.isdigit():
                        try:
                            target_ticker += ".KS" if not yf.Ticker(target_ticker + ".KS").history(period="1d").empty else ".KQ"
                        except: target_ticker += ".KS"

                    cat_val = 'recommended' if '추천' in stock_category else 'general'

                    if target_ticker in portfolio:
                        if cat_val not in portfolio[target_ticker].get('types', []):
                            portfolio[target_ticker].setdefault('types', []).append(cat_val)
                            save_portfolio(current_user, portfolio)
                            st.success(f"[{'이유성 추천' if cat_val == 'recommended' else '일반 분석'}] 분류 추가 완료!")
                            st.rerun()
                        else: st.warning("이미 해당 분류에 등록된 종목입니다.")
                    else:
                        if not selected_stock:
                            code_only = target_ticker.split('.')[0]
                            stock_name = combined_stocks.get(code_only, target_ticker)
                        portfolio[target_ticker] = {"price": 0.0, "qty": 0, "target": 0.0, "name": stock_name, "note": "", "types": [cat_val], "in_account": False}
                        save_portfolio(current_user, portfolio)
                        st.success(f"{stock_name} 추가 완료!")
                        st.rerun()
            else:
                st.warning("검색하거나 코드를 입력하세요.")

    st.markdown("---")
    st.write("### 📂 현재 등록된 리스트")
    for sym in list(portfolio.keys()):
        c1, c2 = st.columns([4, 1])
        types = portfolio[sym].get('types', [])
        icons = ("💡" if 'recommended' in types else "") + ("🔍" if 'general' in types else "")
        c1.write(f"• {icons} **{portfolio[sym].get('name', '')}** ({sym})")
        if c2.button("❌", key=f"del_{sym}"):
            del portfolio[sym]
            save_portfolio(current_user, portfolio)
            st.rerun()
            
    st.markdown("---")
    min_score = st.slider("스크리닝 최소 점수 필터 (⭐)", 0, 12, 6)

# 메인 데이터 로딩 (캐시가 적용되어 매우 빠름)
interest_results = process_tickers(user_tickers)
general_results = [r for r in interest_results if 'general' in r['types']]
recom_results = [r for r in interest_results if 'recommended' in r['types']]

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 대시보드", "🎯 종목 스크리너", "🔍 심층 분석", "💡 이유성 추천!!", "🧮 내 계좌 관리", "📖 투자 전략"])

with tab1:
    if market_df is not None:
        m_today, m_prev = market_df.iloc[-1]['Close'], market_df.iloc[-2]['Close']
        m_trend = "상승장 (M조건 충족)" if m_today > market_df['Close'].rolling(20).mean().iloc[-1] else "하락/조정장 (보수적 접근)"
        st.metric("KOSPI 지수", f"{m_today:,.2f}", f"{m_today - m_prev:,.2f}")
        now_kst = datetime.now(timezone(timedelta(hours=9)))
        market_status = "🟢 장중 (실시간 데이터)" if now_kst.weekday() < 5 and (9 <= now_kst.hour < 15 or (now_kst.hour == 15 and now_kst.minute <= 30)) else "🔴 장 마감"
        st.caption(f"**방향성:** {m_trend} | **상태:** {market_status}")

    st.subheader("🏆 관심 종목 하이라이트")
    best_picks = [r for r in interest_results if r['score'] >= 8]
    if best_picks:
        for p in best_picks:
            icons = ("💡" if 'recommended' in p['types'] else "") + ("🔍" if 'general' in p['types'] else "")
            st.success(f"{icons} **{p['name']}** - 점수: {p['score']}/12 {'⭐'*p['score']}")
    else: st.info("8점 이상의 주도주 신호가 없습니다.")

    st.markdown("---")
    st.subheader("📋 관심 종목 실시간 현황")
    for res in interest_results:
        icons = ("💡" if 'recommended' in res['types'] else "") + ("🔍" if 'general' in res['types'] else "")
        st.write(f"{icons} **{res['name']}** ({res['symbol']}) | 점수: {res['score']} | 현재가: **{res['today']['Close']:,.0f}원**")
        st.progress(res['score'] / 12)

with tab2:
    st.subheader("🎯 한국 시장 우량주 자동 스크리너")
    SCREEN_LIST = list(set(['005930.KS', '000660.KS', '035720.KS', '035420.KS', '005380.KS', '000270.KS', '068270.KS', '051910.KS', '006400.KS', '005490.KS', '105560.KS', '055550.KS', '032830.KS', '033780.KS', '003550.KS', '000810.KS', '012330.KS', '015760.KS', '096770.KS', '086790.KS']))
    if st.button("🚀 전체 시장 스크리닝 시작"):
        st.session_state.screened_results = process_tickers(SCREEN_LIST)
    
    if 'screened_results' in st.session_state:
        filtered = sorted([r for r in st.session_state.screened_results if r['score'] >= min_score], key=lambda x: x['score'], reverse=True)
        if filtered:
            st.write(f"✅ 총 **{len(filtered)}**개 유망 종목 발견!")
            for idx, res in enumerate(filtered):
                with st.expander(f"[{res['score']}점] {res['name']} ({res['symbol']})"):
                    st.metric("현재가", f"{res['today']['Close']:,.0f}원")
                    st.write(", ".join([k for k, v in res['score_details'].items() if v == 1][:4]))
                    if st.button("➕ 일반 분석에 추가", key=f"add_sc_{res['symbol']}_{idx}"):
                        if res['symbol'] not in portfolio:
                            portfolio[res['symbol']] = {"price": 0, "qty": 0, "target": 0, "name": res['name'], "note": "", "types": ["general"], "in_account": False}
                            save_portfolio(current_user, portfolio)
                            st.rerun()
                        elif "general" not in portfolio[res['symbol']].get('types', []):
                            portfolio[res['symbol']].setdefault('types', []).append("general")
                            save_portfolio(current_user, portfolio)
                            st.rerun()
                        else: st.info("이미 리스트에 존재합니다.")
        else: st.warning("조건을 만족하는 종목이 없습니다.")

with tab3:
    st.subheader("🔍 심층 분석 (일반 관심 종목)")
    if not general_results: st.info("일반 관심 종목이 없습니다.")
        
    for idx, res in enumerate(general_results):
        df_res, today_res = res['df'], res['today']
        prev_close = df_res.iloc[-2]['Close'] if len(df_res) >= 2 else today_res['Open']
        chg = today_res['Close'] - prev_close
        pct = (chg / prev_close) * 100 if prev_close > 0 else 0
        trend_str = f":red[▲ {abs(chg):,.0f}원 (+{pct:.2f}%)]" if chg > 0 else (f":blue[▼ {abs(chg):,.0f}원 ({pct:.2f}%)]" if chg < 0 else "보합")
            
        with st.expander(f"🔍 {res['name']} ({res['symbol']}) | {today_res['Close']:,.0f}원 {trend_str}", expanded=False):
            tf_option = st.radio("⏱️ 차트 주기", ["30분", "1시간", "일봉", "주봉", "월봉", "분기봉", "년봉"], horizontal=True, index=2, key=f"tf_{res['symbol']}_{idx}")
            chart_df = get_chart_data(res['symbol'], tf_option)
            if chart_df is not None:
                st.plotly_chart(draw_advanced_chart(chart_df.tail(120), f"{res['name']} ({tf_option})"), use_container_width=True, key=f"c_{res['symbol']}_{idx}")
                for pattern in detect_patterns(chart_df): st.info(pattern)
            else: st.warning("데이터 로드 실패.")

            st.write("**[ 펀더멘털 ]**")
            st.write(f"ROE: {res['fund']['roe']*100:.1f}% | 영업이익률: {res['fund']['op_margin']*100:.1f}% | 매출성장: {res['fund']['sales_growth']*100:.1f}%")
            
            if not portfolio.get(res['symbol'], {}).get('in_account', False):
                if st.button(f"💰 내 계좌 관리에 추가", key=f"acc_{res['symbol']}_{idx}"):
                    portfolio[res['symbol']]['in_account'] = True
                    portfolio[res['symbol']]['price'] = today_res['Close']
                    portfolio[res['symbol']]['target'] = today_res['Close'] * 1.15
                    save_portfolio(current_user, portfolio)
                    st.rerun()

with tab4:
    st.subheader("💡 이유성 추천!! (VIP 추천 종목)")
    total_inv, total_val = 0, 0
    if not recom_results: st.info("추천 종목이 없습니다.")

    for idx, res in enumerate(recom_results):
        sym = res['symbol']
        p_data = portfolio.get(sym, {})
        curr_price = res['today']['Close']
        prev_close = res['df'].iloc[-2]['Close'] if len(res['df']) >= 2 else res['today']['Open']
        chg = curr_price - prev_close
        pct = (chg / prev_close) * 100 if prev_close > 0 else 0
        trend_str = f":red[▲ {abs(chg):,.0f}원 (+{pct:.2f}%)]" if chg > 0 else (f":blue[▼ {abs(chg):,.0f}원 ({pct:.2f}%)]" if chg < 0 else "보합")

        with st.expander(f"🌟 {res['name']} ({sym}) | {curr_price:,.0f}원 {trend_str}", expanded=True):
            new_note = st.text_area("✍️ 비고", value=p_data.get('note', ''), key=f"yn_{sym}_{idx}")
            
            tf_option = st.radio("⏱️ 차트 주기", ["30분", "1시간", "일봉", "주봉", "월봉", "분기봉", "년봉"], horizontal=True, index=2, key=f"rtf_{sym}_{idx}")
            chart_df = get_chart_data(sym, tf_option)
            if chart_df is not None:
                st.plotly_chart(draw_advanced_chart(chart_df.tail(120), f"{res['name']} ({tf_option})"), use_container_width=True, key=f"rc_{sym}_{idx}")
                for pattern in detect_patterns(chart_df): st.info(pattern)

            new_price = st.number_input("매수 단가", value=float(p_data.get('price', curr_price)), step=100.0, key=f"rp_{sym}_{idx}")
            new_qty = st.number_input("수량", value=int(p_data.get('qty', 0)), step=1, key=f"rq_{sym}_{idx}")
            new_target = st.number_input("목표가", value=float(p_data.get('target', new_price * 1.15) if p_data.get('target', 0) > 0 else new_price * 1.15), step=100.0, key=f"rt_{sym}_{idx}")
            
            if st.button("💾 추천 저장", key=f"rs_{sym}_{idx}"):
                portfolio[sym].update({'price': new_price, 'qty': new_qty, 'target': new_target, 'note': new_note, 'name': res['name']})
                save_portfolio(current_user, portfolio)
                st.rerun()

            if new_qty > 0:
                inv = new_price * new_qty
                val = curr_price * new_qty
                total_inv += inv; total_val += val
                st.write(f"▶ 손익: {val - inv:,.0f}원 ({((val-inv)/inv)*100:.2f}%) / 투자금: {inv:,.0f}원")

    st.markdown("---")
    st.subheader("📊 추천 성과")
    if total_inv > 0:
        st.write(f"총 매수: {total_inv:,.0f}원 | 평가: {total_val:,.0f}원 | 수익: {total_val-total_inv:,.0f}원 ({((total_val-total_inv)/total_inv)*100:.2f}%)")

with tab5:
    st.subheader("🧮 내 계좌 관리 (일반 종목)")
    total_inv, total_val = 0, 0
    acc_results = [r for r in general_results if portfolio.get(r['symbol'], {}).get('in_account', False)]

    if not acc_results: st.info("계좌 관리 중인 종목이 없습니다.")

    for idx, res in enumerate(acc_results):
        sym = res['symbol']
        p_data = portfolio.get(sym, {})
        curr_price = res['today']['Close']
        chg = curr_price - (res['df'].iloc[-2]['Close'] if len(res['df']) >= 2 else res['today']['Open'])
        trend_str = f":red[▲ {abs(chg):,.0f}원]" if chg > 0 else (f":blue[▼ {abs(chg):,.0f}원]" if chg < 0 else "보합")

        with st.expander(f"💼 {res['name']} ({sym}) | {curr_price:,.0f}원 {trend_str}", expanded=True):
            new_price = st.number_input("매수 단가", value=float(p_data.get('price', curr_price)), step=100.0, key=f"ap_{sym}_{idx}")
            new_qty = st.number_input("수량", value=int(p_data.get('qty', 0)), step=1, key=f"aq_{sym}_{idx}")
            new_target = st.number_input("목표가", value=float(p_data.get('target', new_price * 1.15) if p_data.get('target', 0) > 0 else new_price * 1.15), step=100.0, key=f"at_{sym}_{idx}")

            c1, c2 = st.columns(2)
            if c1.button("💾 저장", key=f"as_{sym}_{idx}"):
                portfolio[sym].update({'price': new_price, 'qty': new_qty, 'target': new_target})
                save_portfolio(current_user, portfolio)
                st.rerun()
            if c2.button("🗑️ 계좌에서 제외", key=f"ar_{sym}_{idx}"):
                portfolio[sym]['in_account'] = False
                save_portfolio(current_user, portfolio)
                st.rerun()

            if new_qty > 0:
                inv = new_price * new_qty
                val = curr_price * new_qty
                total_inv += inv; total_val += val
                st.write(f"▶ 손익: {val - inv:,.0f}원 ({((val-inv)/inv)*100:.2f}%) / 투자금: {inv:,.0f}원")

    st.markdown("---")
    if total_inv > 0:
        st.write(f"**총 성과:** 매수 {total_inv:,.0f}원 | 평가 {total_val:,.0f}원 | 수익 {total_val-total_inv:,.0f}원 ({((total_val-total_inv)/total_inv)*100:.2f}%)")

with tab6:
    st.header("📖 투자 마스터 클래스 (거장들의 실전 전략)")
    st.markdown("""
    ---
    ### 🏆 1. 윌리엄 오닐의 CANSLIM (최고의 주식 발굴법)
    * **C (Current Earnings):** 최근 분기 EPS 20% 이상 증가
    * **A (Annual Earnings):** 연간 순이익 꾸준한 성장
    * **N (New):** 신고가 돌파
    * **S (Supply and Demand):** 거래량 150% 이상 폭발
    * **L (Leader):** 시장 주도주
    * **I (Institutional Sponsorship):** 기관 매집 (OBV 우상향)
    * **M (Market Direction):** 상승 추세
    
    ### 🛡️ 2. 리스크 관리 (절대 원칙)
    * **기계적인 손절:** 매수가 대비 **-7%** 이탈 시 무조건 매도.
    * **손익비 (Risk/Reward):** 목표 수익이 감수할 손실보다 항상 2배 이상 커야 합니다.
    """)
