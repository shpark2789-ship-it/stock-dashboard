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
/* 화면 너비가 768px 이하(스마트폰)일 때 적용되는 스타일 */
@media (max-width: 768px) {
    /* 전체 기본 폰트 사이즈 축소 */
    html, body, [class*="st-"] {
        font-size: 14px !important;
    }
    /* 제목 폰트 축소 */
    h1 { font-size: 1.5rem !important; }
    h2 { font-size: 1.25rem !important; }
    h3 { font-size: 1.1rem !important; }
    /* 큰 숫자(Metric) 사이즈 축소 */
    [data-testid="stMetricValue"] {
        font-size: 1.4rem !important;
    }
    /* 화면 양옆 여백 축소로 공간 확보 */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
    }
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
    st.stop() # 로그인 전에는 아래 코드가 실행되지 않도록 차단

# 로그인 성공 시 현재 사용자 이름 변수 할당
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
    """사용자 이름별로 독립된 데이터베이스 문서를 가리킵니다."""
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
                # 💡 구버전 데이터를 신규 다중 태그(types) 시스템으로 자동 마이그레이션
                migrated_data = {}
                for k, v in data.items():
                    base_sym = k.split('_')[0] if '_' in k else k
                    if base_sym not in migrated_data:
                        migrated_data[base_sym] = v.copy()
                        migrated_data[base_sym]['types'] = []
                        if 'type' in v:
                            migrated_data[base_sym]['types'].append(v['type'])
                    else:
                        if 'type' in v and v['type'] not in migrated_data[base_sym]['types']:
                            migrated_data[base_sym]['types'].append(v['type'])
                    
                    if 'types' in v:
                        for t in v['types']:
                            if t not in migrated_data[base_sym]['types']:
                                migrated_data[base_sym]['types'].append(t)
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

# 현재 접속한 사용자의 데이터만 불러오기
portfolio = load_portfolio(current_user)
user_tickers = list(portfolio.keys())

# --- 🇰🇷 한글 종목명 변환기 (네이버/KRX 우회) ---
FALLBACK_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오",
    "035420": "NAVER", "005380": "현대차", "000270": "기아",
    "068270": "셀트리온", "051910": "LG화학", "006400": "삼성SDI",
    "005490": "POSCO홀딩스", "105560": "KB금융", "055550": "신한지주",
    "032830": "삼성생명", "033780": "KT&G", "003550": "LG",
    "000810": "삼성화재", "012330": "현대모비스", "015760": "한국전력",
    "096770": "SK이노베이션", "086790": "하나금융지주", "000100": "유한양행",
    "008930": "한미사이언스", "011070": "LG이노텍", "009150": "삼성전기",
    "001250": "GS글로벌", "010140": "삼성중공업", "034020": "두산에너빌리티"
}

