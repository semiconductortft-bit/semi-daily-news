import os
import time
import requests
import json
import feedparser
import urllib.parse
import smtplib
from datetime import datetime, timedelta, timezone
from google import genai
from collections import defaultdict
from urllib.parse import urlparse
from dateutil import parser as date_parser
from googlenewsdecoder import gnewsdecoder
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# =========================================================
# 1. 환경 설정 및 상수 정의
# =========================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
KAKAO_REFRESH_TOKEN = os.getenv("KAKAO_REFRESH_TOKEN")
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

client = genai.Client(api_key=GEMINI_API_KEY)

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

# =========================================================
# 2. 유틸리티 함수
# =========================================================
def parse_date(date_str):
    try:
        return date_parser.parse(date_str)
    except:
        return datetime.now()

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

# =========================================================
# 3. 뉴스 수집 및 처리
# =========================================================
def fetch_news():
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    weekday = now_kst.weekday()

    if weekday == 6:
        print("📅 일요일은 리포트를 휴간합니다.")
        return None

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
        item = f"[{i+1}] Source: {e['display_source']}\nTitle: {e.title}\nURL: {e['clean_url']}\n"
        formatted_text.append(item)
    
    return "\n".join(formatted_text)

# =========================================================
# 4. 콘텐츠 생성 (Gemini)
# =========================================================
def generate_content(news_text):
    print("🤖 AI 전체 리포트 작성 중... (Safe Mode + Material Insight)")
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today_date = now_kst.strftime("%Y년 %m월 %d일")
    publisher = "반도체재료개발TFT 김동휘"
    
    report_title = "Semi-TFT Weekly News" if now_kst.weekday() == 0 else "Semi-TFT Daily News"

    # 상단 파란색 사이트 제목 제거를 위해 본문에서 H1(#) 태그 제거
    prompt = f"""
    당신은 반도체 소재 개발 엔지니어이자 산업 분석가입니다.
    저작권법 준수를 위해 기사 내용을 요약하거나 재생산하지 마십시오.
    오직 기사의 '제목', '카테고리(키워드)', '출처'만 정리하여 독자가 원문을 방문하도록 유도해야 합니다.

    [작성 규칙]
    1. 기사 내용 요약 금지 (제목과 링크만 제공).
    2. Executive Summary는 전체 뉴스 제목들을 보고 느껴지는 '오늘의 반도체 키워드 및 분위기'만 3줄로 작성.
    3. Packaging Material Insight는 '반도체 후공정 소재(EMC, Underfill, Paste, Film 등)' 개발자 관점에서 오늘의 뉴스들이 소재 기술에 미칠 영향이나 중요성을 1문장으로 작성.

    [필수 형식 - 마크다운]
    ##### {today_date} | 발행인: {publisher}

    💡 **Today's Market Mood**
    (전체적인 시장 기술 트렌드나 분위기만 3줄 작성 - 개별 기사 언급 금지)

    🌍 **Headlines & Links**
    (뉴스 10개 작성)
    1. **[기사 제목 그대로 작성]**
       - 🏷️ 태그: [관련 기술/기업 태그]
       - 🔗 원문: [[언론사명](URL)] (반드시 원문 링크 적용)

    📚 **Word of the Day**
    (제목에 등장한 기술 용어 중 1개 선정하여 1줄 정의)

    🧪 **Packaging Material Insight**
    (오늘의 뉴스 흐름이 반도체 패키징 소재 개발에 주는 시사점 1문장)

    (줄바꿈)
    ---
    *본 리포트는 뉴스 링크를 모아 제공하며, 기사의 저작권은 각 언론사에 있습니다. 상세 내용은 반드시 원문 링크를 확인하시기 바랍니다.*
    ⓒ 2026 {publisher}.

    [뉴스 데이터]:
    {news_text}
    """
    
    models = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
    for m in models:
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
            if resp.text: return resp.text
        except: continue
    return "리포트 생성 실패"

def generate_kakao_briefing(news_text, weather_str):
    print("💬 카카오톡 브리핑 생성 시도...")
    KST = timezone(timedelta(hours=9))
    today_str = datetime.now(KST).strftime("%m-%d")

    models = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]

    prompt = f"""
    당신은 테크 뉴스 알리미입니다.
    저작권 보호를 위해 기사 내용을 요약하지 말고, 헤드라인 리스트만 작성하세요.
    길이는 공백 포함 900자 이내.

    [형식]
    ❄️ (날씨/기온 + 짧은 인사)
    ---
    🚀 오늘의 반도체 헤드라인 ({today_str})
    
    (뉴스 제목들만 나열)
    1. (제목) - (매체명)
    2. (제목) - (매체명)
    ...
    
    ---
    📌 원문 링크는 아래 버튼을 눌러 리포트를 확인해주세요.

    [데이터]:
    {news_text}
    """

    for model_name in models:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            if response.text: return response.text
        except Exception:
            time.sleep(1)
            continue

    titles = []
    for line in news_text.split('\n'):
        if line.startswith("Title:"):
            titles.append(line.replace("Title:", "").strip())
    
    fallback_msg = f"""❄️ {weather_str}, 좋은 아침입니다!

    ---
    🚀 오늘의 반도체 헤드라인 ({today_str})

    (AI 서비스 지연으로 제목만 전송합니다)"""

    for i, t in enumerate(titles[:10]):
        fallback_msg += f"\n{i+1}. {t}"

    fallback_msg += f"\n\n---\n📌 상세 내용은 리포트를 확인해주세요."
    return fallback_msg

