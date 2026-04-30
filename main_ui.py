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
                # 구버전 데이터를 신규 다중 태그(types) 시스템으로 자동 마이그레이션 & DB 청소
                migrated_data = {}
                need_update = False
                for k, v in data.items():
                    base_sym = k.split('_')[0] if '_' in k else k
                    if base_sym != k:
                        need_update = True 
                        
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
                
                if need_update:
                    doc_ref.set(migrated_data)
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
        df['MA20'] = ta.sma(df['
