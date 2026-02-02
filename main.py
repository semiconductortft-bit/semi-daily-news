import os
import time
import requests  # 👈 [중요] 이 줄이 빠져 있었습니다! 꼭 넣어주세요.
import json      # 👈 json도 필요할 수 있으니 확인해주세요.
import feedparser
import urllib.parse
import base64
import smtplib # 이메일 기능을 위해 상단 확인 필요
from datetime import datetime, timedelta, timezone
from google import genai
from elevenlabs.client import ElevenLabs
from collections import defaultdict
from urllib.parse import urlparse
from dateutil import parser as date_parser
from googlenewsdecoder import gnewsdecoder
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 1. 환경 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = "cjVigY5qzO86Huf0OWal"
# [카카오 관련 키]
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET") # 보안 코드 추가

# 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)

def parse_date(date_str):
    """날짜 문자열을 datetime 객체로 변환 (정렬용)"""
    try:
        return date_parser.parse(date_str)
    except:
        return datetime.now()

from collections import defaultdict
from urllib.parse import urlparse

# 날짜 파싱 헬퍼 함수 (없을 경우를 대비해 추가)
def parse_date(date_str):
    try:
        from dateutil import parser
        return parser.parse(date_str)
    except:
        return datetime.now()

# 2. 키워드 및 타겟 매체 설정 (확장 버전)
KEYWORDS = [
    'semiconductor', 'advanced packaging', 'hbm', 'tsmc', 'samsung', 'sk hynix', 
    'wafer', 'chiplet', 'interposer', 'Hybrid Bonding', 'CoWoS', 'FOWLP', 'intel',
    'Glass Substrate', 'TC-NCF', 'MUF', 'EMC', 'CXL', 'BSPDN', 'Silicon Photonics',
    'Logic Semiconductor', 'Foundry', 'Automotive Chip', 'NVIDIA', 'AMD'
]

GLOBAL_TARGETS = {
    "semiengineering.com": "Semiconductor Engineering",
    "3dincites.com": "3D InCites",
    "digitimes.com": "Digitimes",
    "eetimes.com": "EE Times",
    "trendforce.com": "TrendForce",
    "semiconductor-digest.com": "Semi Digest",
    "yolegroup.com": "Yole Group",
    "kipost.net": "KIPOST",
    "wccftech.com": "Wccftech",           # 하드웨어/반도체 뉴스 강자
    "techpowerup.com": "TechPowerUp",     # GPU/CPU 상세 기술 뉴스
    "eenewsembedded.com": "eeNews Embedded", # 임베디드/유럽권 뉴스
    "prnewswire.com": "PR Newswire",      # 보도자료 (APAC 포함)
    "asia.nikkei.com": "Nikkei Asia"      # 일본/아시아 시장 분석
}

KOREA_TARGETS = {
    "thelec.kr": "TheElec",
    "etnews.com": "ETNews",
    "zdnet.co.kr": "ZDNet Korea",
    "hankyung.com": "Hankyung Insight"
}

# --- [기능 1] 날씨 정보 ---
def get_weather_info():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=36.99&longitude=127.11&current=temperature_2m,weather_code,pm10&timezone=Asia%2FSeoul"
        res = requests.get(url).json()
        current = res.get('current', {})
        temp = current.get('temperature_2m', 0)
        code = current.get('weather_code', 0)
        
        weather_desc = "맑음"
        if code in [1, 2, 3]: weather_desc = "구름 조금"
        elif code in [45, 48]: weather_desc = "안개"
        elif code in [51, 53, 55, 61, 63, 65]: weather_desc = "비"
        elif code in [71, 73, 75, 85, 86]: weather_desc = "눈"
        elif code >= 95: weather_desc = "뇌우"
        return f"{temp}°C, {weather_desc}"
    except: return "기온 정보 없음"

