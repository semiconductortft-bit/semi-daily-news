import os
import time
import requests
import json
import feedparser
import urllib.parse
import smtplib
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
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")

# 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)

# 2. 키워드 및 타겟 매체 설정 (확장판)
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
    "wccftech.com": "Wccftech",
    "techpowerup.com": "TechPowerUp",
    "eenewsembedded.com": "eeNews Embedded",
    "prnewswire.com": "PR Newswire",
    "asia.nikkei.com": "Nikkei Asia"
}

KOREA_TARGETS = {
    "thelec.kr": "TheElec",
    "etnews.com": "ETNews",
    "zdnet.co.kr": "ZDNet Korea",
    "hankyung.com": "Hankyung Insight"
}

def parse_date(date_str):
    try:
        return date_parser.parse(date_str)
    except:
        return datetime.now()

def fetch_news():
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    weekday = now_kst.weekday()

    if weekday == 6:
        print("📅 일요일은 리포트를 휴간합니다.")
        return None

    # [중요] 기사 확보를 위해 평일에도 2일치 검색
    search_period = "7d" if weekday == 0 else "2d"
    cutoff_hours = 168 if weekday == 0 else 48
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

    print(f"📡 뉴스 수집 중... (기간: {search_period})")
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

        try:
            decoded_res = gnewsdecoder(e.link)
            if isinstance(decoded_res, dict):
                original_url = decoded_res.get('decoded_url', e.link)
            else:
                original_url = decoded_res if decoded_res else e.link
        except:
            original_url = e.link

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

    buckets = defaultdict(list)
    for e in valid_articles: buckets[e['display_source']].append(e)
    
    final_selection = []
    sources = list(buckets.keys())
    if not sources: return "최근 관련 뉴스가 없습니다."

    idx = 0
    while len(final_selection) < 10 and any(buckets.values()):
        src = sources[idx % len(sources)]
        if buckets[src]:
            final_selection.append(buckets[src].pop(0))
        idx += 1

    final_selection.sort(key=lambda x: x['parsed_date'], reverse=True)
    
    formatted_text = []
    for i, e in enumerate(final_selection):
        # AI에게 줄 때는 요약 내용 포함
        clean_summ = e.summary.replace("<b>", "").replace("</b>", "") if hasattr(e, 'summary') else ""
        item = f"[{i+1}] Source: {e['display_source']}\nTitle: {e.title}\nURL: {e['clean_url']}\nSummary: {clean_summ[:200]}\n"
        formatted_text.append(item)
    
    return "\n".join(formatted_text)

def generate_content(news_text):
    print("🤖 AI 전체 리포트 작성 중...")
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today_date = now_kst.strftime("%Y년 %m월 %d일")
    publisher = "반도체재료개발TFT 김동휘"
    
    report_title = "Semi-TFT Weekly News" if now_kst.weekday() == 0 else "Semi-TFT Daily News"

    prompt = f"""
    당신은 반도체 산업 수석 전략가입니다. 아래 [뉴스 데이터]를 기반으로 '{report_title}'를 작성하세요.
    날짜: {today_date}, 발행인: {publisher}

    [필수 형식 - 마크다운]
    # 📦 오늘의 반도체 뉴스
    ##### {today_date} | 발행인: {publisher}

    💡 **Executive Summary**
    (줄바꿈)
    (시장 흐름 5줄 요약, 핵심 키워드 볼드체)

    🌍 **Market & Tech Insights**
    (뉴스 10개 각각 아래 형식으로 작성)
    1. **[기업명] 뉴스 제목**
    (내용 3문장 요약) [출처: [언론사명](URL)]
    * 중요: 출처 표기 시 반드시 `[출처: [TrendForce](https://...)]` 와 같이 대괄호를 중첩하여, 리포트 상에서는 `[출처: TrendForce]` 라는 텍스트에 하이퍼링크가 걸리도록 작성할 것. URL을 괄호 `()` 안에 그대로 텍스트로 노출하지 말 것.
    
    📚 **Technical Term**
    (본문 중 전문 용어 1개 제시)
    (줄바꿈) 
    상세 해설 5줄이내

   (줄바꿈)
    ⓒ 2026 {publisher}. All rights reserved.🚫무단 전재, 복사, 외부 배포 엄금
   
   (줄바꿈, 실선)
    |라디오 스크립트|
    안녕하세요, 반도체재료개발TFT 김동휘입니다. {today_date}, 오늘 아침 확인해야 할 주요 소식입니다.
    (뉴스 핵심 요약 40초 분량, 하십시오체)
    오늘도 좋은 하루 보내시기 바랍니다.

    [뉴스 데이터]:
    {news_text}
    """
    
    # 리포트 작성은 가장 성능 좋은 모델 시도
    models = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        "gemini-pro-latest"
    ]
    for m in models:
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
            if resp.text: return resp.text
        except: continue
    return "리포트 생성 실패"