@st.cache_data(ttl=86400, show_spinner=False)
def get_krx_names():
    result = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    # 1차 시도: 네이버 증권 API (가장 안정적)
    try:
        for market in ['KOSPI', 'KOSDAQ']:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=2000'
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                for stock in data.get('stocks', []):
                    result[stock['itemCode']] = stock['stockName']
        if len(result) > 1000:
            return result
    except:
        pass

    # 2차 시도: KRX 공식 API 우회
    try:
        url = 'http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd'
        payload = {
            'bld': 'dbms/MDC/STAT/standard/MDCSTAT01901',
            'locale': 'ko_KR',
            'mktId': 'ALL',
            'share': '1',
            'csvxls_isNo': 'false',
        }
        krx_headers = headers.copy()
        krx_headers.update({
            'Referer': 'http://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd',
            'Origin': 'http://data.krx.co.kr',
        })
        res = requests.post(url, data=payload, headers=krx_headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            for item in data.get('OutBlock_1', []):
                result[item['ISU_SRT_CD']] = item['ISU_ABBRV']
        if len(result) > 1000:
            return result
    except:
        pass

    # 실패 시 캐시 강제 삭제
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

def get_enhanced_data(ticker, market_df):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        if df.empty or len(df) < 200: return None, None

        # 이평선 추가 (차트 패턴 분석을 위해 20일선 추가)
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
            df['RS_Line'] = (combined['stock'] / combined['market']) * 100
            stock_perf = (df['Close'] / df['Close'].shift(50)) - 1
            market_perf = (market_df['Close'] / market_df['Close'].shift(50)) - 1
            df['RS_Rating'] = stock_perf - market_perf

        df['Vol_Avg'] = df['Volume'].rolling(window=20).mean()
        info = stock.info
        krx_names = get_krx_names()
        code = ticker.split('.')[0]
        kor_name = krx_names.get(code, FALLBACK_NAMES.get(code, info.get('shortName', info.get('longName', ticker))))

        fundamentals = {
            'name': kor_name,
            'sector': info.get('sector', 'Unknown'),
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
    """AI 차트 패턴 자동 분석기"""
    patterns = []
    if len(df) < 5: return patterns
    
    today = df.iloc[-1]
    yest = df.iloc[-2]
    prev = df.iloc[-3]
    
    # 1. 캔들 장악형 (Engulfing)
    if yest['Close'] < yest['Open'] and today['Close'] > today['Open'] and today['Open'] <= yest['Close'] and today['Close'] >= yest['Open']:
        patterns.append("🟢 **[상승 장악형]** 전일의 하락(음봉)을 완전히 덮는 강력한 상승 캔들이 발생했습니다. (바닥권일 경우 강력한 매수 시그널)")
    elif yest['Close'] > yest['Open'] and today['Close'] < today['Open'] and today['Open'] >= yest['Close'] and today['Close'] <= yest['Open']:
        patterns.append("🔴 **[하락 장악형]** 전일의 상승(양봉)을 완전히 덮는 하락 캔들이 발생했습니다. (고점일 경우 강한 매도 시그널)")
        
    # 2. 적삼병 / 흑삼병
    if today['Close'] > today['Open'] and yest['Close'] > yest['Open'] and prev['Close'] > prev['Open'] and today['Close'] > yest['Close'] and yest['Close'] > prev['Close']:
        patterns.append("🔥 **[적삼병]** 3일 연속 상승 양봉이 출현했습니다. 지속적인 대세 상승 추세로 전환될 확률이 높습니다.")
    elif today['Close'] < today['Open'] and yest['Close'] < yest['Open'] and prev['Close'] < prev['Open'] and today['Close'] < yest['Close'] and yest['Close'] < prev['Close']:
        patterns.append("❄️ **[흑삼병]** 3일 연속 하락 음봉이 출현했습니다. 추가적인 하락 추세에 주의해야 합니다.")
        
    # 3. 골든크로스 / 데드크로스 (단기 20일선 vs 중기 50일선)
    if 'MA20' in df.columns and 'MA50' in df.columns:
        if yest['MA20'] <= yest['MA50'] and today['MA20'] > today['MA50']:
            patterns.append("🌟 **[골든 크로스]** 단기(20일) 생명선이 중기(50일) 추세선을 상향 돌파했습니다! 본격적인 상승장이 기대됩니다.")
        elif yest['MA20'] >= yest['MA50'] and today['MA20'] < today['MA50']:
            patterns.append("🚨 **[데드 크로스]** 단기(20일) 생명선이 중기(50일) 추세선을 하향 이탈했습니다. 리스크 관리가 필요합니다.")
            
    # 4. 꼬리 캔들 분석 (망치형 / 유성형)
    body = abs(today['Close'] - today['Open'])
    lower_tail = today['Open'] - today['Low'] if today['Close'] > today['Open'] else today['Close'] - today['Low']
    upper_tail = today['High'] - today['Close'] if today['Close'] > today['Open'] else today['High'] - today['Open']
    
    if body > 0:
        if lower_tail > body * 2 and upper_tail < body * 0.5:
            patterns.append("🔨 **[망치형 캔들]** 장중 하락을 이겨내고 끌어올린 아래꼬리가 긴 형태입니다. 저가 매수세가 강해 지지선을 형성할 가능성이 높습니다.")
        elif upper_tail > body * 2 and lower_tail < body * 0.5:
            patterns.append("☄️ **[유성형(역망치) 캔들]** 윗꼬리가 긴 형태입니다. 상승을 억누르는 대기 매도 물량이 많아 단기 고점일 수 있으니 주의하세요.")
            
    if not patterns:
        patterns.append("⚪ 현재 특이한 캔들 돌파나 이평선 크로스 패턴은 발견되지 않았습니다. 기존의 추세가 이어지고 있습니다.")
        
    return patterns

def calculate_score(df, fund):
    today = df.iloc[-1]
    dist_high = ((fund['high_52w'] - today['Close']) / fund['high_52w']) * 100
    dist_low = ((today['Close'] - fund['low_52w']) / fund['low_52w']) * 100
    vol_ratio = (today['Volume'] / today['Vol_Avg']) * 100 if today['Vol_Avg'] > 0 else 0

    roe_val = fund['roe'] * 100 if fund['roe'] else 0
    sales_growth_val = fund['sales_growth'] * 100 if fund['sales_growth'] else 0
    eps_growth_val = fund['eps_growth'] * 100 if fund['eps_growth'] else 0
    op_margin_val = fund['op_margin'] * 100 if fund['op_margin'] else 0
    debt_ratio_val = fund['debt_ratio'] if fund['debt_ratio'] else 0
    adx_val = today.get('ADX_14', 0)

    score_details = {
        f"[N] 신고가 5% 이내 (-{dist_high:.1f}%)": 1 if dist_high < 5 else 0,
        f"[S] 거래량 150% 폭발 ({vol_ratio:.0f}%)": 1 if vol_ratio > 150 else 0,
        f"[Trend] 이평선 정배열 (50>150>200)": 1 if (today['MA50'] > today['MA150'] > today['MA200']) else 0,
        f"[L] RS 강도 우상향 (시장대비 우위)": 1 if (today.get('RS_Rating', 0) > 0) else 0,
        f"[I] OBV 누적거래량 우상향 (매집)": 1 if (len(df) > 5 and today['OBV'] > df['OBV'].iloc[-5]) else 0,
        f"[Trend] ADX 추세강화 25↑ ({adx_val:.1f})": 1 if adx_val > 25 else 0,
        f"[Trend] 신저가 대비 30%↑ 회복 (+{dist_low:.0f}%)": 1 if dist_low > 30 else 0,
        f"[Quality] ROE 15%↑ ({roe_val:.1f}%)": 1 if (fund['roe'] and fund['roe'] >= 0.15) else 0,
        f"[A] 매출 성장 20%↑ ({sales_growth_val:.1f}%)": 1 if (fund['sales_growth'] and fund['sales_growth'] >= 0.20) else 0,
        f"[C] 이익 성장 20%↑ ({eps_growth_val:.1f}%)": 1 if (fund['eps_growth'] and fund['eps_growth'] >= 0.20) else 0,
        f"[Quality] 영업이익률 10%↑ ({op_margin_val:.1f}%)": 1 if (fund['op_margin'] and fund['op_margin'] >= 0.10) else 0,
        f"[Quality] 부채비율 150%↓ ({debt_ratio_val:.1f}%)": 1 if (fund['debt_ratio'] and fund['debt_ratio'] <= 150) else 0
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
            patterns = detect_patterns(df) # 💡 자동 차트 패턴 분석
            
            results.append({
                'symbol': symbol,
                'name': fund['name'], 
                'score': score,
                'score_details': details, 
                'patterns': patterns, 
                'df': df, 
                'fund': fund,
                'today': df.iloc[-1], 
                'dist_high': dist_high, 
                'vol_ratio': vol_ratio,
                'types': portfolio[symbol].get('types', []) # 다중 카테고리 정보
            })
    if need_save:
        save_portfolio(current_user, portfolio)
    return results

def draw_advanced_chart(df, name):
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#0066ff' for _, row in df.iterrows()]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='주가', increasing_line_color='#ff3333', decreasing_line_color='#0066ff'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA50'], line=dict(color='orange', width=1.5), name='50일선'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA150'], line=dict(color='green', width=1.5), name='150일선'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA200'], line=dict(color='purple', width=1.5), name='200일선'), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name='거래량'), row=2, col=1)
    
    fig.update_layout(
        title=dict(text=f"📈 {name} 분석 차트", font=dict(size=14)),
        yaxis_title=dict(text="주가 (원)", font=dict(size=11)),
        yaxis2_title=dict(text="거래량", font=dict(size=11)),
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=40, b=10),
        height=380,
        showlegend=True,
        legend=dict(
            orientation="h", 
            yanchor="bottom", y=1.02, 
            xanchor="right", x=1,
            font=dict(size=10)
        ),
        font=dict(size=11)
    )
    return fig

# --- UI 레이아웃 시작 ---
st.title("🛡️ 박스 모멘텀 프로: 실전 투자 시스템")

market_df = get_market_data()

# 종목 검색 리스트 생성
krx_map = get_krx_names()
combined_stocks = {**FALLBACK_NAMES, **krx_map}
search_list = [f"{name} ({code})" for code, name in combined_stocks.items()]
search_list.sort() # 가나다순 정렬

with st.sidebar:
    st.success(f"👤 **{current_user}**님 접속 중")
    if st.button("🚪 로그아웃", help="다른 닉네임으로 접속하려면 누르세요."):
        del st.session_state['username']
        st.rerun()
        
    st.markdown("---")
    st.header("⚙️ 내 관심/보유 종목 관리")

    if len(krx_map) < 100:
        st.warning("⚠️ 거래소 서버 통신 지연으로 일부 종목만 검색됩니다. 검색에 안 나오는 종목은 아래 '직접 입력'을 이용해주세요.")

    if st.button("🔄 최신 주가 데이터 새로고침", help="이 버튼을 누르면 검색 목록이 최신화됩니다!"):
        st.cache_data.clear()
        st.rerun()

    with st.form("add_stock_form", clear_on_submit=True):
        st.write("🔍 **새 종목 추가**")
        
        # 종목 분류 선택 버튼
        stock_category = st.radio(
            "종목 분류 선택", 
            ["🔍 일반 분석 (내가 찾은 종목)", "💡 이유성 추천 (VIP 종목)"],
            horizontal=True
        )
        
        selected_stock = st.selectbox(
            "회사명으로 검색", 
            options=search_list, 
            index=None, 
            placeholder="예: 삼성전자 (초성 및 일부 검색 가능)"
        )
        
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
                            if not yf.Ticker(target_ticker + ".KS").history(period="1d").empty:
                                target_ticker += ".KS"
                            else:
                                target_ticker += ".KQ"
                        except:
                            target_ticker += ".KS"

                    cat_val = 'recommended' if '추천' in stock_category else 'general'

                    # 💡 데이터 구조 통폐합 로직 적용 완료
                    if target_ticker in portfolio:
                        # 이미 있는 종목인데 다른 카테고리로 추가하려 할 때
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
                            if code_only in combined_stocks:
                                stock_name = combined_stocks[code_only]
                            else:
                                try:
                                    temp_info = yf.Ticker(target_ticker).info
                                    stock_name = temp_info.get('shortName', temp_info.get('longName', target_ticker))
                                except:
                                    stock_name = target_ticker

                        portfolio[target_ticker] = {"price": 0.0, "qty": 0, "target": 0.0, "name": stock_name, "note": "", "types": [cat_val]}
                        save_portfolio(current_user, portfolio)
                        st.success(f"{stock_name} 추가 완료!")
                        st.rerun()
            else:
                st.warning("종목을 검색하거나 코드를 입력해주세요.")

    st.markdown("---")
    st.write("### 📂 현재 등록된 리스트")
    st.caption("삭제 버튼(❌)을 누르면 목록에서 지워집니다.")

    for sym in list(portfolio.keys()):
        col1, col2 = st.columns([4, 1])
        name = portfolio[sym].get('name', '')
        types = portfolio[sym].get('types', [])
        
        # 목록에서도 분류를 한눈에 볼 수 있도록 다중 이모티콘 병합 표시
        icons = ""
        if 'recommended' in types: icons += "💡"
        if 'general' in types: icons += "🔍"
        
        display_text = f"• {icons} **{name}** ({sym})"
        
        col1.write(display_text)
        if col2.button("❌", key=f"del_sidebar_{sym}"):
            del portfolio[sym]
            save_portfolio(current_user, portfolio)
            st.rerun()

    st.markdown("---")
    min_score = st.slider("스크리닝 최소 점수 필터 (⭐)", 0, 12, 6)

interest_results = process_tickers(user_tickers)

# 분류별로 데이터 나누기
general_results = [r for r in interest_results if 'general' in r['types']]
recom_results = [r for r in interest_results if 'recommended' in r['types']]

# 탭 구조 업데이트
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 대시보드", "🎯 종목 스크리너", "🔍 심층 분석", "💡 이유성 추천!!", "🧮 내 계좌 관리", "📖 투자 마스터 클래스"])

with tab1:
    if market_df is not None:
        m_today = market_df.iloc[-1]['Close']
        m_prev = market_df.iloc[-2]['Close']
        m_trend = "상승장 (M조건 충족)" if market_df.iloc[-1]['Close'] > market_df['Close'].rolling(20).mean().iloc[-1] else "하락/조정장 (보수적 접근)"
        
        st.metric("KOSPI 지수", f"{m_today:,.2f}", f"{m_today - m_prev:,.2f}")
        
        KST = timezone(timedelta(hours=9))
        now_kst = datetime.now(KST)
        is_market_open = now_kst.weekday() < 5 and (9 <= now_kst.hour < 15 or (now_kst.hour == 15 and now_kst.minute <= 30))
        market_status = "🟢 장중 (실시간 데이터 반영)" if is_market_open else "🔴 장 마감 (최종 종가 기준)"
        
        st.caption(f"**현재 시장 방향성:** {m_trend} &nbsp;|&nbsp; **시장 상태:** {market_status}")

    st.subheader("🏆 관심 종목 하이라이트")
    best_picks = [r for r in interest_results if r['score'] >= 8]
    if best_picks:
        for idx, pick in enumerate(best_picks):
            with st.container():
                icons = ""
                if 'recommended' in pick['types']: icons += "💡"
                if 'general' in pick['types']: icons += "🔍"
                st.success(f"{icons} **{pick['name']}**")
                st.metric("종합 점수", f"{pick['score']} / 12", f"{'⭐' * pick['score']}")
    else:
        st.info("현재 관심 종목 중 8점 이상의 강력한 주도주 신호가 없거나, 등록된 종목이 없습니다. 좌측에서 종목을 추가해주세요.")

    st.markdown("---")
    st.subheader("📋 관심 종목 실시간 현황")
    if not interest_results:
        st.warning("왼쪽 ⚙️ 설정 창에서 관심 있는 종목(예: 삼성전자)을 먼저 추가해 주세요!")
    else:
        for i, res in enumerate(interest_results):
            with st.container():
                icons = ""
                if 'recommended' in res['types']: icons += "💡"
                if 'general' in res['types']: icons += "🔍"
                st.markdown(f"{icons} **{res['name']}** ({res['symbol']})")
                st.write(f"점수: {'⭐' * res['score']}")
                st.progress(res['score'] / 12)
                st.write(f"현재가: **{res['today']['Close']:,.0f}원**")
                st.write("---")

with tab2:
    st.subheader("🎯 한국 시장 우량주 자동 스크리너")
    st.write("주요 대형주를 스캔하여 최적의 매수 후보를 발굴합니다. 버튼을 눌러 바로 내 리스트에 추가하세요.")

    raw_screen_list = [
        '005930.KS', '000660.KS', '035720.KS', '035420.KS', '005380.KS',
        '000270.KS', '068270.KS', '051910.KS', '006400.KS', '005490.KS',
        '105560.KS', '055550.KS', '032830.KS', '033780.KS', '003550.KS',
        '000810.KS', '012330.KS', '015760.KS', '096770.KS', '086790.KS'
    ]
    SCREEN_LIST = list(set(raw_screen_list))

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
                    positive_points = [k for k, v in res['score_details'].items() if v == 1]
                    st.write(", ".join(positive_points[:4]) + " 등")

                    if st.button("➕ 내 리스트에 추가 (일반 종목으로)", key=f"add_screen_{res['symbol']}"):
                        if res['symbol'] not in portfolio:
                            portfolio[res['symbol']] = {"price": 0.0, "qty": 0, "target": 0.0, "name": res['name'], "note": "", "types": ["general"]}
                            save_portfolio(current_user, portfolio)
                            st.toast(f"✅ {res['name']}이(가) 관심 종목에 추가되었습니다!")
                            st.rerun()
                        else:
                            if "general" not in portfolio[res['symbol']].get('types', []):
                                portfolio[res['symbol']].setdefault('types', []).append("general")
                                save_portfolio(current_user, portfolio)
                                st.toast(f"✅ 일반 분석에 추가되었습니다!")
                                st.rerun()
                            else:
                                st.info("이미 내 리스트에 등록된 종목입니다.")
        else:
            st.warning("현재 필터링 조건을 만족하는 종목이 시장에 없습니다.")

with tab3:
    st.subheader("🔍 심층 분석 (일반 관심 종목)")
    st.write("내가 직접 발굴하고 추가한 일반 관심 기업들의 심층 분석입니다.")

    if not general_results:
        st.info("현재 일반 관심 종목이 없습니다. 왼쪽 메뉴에서 '일반 분석'을 선택 후 종목을 추가해보세요.")
        
    for res in general_results:
        with st.expander(f"🔍 {res['name']} ({res['symbol']}) 분석 리포트", expanded=False):
            chart_fig = draw_advanced_chart(res['df'].tail(120), res['name'])
            # 💡 에러 방지: 차트를 그릴 때마다 고유한 key를 부여 (tab3)
            st.plotly_chart(chart_fig, use_container_width=True, key=f"chart_tab3_{res['symbol']}")

            # 💡 새로 추가된 AI 캔들 & 차트 패턴 분석
            st.markdown("---")
            st.write("**[ 📊 AI 캔들 & 차트 패턴 분석 ]**")
            for pattern in res['patterns']:
                st.info(pattern)

            st.markdown("---")
            st.write("**[ 기본적 분석 (Fundamental) ]**")
            st.metric("ROE (자기자본이익률)", f"{res['fund']['roe'] * 100:.1f}%" if res['fund']['roe'] else "N/A")
            st.metric("영업이익률", f"{res['fund']['op_margin'] * 100:.1f}%" if res['fund']['op_margin'] else "N/A")
            st.metric("매출성장률", f"{res['fund']['sales_growth'] * 100:.1f}%" if res['fund']['sales_growth'] else "N/A")
            
            st.write("**[ 체크리스트 (CANSLIM/VCP 분석) ]**")
            for label, val in res['score_details'].items():
                if "[C]" in label or "[A]" in label or "[N]" in label or "[S]" in label or "[L]" in label or "[I]" in label:
                    st.markdown(f"{'✅' if val else '❌'} **{label}**")
                else:
                    st.write(f"{'✅' if val else '❌'} {label}")

with tab4:
    st.subheader("💡 이유성 추천!! (VIP 추천 종목)")
    st.write("이유성 전문가가 픽한 특별 추천 종목들의 사유와 목표가를 관리하는 공간입니다.")

    total_invested_yoo = 0
    total_current_val_yoo = 0

    if not recom_results:
        st.info("왼쪽 메뉴에서 '💡 이유성 추천 종목'을 선택 후 새 종목을 추가하시면 추천 관리 기능이 활성화됩니다.")

    for res in recom_results:
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0.0, "qty": 0, "target": 0.0, "note": ""})
        curr_price = res['today']['Close']

        with st.expander(f"🌟 {res['name']} ({res['symbol']}) - 추천 관리 (현재가: {curr_price:,.0f}원)", expanded=True):
            new_note = st.text_area(
                "✍️ 비고 (이유성 전문가 추천 사유 및 코멘트)", 
                value=p_data.get('note', ''), 
                placeholder="이 종목을 추천하는 특별한 이유나 매매 전략을 적어주세요!", 
                key=f"y_n_{sym}"
            )
            
            chart_fig = draw_advanced_chart(res['df'].tail(120), res['name'])
            # 💡 에러 방지: 차트를 그릴 때마다 고유한 key를 부여 (tab4)
            st.plotly_chart(chart_fig, use_container_width=True, key=f"chart_tab4_{sym}")

            st.markdown("---")
            st.write("**[ 📊 AI 캔들 & 차트 패턴 분석 ]**")
            for pattern in res['patterns']:
                st.info(pattern)
            st.markdown("---")

            st.write("**[ 추천 매매 단가 설정 ]**")
            new_price = st.number_input("추천 매수 단가 (원)", value=float(p_data.get('price', 0)), step=100.0, key=f"y_p_{sym}")
            new_qty = st.number_input("매수 수량 (주)", value=int(p_data.get('qty', 0)), step=1, key=f"y_q_{sym}")
            new_target = st.number_input("목표 단가 (원)", value=float(p_data.get('target', new_price * 1.2)), step=100.0, key=f"y_t_{sym}")

            stop_loss_price = new_price * 0.93

            if new_price > 0 and new_target > new_price:
                risk = new_price - stop_loss_price
                reward = new_target - new_price
                rr_ratio = reward / risk if risk > 0 else 0
                if rr_ratio >= 2.0:
                    rr_status = f"✅ 훌륭함 (1:{rr_ratio:.1f})"
                else:
                    rr_status = f"⚠️ 위험함 (1:{rr_ratio:.1f})"
                st.metric("손익비 (Risk/Reward)", rr_status, help="최소 1:2 이상 권장")
            else:
                st.info("추천 매수가와 목표가를 입력하세요.")

            if st.button("💾 추천 정보 저장", key=f"y_save_{sym}"):
                portfolio[sym]['price'] = new_price
                portfolio[sym]['qty'] = new_qty
                portfolio[sym]['target'] = new_target
                portfolio[sym]['note'] = new_note
                portfolio[sym]['name'] = res['name']
                save_portfolio(current_user, portfolio)
                st.success(f"{res['name']} 추천 정보가 성공적으로 저장되었습니다!")
                st.rerun()

            if new_qty > 0:
                invested = new_price * new_qty
                curr_val = curr_price * new_qty
                profit = curr_val - invested
                roi = (profit / invested) * 100 if invested > 0 else 0
                total_invested_yoo += invested
                total_current_val_yoo += curr_val

                st.write(f"▶ **현재 평가 손익:** {profit:,.0f}원 ({roi:.2f}%) / 투자금액: {invested:,.0f}원")

                st.markdown("##### 🛎️ 매매 액션 가이드 (시스템 판정)")
                if new_target > new_price and curr_price >= new_target:
                    st.success(f"🎯 **[목표가 도달]** 축하합니다! 설정하신 목표가({new_target:,.0f}원)를 돌파했습니다. **분할 매도 또는 전량 익절**을 고려하세요.")
                elif new_price > 0 and curr_price <= stop_loss_price:
                    st.error(f"🚨 **[손절가 이탈]** 현재가({curr_price:,.0f}원)가 손절선({stop_loss_price:,.0f}원) 아래로 내려갔습니다. 원칙에 따라 **기계적 손절**을 강력히 권장합니다.")
                elif new_price > 0:
                    if roi > 0:
                        st.info(f"🟢 **[보유 유지 - 수익 중]** 목표가({new_target:,.0f}원)까지 {new_target - curr_price:,.0f}원 남았습니다.")
                    else:
                        st.warning(f"🟡 **[보유 유지 - 손실 중]** 손절선({stop_loss_price:,.0f}원)까지 {curr_price - stop_loss_price:,.0f}원 여유가 있습니다.")

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
    st.write("일반 관심 종목들의 매수/매도 타이밍을 관리하는 곳입니다.")

    total_invested = 0
    total_current_val = 0

    if not general_results:
        st.info("왼쪽에서 일반 종목을 추가하시면 계좌 관리 기능이 활성화됩니다.")

    for res in general_results:
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0.0, "qty": 0, "target": 0.0})
        curr_price = res['today']['Close']

        with st.expander(f"💼 {res['name']} ({res['symbol']}) - 현재가: {curr_price:,.0f}원", expanded=True):
            new_price = st.number_input("매수 단가 (원)", value=float(p_data['price']), step=100.0, key=f"gen_p_{sym}")
            new_qty = st.number_input("보유 수량 (주)", value=int(p_data.get('qty', 0)), step=1, key=f"gen_q_{sym}")
            new_target = st.number_input("목표 단가 (원)", value=float(p_data.get('target', new_price * 1.2)), step=100.0, key=f"gen_t_{sym}")

            stop_loss_price = new_price * 0.93

            if new_price > 0 and new_target > new_price:
                risk = new_price - stop_loss_price
                reward = new_target - new_price
                rr_ratio = reward / risk if risk > 0 else 0
                if rr_ratio >= 2.0:
                    rr_status = f"✅ 훌륭함 (1:{rr_ratio:.1f})"
                else:
                    rr_status = f"⚠️ 위험함 (1:{rr_ratio:.1f})"
                st.metric("손익비 (Risk/Reward)", rr_status, help="최소 1:2 이상 권장")
            else:
                st.info("매수/목표가를 입력하세요.")

            if st.button("💾 이 종목 정보 저장", key=f"gen_save_{sym}"):
                portfolio[sym]['price'] = new_price
                portfolio[sym]['qty'] = new_qty
                portfolio[sym]['target'] = new_target
                portfolio[sym]['name'] = res['name']
                save_portfolio(current_user, portfolio)
                st.success("투자 정보가 파일에 저장되었습니다.")
                st.rerun()

            if new_qty > 0:
                invested = new_price * new_qty
                curr_val = curr_price * new_qty
                profit = curr_val - invested
                roi = (profit / invested) * 100 if invested > 0 else 0
                total_invested += invested
                total_current_val += curr_val

                st.write(f"▶ **현재 평가 손익:** {profit:,.0f}원 ({roi:.2f}%) / 투자금액: {invested:,.0f}원")

                st.markdown("##### 🛎️ 매매 액션 가이드 (시스템 판정)")
                if new_target > new_price and curr_price >= new_target:
                    st.success(f"🎯 **[목표가 도달]** 축하합니다! 설정하신 목표가({new_target:,.0f}원)를 돌파했습니다.")
                elif new_price > 0 and curr_price <= stop_loss_price:
                    st.error(f"🚨 **[손절가 이탈]** 현재가({curr_price:,.0f}원)가 손절선({stop_loss_price:,.0f}원) 아래로 내려갔습니다.")
                elif new_price > 0:
                    if roi > 0:
                        st.info(f"🟢 **[보유 유지 - 수익 중]** 목표가({new_target:,.0f}원)까지 {new_target - curr_price:,.0f}원 남았습니다.")
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