# --- [기능 2] 카카오 토큰 자동 갱신 (핵심 기능) ---
def get_new_kakao_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "client_secret": KAKAO_CLIENT_SECRET, # 보안 코드가 필수입니다!
        "refresh_token": KAKAO_REFRESH_TOKEN
    }
    
    try:
        response = requests.post(url, data=data)
        tokens = response.json()
        if "access_token" in tokens:
            return tokens["access_token"]
        else:
            print(f"❌ 토큰 갱신 실패: {tokens}")
            return None
    except Exception as e:
        print(f"❌ 토큰 요청 중 에러: {e}")
        return None

# --- [기능 3] 카카오톡 전송 ---
def send_kakao_message(briefing_text, report_url):
    # 1. 새로운 액세스 토큰 발급 (매일 아침 수행)
    access_token = get_new_kakao_token()
    if not access_token:
        print("⚠️ 토큰 발급 실패로 카톡 전송을 건너뜁니다.")
        return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    # 메시지 1: 알림 및 링크
    payload1 = {"template_object": json.dumps({
        "object_type": "text",
        "text": f"김동휘입니다. 뉴스레터와 함께 좋은 하루 보내세요!\n자세한 내용은 : {report_url}",
        "link": {"web_url": report_url, "mobile_web_url": report_url},
        "button_title": "리포트 바로가기"
    })}

    # 메시지 2: 요약 브리핑
    payload2 = {"template_object": json.dumps({
        "object_type": "text",
        "text": briefing_text,
        "link": {"web_url": report_url, "mobile_web_url": report_url}
    })}

    try:
        requests.post(url, headers=headers, data=payload1)
        time.sleep(1)
        requests.post(url, headers=headers, data=payload2)
        print("✅ 카카오톡 전송 성공")
    except Exception as e:
        print(f"❌ 전송 실패: {e}")

# --- [수정] 카카오톡 브리핑 멘트 생성 (모델 로테이션 + 재시도 전략) ---
def generate_kakao_briefing(news_text, weather_str):
    print("💬 카카오톡 브리핑 멘트 생성 중... (모델 로테이션 모드)")
    
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    today_str = now.strftime("%m-%d")
    
    # 1. 사용할 모델 리스트 (앞쪽 모델이 실패하면 뒤쪽 모델이 투입됩니다)
    available_models = [
        "gemini-2.0-flash",       # 1순위: 가장 빠르고 가성비 좋음
        "gemini-2.5-flash",       # 2순위: 최신 플래시 (가상)
        "gemini-1.5-flash",       # 3순위: 안정적인 구형 플래시
        "gemini-flash-latest",    # 4순위: 최신 별칭
        "gemini-2.5-pro",         # 5순위: 고성능 (느릴 수 있음)
        "gemini-pro-latest"       # 6순위: 최후의 보루
    ]
    
    # 예시 스타일을 프롬프트에 직접 입력해서 학습시킵니다.
    prompt = f"""
    당신은 테크 뉴스 전문 큐레이터입니다. 
    아래 [뉴스 데이터]를 바탕으로, 카카오톡으로 발송할 '핵심 요약 브리핑'을 작성해주세요.
    
    [입력 정보]
    - 날씨: {weather_str} (평택 기준)
    - 날짜: {today_str}
    
    [필수 작성 양식 - 이대로만 출력하세요]
    
    ❄️ (날씨와 기온을 언급하며, 따뜻한 안부 인사 1문장. 예: 오늘은 -5°C에 흐린 날씨, 따뜻하게 입으세요!)
    
    ---
    
    🚀 Semi-TFT 오늘의 브리핑 ({today_str}, 06:00 발송)
    
    # 1️⃣ (가장 중요한 뉴스 제목 - 핵심만 짧게)
    (본문 요약 1~2문장)
    🗨️ *Insight*: (실무자 관점의 한 줄 평가/전망)
    
    # 2️⃣ (두 번째 중요한 뉴스 제목)
    (본문 요약 1~2문장)
    🗨️ *Insight*: (한 줄 평가)
    
    # 3️⃣ (세 번째 중요한 뉴스 제목)
    (본문 요약 1~2문장)
    
    # 4️⃣ (네 번째 중요한 뉴스 제목)
    (본문 요약 1~2문장)
    
    # 5️⃣ (다섯 번째 중요한 뉴스 제목)
    (본문 요약 1~2문장)

    ---
    
    📌 오늘의 한마디
    (반도체/테크 업계 종사자에게 힘이 되는 격려나 통찰 한 문장)
    
    🌟 (마무리 인사 1문장)
    
    [데이터]:
    {news_text}
    """

