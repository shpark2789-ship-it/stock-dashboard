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
    DOC_REF = db.collection('investing').document('portfolio')
except Exception as e:
    st.error(f"데이터베이스 연결 실패: {e}")
    st.stop()

def load_portfolio():
    default_data = {
        "005930.KS": {"price": 0.0, "qty": 0, "target": 0.0, "name": "삼성전자"}
    }
    try:
        doc = DOC_REF.get()
        if doc.exists:
            data = doc.to_dict()
            if data: return data
        DOC_REF.set(default_data)
        return default_data
    except:
        return default_data

def save_portfolio(data):
    try:
        DOC_REF.set(data)
    except:
        pass

portfolio = load_portfolio()
user_tickers = list(portfolio.keys())

# --- 🇰🇷 한글 종목명 변환기 ---
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

@st.cache_data(ttl=86400)
def get_krx_names():
    """한국거래소 및 네이버 증권을 통해 전체 코스피/코스닥 종목명을 가져옵니다."""
    result = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'}
    
    # 1차 시도: 네이버 증권 모바일 API (클라우드 차단 거의 없음, 빠름)
    try:
        for market in ['KOSPI', 'KOSDAQ']:
            url = f'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=2000'
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                for stock in data.get('stocks', []):
                    result[stock['itemCode']] = stock['stockName']
        if result:
            return result
    except:
        pass

    # 2차 시도: KRX 정보데이터시스템 API
    try:
        url = 'http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd'
        payload = {
            'bld': 'dbms/MDC/STAT/standard/MDCSTAT01901',
            'locale': 'ko_KR',
            'mktId': 'ALL',
            'share': '1',
            'csvxls_isNo': 'false',
        }
        res = requests.post(url, data=payload, headers=headers, timeout=5)
        data = res.json()
        krx_result = {item['ISU_SRT_CD']: item['ISU_ABBRV'] for item in data['OutBlock_1']}
        if krx_result:
            return krx_result
    except:
        pass

    # 3차 시도: 기존 상장법인목록 (KIND) HTML 파싱
    try:
        url = 'http://kind.krx.co.kr/corpgeneral/corpList.do?method=download&searchType=13'
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = 'euc-kr'
        df = pd.read_html(io.StringIO(res.text), header=0)[0]
        df['종목코드'] = df['종목코드'].map('{:06d}'.format)
        return dict(zip(df['종목코드'], df['회사명']))
    except:
        return {}

# --- 데이터 수집 함수 ---
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
            results.append({
                'symbol': symbol, 'name': fund['name'], 'score': score,
                'score_details': details, 'df': df, 'fund': fund,
                'today': df.iloc[-1], 'dist_high': dist_high, 'vol_ratio': vol_ratio
            })
    if need_save:
        save_portfolio(portfolio)
    return results