with tab6:
    st.header("📖 투자 마스터 클래스 (거장들의 실전 전략)")
    st.markdown("""
    이 대시보드는 월스트리트 전설들의 투자 기법을 하나의 알고리즘으로 통합한 것입니다. 아래의 핵심 원칙을 읽고 투자에 적용해 보세요.
    ---
    ### 🏆 1. 윌리엄 오닐의 CANSLIM (최고의 주식 발굴법)
    * **C (Current Earnings):** 최근 분기 EPS 전년 동기 대비 20% 이상 증가
    * **A (Annual Earnings):** 연간 순이익 꾸준한 성장
    * **N (New):** 신고가(New High) 돌파
    * **S (Supply and Demand):** 거래량 150% 이상 폭발 (기관 개입)
    * **L (Leader):** 시장 주도주 (RS 강도 우위)
    * **I (Institutional Sponsorship):** 기관 매집 (OBV 우상향)
    * **M (Market Direction):** 코스피 지수 상승 추세
    
    ### 🛡️ 2. 리스크 관리 (절대 원칙) - "손익비"
    * **기계적인 손절:** 매수가 대비 **-7%** 이탈 시 무조건 매도.
    * **손익비 (Risk/Reward):** 목표 수익이 감수할 손실보다 항상 2배 이상 커야 합니다.
    """)

st.caption(f"시스템 정상 작동 중 | 마지막 업데이트: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