# 3. 모델 순환 시도 (핵심 로직)
    for model_name in available_models:
        try:
            print(f"   🔄 시도 중인 모델: {model_name}...")
            response = client.models.generate_content(model=model_name, contents=prompt)
            
            if response.text:
                print(f"   ✅ 성공! ({model_name} 사용됨)")
                return response.text
                
        except Exception as e:
            error_msg = str(e)
            print(f"   ❌ {model_name} 실패: {error_msg[:100]}...") # 에러 로그 짧게 출력
            
            # 429 에러(쿼터 초과)일 경우에만 잠시 대기 후 다음 모델로 넘어감
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                print("      ⏳ 쿼터 초과! 5초 숨 고르고 다음 모델 투입합니다.")
                time.sleep(5) 
                continue # 다음 for문으로 넘어감 (다음 모델 실행)
            
            # 그 외 에러도 일단 다음 모델 시도
            time.sleep(2)
            continue

    # 4. 모든 모델이 다 실패했을 경우 (최후의 수단)
    print("   😱 모든 모델 가동 실패.")
    return f"❄️ 오늘의 브리핑\n\n죄송합니다. 현재 AI 서버 접속량이 많아 요약을 불러오지 못했습니다.\n아래 [리포트 바로가기] 버튼을 눌러 전체 내용을 확인해주세요!"
    
def fetch_news():
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    weekday = now_kst.weekday()

    # [Q3 반영] 일요일(6)은 발행 중단
    if weekday == 6:
        print("📅 일요일은 리포트를 휴간합니다.")
        return None

    # [Q3 반영] 월요일(0)은 7일치(주간), 나머지는 1일치(데일리)
    search_period = "7d" if weekday == 0 else "1d"
    cutoff_hours = 168 if weekday == 0 else 30
    cutoff_date = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)

    all_targets = {**GLOBAL_TARGETS, **KOREA_TARGETS}
    raw_articles = []

    def get_rss_entries(targets, region, lang):
        site_query = " OR ".join([f"site:{d}" for d in targets.keys()])
        kw_query = " OR ".join(KEYWORDS)
        final_query = f"({site_query}) AND ({kw_query})"
        encoded_query = urllib.parse.quote(final_query)
        url = f"https://news.google.com/rss/search?q={encoded_query}+when:{search_period}&hl={lang}&gl={region}&ceid={region}:{lang}"
        return feedparser.parse(url).entries

    print(f"📡 뉴스 수집 중... (모드: {'주간 하이라이트' if weekday==0 else '데일리'})")
    raw_articles.extend(get_rss_entries(GLOBAL_TARGETS, "US", "en-US"))
    raw_articles.extend(get_rss_entries(KOREA_TARGETS, "KR", "ko"))

    valid_articles = []
    seen_links = set()

    for e in raw_articles:
        if e.link in seen_links: continue
        
        try:
            pub_date = date_parser.parse(e.published)
            if pub_date.tzinfo is None: pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff_date: continue
        except: continue

        # [딕셔너리 에러 해결 지점]
        try:
            decoded_res = gnewsdecoder(e.link)
            if isinstance(decoded_res, dict):
                original_url = decoded_res.get('decoded_url', e.link)
            else:
                original_url = decoded_res if decoded_res else e.link
        except:
            original_url = e.link

        # 문자열 보장
        original_url = str(original_url)

        domain = urlparse(original_url).netloc.replace("www.", "")
        source_name = "News"
        for t_domain, t_name in all_targets.items():
            if t_domain in domain:
                source_name = t_name
                break
        
        e['display_source'] = source_name
        e['parsed_date'] = pub_date
        e['clean_url'] = original_url
        valid_articles.append(e)
        seen_links.add(e.link)

    # [Q3 반영] 매체별 균형 선별 (최소 1개, 최대 2개) 후 총 10개 채우기
    buckets = defaultdict(list)
    for e in valid_articles: buckets[e['display_source']].append(e)
    
    final_selection = []
    sources = list(buckets.keys())
    if not sources: return "최근 관련 뉴스가 없습니다."

    # 라운드 로빈 방식으로 10개 추출
    idx = 0
    while len(final_selection) < 10 and any(buckets.values()):
        src = sources[idx % len(sources)]
        if buckets[src]:
            final_selection.append(buckets[src].pop(0))
        idx += 1

    final_selection.sort(key=lambda x: x['parsed_date'], reverse=True)
    
    formatted_text = []
    for i, e in enumerate(final_selection):
        item = f"[{i+1}] Source: {e['display_source']}\nTitle: {e.title}\nURL: {e['clean_url']}\nSummary: {e.summary[:200] if hasattr(e, 'summary') else ''}\n"
        formatted_text.append(item)
    
    return "\n".join(formatted_text)