# --- [수정 완료] 여러 모델 순차 시도 및 에러 방지 ---
def generate_kakao_briefing(news_text, weather_str):
    print("💬 카카오톡 브리핑 생성 시도... (안전장치 모드)")
    KST = timezone(timedelta(hours=9))
    today_str = datetime.now(KST).strftime("%m-%d")

    # 1. 사용할 모델 리스트 (우선순위 순서대로)
    # 리스트는 프롬프트 밖(파이썬 코드 영역)에 있어야 합니다!
    models = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash",
        "gemini-flash-latest",
        "gemini-pro-latest"
    ]

    prompt = f"""
    당신은 테크 뉴스 큐레이터입니다.
    아래 [뉴스 데이터]를 보고 카카오톡 브리핑을 작성하세요.
    **길이는 공백 포함 900자 이내 필수.**

    [형식]
    ❄️ (날씨/기온 언급 + 따뜻한 인사 1문장)
    ---
    🚀 오늘의 브리핑 ({today_str})
    
    💡 **Executive Summary**
    (3줄 요약)
    
    📰 **Headlines**
    1. (제목)
    2. (제목)
    ...
    
    ---
    📌 (마무리 인사)

    [데이터]:
    {news_text}
    """

# 2. AI 시도 (모델 리스트를 돌면서 성공할 때까지 시도)
    for model_name in models:
        try:
            print(f"   🔄 시도 중: {model_name}...")
            response = client.models.generate_content(model=model_name, contents=prompt)
            
            if response.text:
                print(f"   ✅ 성공 ({model_name})")
                return response.text
                
        except Exception as e:
            print(f"   ⚠️ {model_name} 실패: {e}")
            time.sleep(1) # 잠시 대기 후 다음 모델 시도
            continue

    # 3. 모든 모델 실패 시 -> 비상 모드 (파이썬 강제 조립)
    print("🚨 모든 모델 실패. 비상 모드(파이썬 강제 조립) 가동")
    titles = []
    for line in news_text.split('\n'):
        if line.startswith("Title:"):
            titles.append(line.replace("Title:", "").strip())
    
    fallback_msg = f"""❄️ {weather_str}, 기분 좋은 아침입니다!

    ---

    🚀 오늘의 브리핑 ({today_str})

    💡 **Executive Summary**
    (AI 서비스 지연으로 헤드라인 위주로 전해드립니다. 자세한 내용은 리포트를 확인해주세요.)

    📰 **Headlines**"""

    for i, t in enumerate(titles[:10]):
        fallback_msg += f"\n{i+1}. {t}"

    fallback_msg += f"\n\n---\n\n📌 오늘도 즐거운 하루 보내세요!"
    return fallback_msg

def get_weather_info():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=36.99&longitude=127.11&current=temperature_2m,weather_code&timezone=Asia%2FSeoul"
        res = requests.get(url).json()
        current = res.get('current', {})
        temp = current.get('temperature_2m', 0)
        code = current.get('weather_code', 0)
        
        desc = "맑음"
        if code in [1, 2, 3]: desc = "구름 조금"
        elif code in [45, 48]: desc = "안개"
        elif code >= 51: desc = "비/눈"
        
        return f"{temp}°C, {desc}"
    except: return "기온 정보 없음"

def get_new_kakao_token():
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "client_secret": KAKAO_CLIENT_SECRET,
        "refresh_token": KAKAO_REFRESH_TOKEN
    }
    try:
        res = requests.post(url, data=data)
        tokens = res.json()
        return tokens.get("access_token")
    except: return None