# =========================================================
# 5. 전송 및 저장 - [강력 수정: 파란 글씨 강제 삭제 CSS]
# =========================================================
def save_newsletter(content):
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")
    
    report_title = "Semi-TFT Weekly News" if now.weekday() == 0 else "Semi-TFT Daily News"
    
    # [핵심] CSS를 주입하여 GitHub Pages의 기본 사이트 제목(.site-title)을 안 보이게 처리
    hide_header_css = """
<style>
/* GitHub Pages 기본 테마의 헤더(파란 글씨) 숨기기 */
.site-title, .site-header { display: none !important; }
/* 헤더가 사라져서 너무 붙는 것을 방지 */
body { margin-top: 30px !important; }
</style>
"""

    front_matter = f"""---
layout: default
title: "{report_title} ({date_str})"
---
{hide_header_css}

# 📦 {report_title}
"""
    # Front Matter + CSS + 본문 결합
    final_content = front_matter + content

    folder = f"newsletter/{date_str}"
    if not os.path.exists(folder): os.makedirs(folder, exist_ok=True)

    with open(f"{folder}/index.md", "w", encoding="utf-8") as f: f.write(final_content)
    with open("index.md", "w", encoding="utf-8") as f: f.write(final_content)

def send_kakao_message(briefing_text, report_url):
    access_token = get_new_kakao_token()
    if not access_token: return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    header = "안녕하세요. 김동휘입니다."
    footer = f"\n\n🔗 원문 링크 모음 : {report_url}"
    suffix = "\n...(더 보기)"

    MAX_LEN = 1000
    fixed_len = len(header) + len("\n\n") + len(footer)
    max_body = MAX_LEN - fixed_len - len(suffix)

    if len(briefing_text) > max_body:
        safe_text = briefing_text[:max_body] + suffix
    else:
        safe_text = briefing_text

    final_text = f"{header}\n\n{safe_text}{footer}"

    template = {
        "object_type": "text",
        "text": final_text,
        "link": {"web_url": report_url, "mobile_web_url": report_url},
        "buttons": [
            {
                "title": "뉴스 큐레이션 보기 🔗",
                "link": {"web_url": report_url, "mobile_web_url": report_url}
            }
        ]
    }

    try:
        res = requests.post(url, headers=headers, data={"template_object": json.dumps(template)})
        if res.status_code == 200: print("✅ 카카오톡 전송 성공")
        else: print(f"❌ 전송 실패: {res.text}")
    except Exception as e: print(f"❌ 전송 에러: {e}")

def send_email(subject, body, to_email):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("⚠️ 이메일 설정 누락으로 전송 건너뜀")
        return

    msg = MIMEMultipart()
    msg['From'] = GMAIL_USER
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    try:
        s = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        s.send_message(msg)
        s.quit()
        print("📧 이메일 전송 성공")
    except Exception as e: print(f"❌ 이메일 실패: {e}")

# =========================================================
# 6. 메인 실행 블록
# =========================================================
if __name__ == "__main__":
    try:
        print("🚀 뉴스 큐레이션 공정 시작")
        raw_data = fetch_news()
        
        if not raw_data: 
            print("뉴스 없음 종료")
            exit(0)
            
        if isinstance(raw_data, list):
            news_text = "\n".join([f"Title: {e.title}" for e in raw_data])
        else:
            news_text = raw_data

        # AI 리포트 생성
        full_text = generate_content(news_text)
        
        # 저장 (CSS 주입됨)
        save_newsletter(full_text)
        
        KST = timezone(timedelta(hours=9))
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        web_url = f"https://semiconductortft-bit.github.io/semi-daily-news/newsletter/{date_str}/"

        print("☕ API 보호 대기 (60초)...")
        time.sleep(60)

        weather = get_weather_info()
        kakao_msg = generate_kakao_briefing(news_text, weather)
        send_kakao_message(kakao_msg, web_url)

        send_email(f"📦 [반도체 뉴스] {date_str}", full_text.replace("\n", "<br>"), "keenhwi@gmail.com")
        
        print("✅ 모든 공정 완료")
        
    except Exception as e:
        print(f"⚠️ 시스템 치명적 에러: {e}")