def generate_content(news_text):
    """Gemini를 이용해 뉴스레터와 라디오 스크립트 생성"""
    print("🤖 AI 분석 및 집필 중... (가독성 최적화 모드)")
    
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today_date = now_kst.strftime("%Y년 %m월 %d일")
    publisher = "반도체재료개발TFT 김동휘"
    
    # [수정 1] 요일에 따른 뉴스 제목 분기 처리
    # 월요일(weekday=0)이면 Weekly, 그 외는 Daily
    if now_kst.weekday() == 0:
        report_title = "Semi-TFT Weekly News"
        intro_ment = "지난 한 주간의 반도체 핵심 이슈를 정리해 드립니다."
    else:
        report_title = "Semi-TFT Daily News"
        intro_ment = "오늘 아침 확인해야 할 반도체 주요 소식입니다."
  
    # 프롬프트 설계
    prompt = f"""
    당신은 반도체 산업 수석 전략가이자 인기 테크 뉴스레터 발행인입니다.
    오늘 날짜는 {today_date}, 발행인은 '{publisher}'입니다.
    
    ---
    제공된 [분석할 뉴스 데이터]에 포함되지 않은 내용은 절대 창작하거나 포함하지 마십시오. 뉴스 데이터가 부족하면 '관련 뉴스 없음'이라고 명시하십시오.
    해당 날짜 기준 24시간 이내의 기사를 인용하므로, 무조건 공개된 사이트에서 확인 검증할 수 있는 내용이어야 합니다.
    
    - 반드시 마크다운(Markdown) 형식을 사용하세요.
    - **가독성 원칙**: 줄글로 길게 쓰지 말고, 불렛 포인트와 볼드체를 적극 활용하세요.
    **중요: 출처 표기 방식 (Hyperlink)**
    - 각 뉴스 항목의 끝에는 반드시 원본 기사로 이동하는 링크를 걸어야 합니다.
    - 형식: `[출처: [언론사명](기사URL)]`
    - 예시: `...전망입니다. [출처: [Digitimes](https://www.digitimes.com/...)]`
    - 제공된 데이터의 'URL' 필드 값을 정확히 사용하세요. 가짜 링크를 만들지 마세요.
    - 언론사명은 영어면 영어, 한글이면 한글 그대로 표기하세요.
    ***[필수 서식 규칙 - 엄수하세요]***
    1. **모든 섹션 제목(#) 다음에는 무조건 두 번 줄바꿈(\\n\\n)을 하세요.**
    2. **뉴스 항목의 '제목'과 '요약 내용' 사이는 무조건 줄바꿈하세요.**
    3. 절대 `|` (파이프) 문자를 사용하여 표(Table) 형식을 만들지 마세요. 가독성이 떨어집니다.
    4. 출처 링크는 반드시 `[출처: [언론사명](URL)]` 형식을 지키세요.
    
    **1. 헤더 (Header)**
    # 📦 오늘의 반도체 뉴스
    ##### {today_date} | 발행인: {publisher}
    
    **2. Executive Summary (요약, 볼드체)**
    - 아랫줄로 옮겨서, `###💡 Executive Summary`
    - 전체 시장 흐름을 5줄 이내로 요약하세요. 핵심 키워드는 **볼드체**로 강조하세요.
    
    **3. Key Insights (핵심 뉴스)**
    - `###🌍 Market & Tech Insights`(볼드체)
    - 수집된 데이터 중 가장 중요한 뉴스를 꼭 10개 선정해주세요. "관련 뉴스 없음"과 같은 불필요한 문구는 절대 포함하지 마십시오.
    - 아래 형식을 반드시 지킬 것.
    1. **[기업명] 뉴스 제목 (볼드체)**
    (줄바꿈)
    뉴스 내용 3문장 요약... [출처: [언론사명](URL)]
    (줄바꿈)
    (줄바꿈)

    2. **[기업명] 뉴스 제목 (볼드체)**
    (줄바꿈)
    뉴스 내용 3문장 요약... [출처: [언론사명](URL)]
    
    (... 10번까지 반복)
        
    **4. Technical Term (용어 해설)**
    - `###📚 Technical Term`
    - **[용어명 (한글/영어)]**
    (줄바꿈)
    - Technical Term: 'BSPDN', 'Glass Substrate', 'Hybrid Bonding' 등 반도체 전문가 수준의 심도 있는 기술 용어 1개를 선정해 상세히 설명하세요.

    **5. Footer (저작권 및 보안 경고)**
    - 리포트 맨 마지막에 반드시 다음 문구를 볼드체로 포함하세요:
    `ⓒ 2026 {publisher}. All rights reserved.`
    `무단 전재, 복사, 외부 배포 엄금`
    ---
    
    - 구분자 `|라디오 스크립트|`를 먼저 적고 내용을 작성하세요.
    - **오프닝**: "안녕하세요, 반도체재료개발TFT 김동휘입니다. {today_date}, 오늘 아침 확인해야 할 반도체 패키징 주요 소식 전해드립니다."
    - **본문**: 뉴스레터의 핵심만 요약하여 40초 분량으로 작성하세요.
    - **어조**: "최근 ~라는 소식입니다.", "~할 전망입니다." 등 차분하고 신뢰감 있는 뉴스 브리핑 톤(하십시오체 위주)을 사용하세요.
    - 지시문(BGM 등)은 절대 포함하지 마세요.
    - 마지막 문구: 보고서의 맨 마지막은 반드시 "오늘도 좋은 하루 보내시기 바랍니다."로 끝맺음 하세요.    
    ---
    
    [분석할 뉴스 데이터]:
    {news_text}
    """
    
    # 조회된 모델 중 텍스트 생성에 적합한 모델 선택
    available_models = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        "gemini-pro-latest"
    ]
    
    for model_name in available_models:
        try:
            print(f"\n공정 시도 중: {model_name}...")
            
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            
            # 응답 텍스트 추출
            if response and hasattr(response, 'text') and response.text:
                print(f"✅ {model_name} 가동 성공!")
                return response.text
            
            # 대체 응답 구조
            if response and hasattr(response, 'candidates') and response.candidates:
                if hasattr(response.candidates[0], 'content'):
                    text = response.candidates[0].content.parts[0].text
                    print(f"✅ {model_name} 가동 성공!")
                    return text
                
        except Exception as e:
            error_msg = str(e)[:300]
            print(f"❌ {model_name} 가동 실패: {error_msg}")
            time.sleep(1)
            continue
    
    raise Exception("모든 엔진이 응답하지 않습니다. API 키와 권한을 확인하세요.")