# --- [핵심] 버튼 강제 삽입 & URL 숨김 전송 ---
def send_kakao_message(briefing_text, report_url):
    access_token = get_new_kakao_token()
    if not access_token: return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    # 2. [고정 문구 설정] 머리말과 꼬리말 정의
    header = "안녕하세요. 김동휘입니다."
    footer = f"자세한 내용은 : {report_url}"

    # 본문 길이 자르기 (900자)
    safe_text = briefing_text[:900] + "\n...(중략)" if len(briefing_text) > 900 else briefing_text
    
    final_text = f"{header}\n\n{safe_text}\n\n{footer}"

    # 버튼 강제 생성 템플릿
    template = {
        "object_type": "text",
        "text": final_text,
        "link": {"web_url": report_url, "mobile_web_url": report_url},
        "buttons": [
            {
                "title": "리포트 전체 보기 🔗",
                "link": {"web_url": report_url, "mobile_web_url": report_url}
            }
        ]
    }

    try:
        res = requests.post(url, headers=headers, data={"template_object": json.dumps(template)})
        if res.status_code == 200: print("✅ 카카오톡 전송 성공")
        else: print(f"❌ 전송 실패: {res.text}")
    except Exception as e: print(f"❌ 전송 에러: {e}")

def generate_audio(script):
    try:
        if not ELEVENLABS_API_KEY: return
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        audio = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=script[:5000],
            model_id="eleven_multilingual_v2"
        )
        with open("radio.mp3", "wb") as f:
            for chunk in audio: f.write(chunk)
    except Exception as e: print(f"⚠️ 오디오 실패: {e}")

def save_newsletter(content):
    import shutil
    KST = timezone(timedelta(hours=9))
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    folder = f"newsletter/{date_str}"
    if not os.path.exists(folder): os.makedirs(folder, exist_ok=True)
    
    if os.path.exists("radio.mp3"):
        shutil.move("radio.mp3", os.path.join(folder, "radio.mp3"))
        
    audio_tag = f"<audio controls style='width:100%'><source src='radio.mp3'></audio>\n\n---\n\n"
    with open(f"{folder}/index.md", "w", encoding="utf-8") as f: f.write(audio_tag + content)
    
    main_audio = f"<audio controls style='width:100%'><source src='{folder}/radio.mp3'></audio>\n\n---\n\n"
    with open("index.md", "w", encoding="utf-8") as f: f.write(main_audio + content)

def send_email(subject, body, to_email):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pw = os.getenv("GMAIL_APP_PASSWORD")
    msg = MIMEMultipart()
    msg['From'] = gmail_user
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    try:
        s = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        s.login(gmail_user, gmail_pw)
        s.send_message(msg)
        s.quit()
        print("📧 이메일 전송 성공")
    except Exception as e: print(f"❌ 이메일 실패: {e}")

# --- 메인 실행 ---
if __name__ == "__main__":
    try:
        print("🚀 리포트 공정 시작")
        raw_data = fetch_news()
        
        # 데이터가 리스트면 텍스트로 변환, 없으면 종료
        if not raw_data: 
            print("뉴스 없음 종료")
            exit(0)
            
        if isinstance(raw_data, list): # 혹시 list로 오면 변환
            news_text = "\n".join([f"Title: {e.title}" for e in raw_data])
        else:
            news_text = raw_data

        # 콘텐츠 생성
        full_text = generate_content(news_text)
        
        # 오디오 생성
        script = full_text.split("라디오 스크립트")[-1].strip() if "라디오 스크립트" in full_text else full_text[:500]
        generate_audio(script)
        
        # 저장
        save_newsletter(full_text)
        
        # URL 생성
        KST = timezone(timedelta(hours=9))
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        web_url = f"https://semiconductortft-bit.github.io/semi-daily-news/newsletter/{date_str}/"

        # 60초 대기 (API 보호)
        print("☕ 60초 휴식...")
        time.sleep(60)

        # 카카오톡 전송 (안전장치 적용됨)
        weather = get_weather_info()
        kakao_msg = generate_kakao_briefing(news_text, weather)
        send_kakao_message(kakao_msg, web_url)

        # 이메일 전송
        send_email(f"📦 [반도체 데일리] {date_str}", full_text.replace("\n", "<br>"), "keenhwi@gmail.com")
        
        print("✅ 모든 공정 완료")
        
    except Exception as e:
        print(f"⚠️ 시스템 치명적 에러: {e}")