def draw_advanced_chart(df, name):
    colors = ['#ff3333' if row['Close'] >= row['Open'] else '#0066ff' for _, row in df.iterrows()]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.7, 0.3])
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='주가', increasing_line_color='#ff3333', decreasing_line_color='#0066ff'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA50'], line=dict(color='orange', width=1.5), name='50일선'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA150'], line=dict(color='green', width=1.5), name='150일선'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA200'], line=dict(color='purple', width=1.5), name='200일선'), row=1, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name='거래량'), row=2, col=1)
    fig.update_layout(title=f"📈 {name} 최근 120일 기술적 분석 차트", yaxis_title="주가 (원)", yaxis2_title="거래량", xaxis_rangeslider_visible=False, margin=dict(l=0, r=0, t=40, b=0), height=450, showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    return fig

# --- UI 레이아웃 시작 ---
st.title("🛡️ 박스 모멘텀 프로: 실전 투자 시스템")

market_df = get_market_data()

# 종목 검색을 위한 리스트 생성 로직
krx_map = get_krx_names()
combined_stocks = {**FALLBACK_NAMES, **krx_map}
search_list = [f"{name} ({code})" for code, name in combined_stocks.items()]
search_list.sort() # 가나다 순으로 정렬

with st.sidebar:
    st.header("⚙️ 내 관심/보유 종목 관리")

    if st.button("🔄 최신 주가 데이터 새로고침", help="장 중에 최신 데이터로 업데이트하고 싶을 때 누르세요."):
        st.cache_data.clear()
        st.rerun()

    with st.form("add_stock_form", clear_on_submit=True):
        st.write("🔍 **새 종목 추가**")
        
        # 1. 자동완성 검색 기능 (회사명)
        selected_stock = st.selectbox(
            "회사명으로 검색", 
            options=search_list, 
            index=None, 
            placeholder="예: 삼성전자 (초성 및 일부 검색 가능)"
        )
        
        # 2. 수동 코드 입력 (미국 주식이나 신규 상장 등)
        manual_ticker = st.text_input("또는 종목코드 직접 입력 (미국 주식 등)", placeholder="예: AAPL, TSLA, 005380")
        
        submitted = st.form_submit_button("➕ 종목 추가")
        
        if submitted:
            target_ticker = ""
            stock_name = ""
            
            # 검색창이나 수동입력창 중 하나라도 입력된 경우
            if selected_stock:
                # "삼성전자 (005930)" 형태에서 코드 추출
                target_ticker = selected_stock.split('(')[-1].replace(')', '').strip()
                stock_name = selected_stock.split('(')[0].strip()
            elif manual_ticker:
                target_ticker = manual_ticker.strip().upper()
                stock_name = target_ticker

            if target_ticker:
                with st.spinner("종목 검색 및 추가 중..."):
                    # 한국 주식 코드 6자리인 경우 자동으로 .KS / .KQ 붙여주기
                    if len(target_ticker) == 6 and target_ticker.isdigit():
                        try:
                            if not yf.Ticker(target_ticker + ".KS").history(period="1d").empty:
                                target_ticker += ".KS"
                            else:
                                target_ticker += ".KQ"
                        except:
                            target_ticker += ".KS"

                    if target_ticker not in portfolio:
                        # 수동 입력 시 회사명 가져오기
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

                        # 포트폴리오에 저장
                        portfolio[target_ticker] = {"price": 0.0, "qty": 0, "target": 0.0, "name": stock_name}
                        save_portfolio(portfolio)
                        st.success(f"{stock_name} 추가 완료!")
                        st.rerun()
                    else:
                        st.warning("이미 등록된 종목입니다.")
            else:
                st.warning("종목을 검색하거나 코드를 입력해주세요.")

    st.markdown("---")
    st.write("### 📂 현재 등록된 리스트")
    st.caption("삭제 버튼(❌)을 누르면 목록에서 지워집니다.")

    for sym in list(portfolio.keys()):
        col1, col2 = st.columns([4, 1])
        name = portfolio[sym].get('name', '')
        display_text = f"• **{name}** ({sym})" if name and name != sym else f"• {sym}"
        col1.write(display_text)
        if col2.button("❌", key=f"del_{sym}"):
            del portfolio[sym]
            save_portfolio(portfolio)
            st.rerun()

    st.markdown("---")
    min_score = st.slider("스크리닝 최소 점수 필터 (⭐)", 0, 12, 6)

# 메인 데이터 처리 (여기가 비어있으면 화면에 안 나옵니다!)
interest_results = process_tickers(user_tickers)

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 대시보드", "🎯 종목 스크리너", "🔍 심층 분석", "🧮 내 계좌(포트폴리오) 관리", "📖 투자 마스터 클래스 (전략)"])

with tab1:
    if market_df is not None:
        m_today = market_df.iloc[-1]['Close']
        m_prev = market_df.iloc[-2]['Close']
        m_trend = "상승장 (M조건 충족)" if market_df.iloc[-1]['Close'] > market_df['Close'].rolling(20).mean().iloc[-1] else "하락/조정장 (보수적 접근)"
        
        st.metric("KOSPI 지수", f"{m_today:,.2f}", f"{m_today - m_prev:,.2f}")
        
        # --- 🕒 한국 시간 기준 장중/장마감 판단 로직 추가 ---
        KST = timezone(timedelta(hours=9))
        now_kst = datetime.now(KST)
        is_market_open = now_kst.weekday() < 5 and (9 <= now_kst.hour < 15 or (now_kst.hour == 15 and now_kst.minute <= 30))
        market_status = "🟢 장중 (실시간 데이터 반영)" if is_market_open else "🔴 장 마감 (최종 종가 기준)"
        
        st.caption(f"**현재 시장 방향성:** {m_trend} &nbsp;|&nbsp; **시장 상태:** {market_status}")

    st.subheader("🏆 관심 종목 하이라이트")
    best_picks = [r for r in interest_results if r['score'] >= 8]
    if best_picks:
        bcols = st.columns(len(best_picks))
        for idx, pick in enumerate(best_picks):
            with bcols[idx]:
                if idx == 0: st.balloons()
                st.success(f"**{pick['name']}**")
                st.metric("종합 점수", f"{pick['score']} / 12", f"{'⭐' * pick['score']}")
    else:
        st.info("현재 관심 종목 중 8점 이상의 강력한 주도주 신호가 없거나, 등록된 종목이 없습니다. 좌측에서 종목을 추가해주세요.")

    st.markdown("---")
    st.subheader("📋 관심 종목 실시간 현황")
    if not interest_results:
        st.warning("왼쪽 ⚙️ 설정 창에서 관심 있는 종목(예: 삼성전자)을 먼저 추가해 주세요!")
    else:
        cols = st.columns(3)
        for i, res in enumerate(interest_results):
            with cols[i % 3]:
                st.write(f"**{res['name']}** ({res['symbol']})")
                st.write(f"점수: {'⭐' * res['score']}")
                st.progress(res['score'] / 12)
                st.write(f"현재가: **{res['today']['Close']:,.0f}원**")

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
                    sc1, sc2, sc3 = st.columns(3)
                    sc1.metric("현재가", f"{res['today']['Close']:,.0f}원")
                    positive_points = [k for k, v in res['score_details'].items() if v == 1]
                    sc2.write(", ".join(positive_points[:4]) + " 등")

                    if sc3.button("➕ 내 리스트에 추가", key=f"add_{res['symbol']}_{idx}"):
                        if res['symbol'] not in portfolio:
                            portfolio[res['symbol']] = {"price": 0.0, "qty": 0, "target": 0.0, "name": res['name']}
                            save_portfolio(portfolio)
                            st.toast(f"✅ {res['name']}이(가) 관심 종목에 추가되었습니다!")
                            st.rerun()
                        else:
                            st.info("이미 내 리스트에 등록된 종목입니다.")
        else:
            st.warning("현재 필터링 조건을 만족하는 종목이 시장에 없습니다.")

with tab3:
    st.subheader("🔍 기술적 추세 vs 재무 건전성 (CANSLIM 기반)")
    with st.expander("💡 차트 및 지표 읽는 법 (투자 보조 가이드)", expanded=False):
        st.markdown("""
        #### 📈 이동평균선(MA) 그래프 읽는 법
        * **캔들 차트 (빨간색/파란색 기둥):** 상승은 빨간색, 하락은 파란색.
        * **MA50 (50일선 - 주황색):** '기관 투자자의 생명선'. 주가가 이 선을 딛고 올라가면 강한 상승 모멘텀.
        * **MA150 & MA200 (150/200일선 - 녹색/보라색):** 대세 상승/하락을 가르는 '장기 마지노선'.
        #### ✅ 체크리스트 해석 가이드
        * **이평선 정배열 (MA50 > MA150 > MA200):** 안정적인 상승 추세.
        * **OBV 우상향:** 세력 매집 지표.
        * **RS 강도:** 시장 지수 대비 우위(대장주).
        * **ADX 추세강화:** 현재 주가 방향으로의 추세 강도 (25 이상 훌륭함).
        """)

    if not interest_results:
        st.info("왼쪽에서 관심 종목을 추가하시면 전문가용 차트와 심층 분석 리포트가 나타납니다.")
        
    for res in interest_results:
        with st.expander(f"{res['name']} ({res['symbol']}) 분석 리포트", expanded=False):
            chart_fig = draw_advanced_chart(res['df'].tail(120), res['name'])
            st.plotly_chart(chart_fig, use_container_width=True)

            st.markdown("---")
            c1, c2 = st.columns([1, 1])
            with c1:
                st.write("**기본적 분석 (Fundamental)**")
                st.metric("ROE (자기자본이익률)", f"{res['fund']['roe'] * 100:.1f}%" if res['fund']['roe'] else "N/A", help="15% 이상 우량")
                st.metric("영업이익률", f"{res['fund']['op_margin'] * 100:.1f}%" if res['fund']['op_margin'] else "N/A", help="10% 이상 우량")
                st.metric("매출성장률", f"{res['fund']['sales_growth'] * 100:.1f}%" if res['fund']['sales_growth'] else "N/A", help="20% 이상 우량")
            with c2:
                st.write("**체크리스트 (CANSLIM/VCP 분석)**")
                for label, val in res['score_details'].items():
                    if "[C]" in label or "[A]" in label or "[N]" in label or "[S]" in label or "[L]" in label or "[I]" in label:
                        st.markdown(f"{'✅' if val else '❌'} **{label}**")
                    else:
                        st.write(f"{'✅' if val else '❌'} {label}")

with tab4:
    st.subheader("🧮 내 계좌 관리 & 매도 타이밍 시그널")
    st.write("목표가와 손절가를 기반으로 언제 익절/손절해야 할지 모니터링합니다.")

    total_invested = 0
    total_current_val = 0

    if not interest_results:
        st.info("왼쪽에서 종목을 추가하시면 계좌 관리 기능이 활성화됩니다.")

    for res in interest_results:
        sym = res['symbol']
        p_data = portfolio.get(sym, {"price": 0.0, "qty": 0, "target": 0.0})
        curr_price = res['today']['Close']

        with st.expander(f"💼 {res['name']} ({sym}) - 현재가: {curr_price:,.0f}원", expanded=True):
            c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
            new_price = c1.number_input("매수 단가 (원)", value=float(p_data['price']), step=100.0, key=f"p_{sym}")
            new_qty = c2.number_input("보유 수량 (주)", value=int(p_data.get('qty', 0)), step=1, key=f"q_{sym}")
            new_target = c3.number_input("목표 단가 (원)", value=float(p_data.get('target', new_price * 1.2)), step=100.0, key=f"t_{sym}")

            stop_loss_price = new_price * 0.93

            if new_price > 0 and new_target > new_price:
                risk = new_price - stop_loss_price
                reward = new_target - new_price
                rr_ratio = reward / risk if risk > 0 else 0
                if rr_ratio >= 2.0:
                    rr_status = f"✅ 훌륭함 (1:{rr_ratio:.1f})"
                else:
                    rr_status = f"⚠️ 위험함 (1:{rr_ratio:.1f})"
                c4.metric("손익비 (Risk/Reward)", rr_status, help="최소 1:2 이상 권장")
            else:
                c4.info("매수/목표가를 입력하세요.")

            if st.button("💾 이 종목 정보 저장", key=f"save_{sym}"):
                portfolio[sym]['price'] = new_price
                portfolio[sym]['qty'] = new_qty
                portfolio[sym]['target'] = new_target
                portfolio[sym]['name'] = res['name']
                save_portfolio(portfolio)
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
                    st.success(f"🎯 **[목표가 도달]** 축하합니다! 설정하신 목표가({new_target:,.0f}원)를 돌파했습니다. **분할 매도 또는 전량 익절**을 고려하세요.")
                elif new_price > 0 and curr_price <= stop_loss_price:
                    st.error(f"🚨 **[손절가 이탈]** 현재가({curr_price:,.0f}원)가 손절선({stop_loss_price:,.0f}원) 아래로 내려갔습니다. 원칙에 따라 **기계적 손절**을 강력히 권장합니다.")
                elif new_price > 0:
                    if roi > 0:
                        st.info(f"🟢 **[보유 유지 - 수익 중]** 목표가({new_target:,.0f}원)까지 {new_target - curr_price:,.0f}원 남았습니다. 추세를 계속 즐기세요!")
                    else:
                        st.warning(f"🟡 **[보유 유지 - 손실 중]** 손절선({stop_loss_price:,.0f}원)까지 {curr_price - stop_loss_price:,.0f}원 여유가 있습니다. 손절선을 깨지 않는 한 보유하며 관망하세요.")

    st.markdown("---")
    st.subheader("📊 총 포트폴리오 현황")
    if total_invested > 0:
        total_profit = total_current_val - total_invested
        total_roi = (total_profit / total_invested) * 100
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("총 매수 금액", f"{total_invested:,.0f}원")
        mc2.metric("총 평가 금액", f"{total_current_val:,.0f}원")
        mc3.metric("총 수익률", f"{total_profit:,.0f}원", f"{total_roi:.2f}%")
    else:
        st.info("등록된 투자 정보가 없습니다. 종목별로 매수 정보를 저장해 보세요.")

with tab5:
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