def generate_audio(script):
    try:
        if not ELEVENLABS_API_KEY or ELEVENLABS_API_KEY == "":
            print("⚠️ ElevenLabs API 키가 설정되지 않았습니다.")
            print("💡 GitHub Settings → Secrets → Actions → ELEVENLABS_API_KEY를 추가하세요.")
            print("💡 https://elevenlabs.io 에서 API 키를 발급받을 수 있습니다.")
            return
        
        print(f"🎙️ 음성 생성 중... (스크립트 길이: {len(script)} 문자)")
        
        # 너무 긴 텍스트는 잘라내기 (ElevenLabs 제한 고려)
        max_chars = 5000
        if len(script) > max_chars:
            print(f"⚠️ 스크립트가 너무 깁니다. {max_chars}자로 제한합니다.")
            script = script[:max_chars]
        
        el_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        
        # 최신 API 방식: text_to_speech.convert 사용
        audio_generator = el_client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=script,
            model_id="eleven_multilingual_v2"
        )
        
        # MP3 파일로 저장
        with open("radio.mp3", "wb") as f:
            for chunk in audio_generator:
                if chunk:
                    f.write(chunk)
        
        # 파일 크기 확인
        if os.path.exists("radio.mp3"):
            file_size = os.path.getsize("radio.mp3")
            print(f"✅ 오디오 생성 완료! (파일 크기: {file_size:,} 바이트)")
        else:
            print("⚠️ 오디오 파일 생성에 실패했습니다.")
            
    except Exception as e:
        print(f"⚠️ 오디오 생성 실패: {e}")
        print("💡 ElevenLabs API 키, 할당량, 네트워크 연결을 확인하세요.")

def save_newsletter(content):
    import os
    import shutil
    from datetime import datetime, timedelta, timezone

    # 1. 오늘 날짜 가져오기 (한국 시간 기준)
    KST = timezone(timedelta(hours=9))
    date_str = datetime.now(KST).strftime("%Y-%m-%d") # 예: "2026-01-31"
    
    # 2. 날짜별 폴더 경로 설정 및 생성
    # 'newsletter/2026-01-31' 이라는 폴더를 만듭니다.
    folder_path = f"newsletter/{date_str}"
    if not os.path.exists(folder_path):
        os.makedirs(folder_path, exist_ok=True)

    # 3. 생성된 오디오 파일(radio.mp3)을 해당 날짜 폴더로 이동
    audio_filename = "radio.mp3"
    target_audio_path = os.path.join(folder_path, audio_filename)
    
    if os.path.exists("radio.mp3"):
        shutil.move("radio.mp3", target_audio_path)
        print(f"✅ 오디오 파일을 {folder_path} 폴더로 옮겼습니다.")

    # 4. 각 페이지용 오디오 플레이어 태그 만들기
    # 이 페이지(index.md)와 오디오(radio.mp3)는 같은 폴더에 있게 되므로 파일 이름만 씁니다.
    audio_player_html = f"<audio controls style='width: 100%;'><source src='{audio_filename}' type='audio/mpeg'></audio>\n\n---\n\n"

    # 5. [중요] 날짜별 고유 페이지 저장
    # newsletter/2026-01-31/index.md 경로에 저장합니다.
    with open(os.path.join(folder_path, "index.md"), "w", encoding="utf-8") as f:
        f.write(audio_player_html + content)
    print(f"📝 고유 주소용 페이지 생성 완료: {folder_path}/index.md")

    # 6. 메인 페이지(최상위 index.md) 업데이트
    # 사용자가 처음 접속했을 때 바로 최신 글을 볼 수 있게 루트 폴더에도 저장합니다.
    # 이때 오디오 경로는 폴더명을 포함해야 메인에서 소리가 납니다.
    main_audio_player = f"<audio controls style='width: 100%;'><source src='{folder_path}/{audio_filename}' type='audio/mpeg'></audio>\n\n---\n\n"
    with open("index.md", "w", encoding="utf-8") as f:
        f.write(main_audio_player + content)
    print("🏠 메인 페이지 업데이트 완료")

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(subject, body, to_email):
    # 깃허브 시크릿에 저장한 이메일 계정 정보 사용
    gmail_user = os.getenv("GMAIL_USER") 
    gmail_password = os.getenv("GMAIL_APP_PASSWORD") # 일반 비밀번호가 아닌 '앱 비밀번호'

    msg = MIMEMultipart()
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html')) # 마크다운 대신 HTML로 보내면 더 예쁩니다.

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(gmail_user, gmail_password)
        server.send_message(msg)
        server.close()
        print("📧 이메일 발송 성공!")
    except Exception as e:
        print(f"❌ 이메일 발송 실패: {e}")

# --- 메인 실행 ---
if __name__ == "__main__":
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")
    
    print("🚀 반도체 리포트 생산 공정 개시")
    print(f"📅 기준 날짜: {date_str}")

    try:
        # 1. 뉴스 수집
        raw_data = fetch_news()
        
        if raw_data is None:
            print("🛑 발행 조건 미충족으로 종료합니다.")
            exit(0)

        if isinstance(raw_data, list):
            formatted_news = []
            for i, e in enumerate(raw_data[:12]): # 12개 넉넉히
                clean_summ = e.summary.replace("<b>", "").replace("</b>", "") if hasattr(e, 'summary') else ""
                item = f"[{i+1}] Source: {e['display_source']}\nTitle: {e.title}\nURL: {e['clean_url']}\nSummary: {clean_summ[:300]}\n"
                formatted_news.append(item)
            news_text = "\n".join(formatted_news)
        else:
            news_text = raw_data

        # 2. 본문 생성 (Gemini)
        full_text = generate_content(news_text)
        print(f"✅ 콘텐츠 생성 완료")

        # 3. 라디오 생성
        if "라디오 스크립트" in full_text:
            script = full_text.split("라디오 스크립트")[-1].strip()
        else:
            script = full_text[:500]
        generate_audio(script)

        # 4. 파일 저장
        save_newsletter(full_text)
        web_url = f"https://semiconductortft-bit.github.io/semi-daily-news/newsletter/{date_str}/"

        # -------------------------------------------------------
        # [핵심] API 쿼터 확보를 위한 강제 휴식 (에러 방지용)
        # -------------------------------------------------------
        print("\n☕ AI 휴식 중... (API 에러 방지를 위해 60초 대기)")
        time.sleep(60) 
        # -------------------------------------------------------

        # 5. 카카오톡 발송
        print("\n💬 카카오톡 발송 프로세스 시작...")
        weather_info = get_weather_info()
        print(f"☀️ 현재 날씨: {weather_info}")
        
        # 브리핑 생성
        kakao_briefing = generate_kakao_briefing(news_text[:2500], weather_info)
        send_kakao_message(kakao_briefing, web_url)

        # 6. 이메일 발송
        print("\n📧 이메일 발송 준비 중...")
        mail_subject = f"📦 [반도체 데일리 뉴스] {date_str} 리포트"
        email_body = full_text.replace("\n", "<br>")
        send_email(mail_subject, email_body, "keenhwi@gmail.com")
        
        print("\n✅✅✅ 모든 공정이 성공적으로 완료되었습니다! ✅✅✅")
        
    except Exception as error:
        print(f"\n⚠️ 시스템 에러 발생: {error}")
