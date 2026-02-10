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

def get_pm_grade(value, thresholds, labels):
    """PM 수치에 따른 등급 문자열 반환"""
    if value is None:
        return "정보없음"
    for threshold, label in zip(thresholds, labels):
        if value <= threshold:
            return label
    return labels[-1]

def get_weather_info():
    """날씨 + 미세먼지(PM2.5/PM10) 정보를 함께 반환합니다. (튜플 반환)"""
    LAT, LON = 36.99, 127.11  # 아산/천안 기준

    # --- 날씨 정보 ---
    try:
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={LAT}&longitude={LON}"
            f"&current=temperature_2m,weather_code"
            f"&timezone=Asia%2FSeoul"
        )
        res = requests.get(weather_url, timeout=5).json()
        current = res.get('current', {})
        temp = current.get('temperature_2m', 0)
        code = current.get('weather_code', 0)

        weather_desc = "맑음 ☀️"
        if code in [1, 2, 3]:    weather_desc = "구름 조금 ⛅"
        elif code in [45, 48]:   weather_desc = "안개 🌫️"
        elif code in range(51, 70): weather_desc = "비 🌧️"
        elif code in range(70, 80): weather_desc = "눈 ❄️"
        elif code >= 80:          weather_desc = "폭우/뇌우 ⛈️"

        weather_str = f"{temp}°C, {weather_desc}"
    except Exception:
        weather_str = "기온 정보 없음"

    # --- 미세먼지 정보 (Open-Meteo Air Quality API - 무료, 키 불필요) ---
    try:
        aq_url = (
            f"https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={LAT}&longitude={LON}"
            f"&current=pm2_5,pm10"
            f"&timezone=Asia%2FSeoul"
        )
        aq_res = requests.get(aq_url, timeout=5).json()
        aq = aq_res.get('current', {})
        pm25 = aq.get('pm2_5')
        pm10 = aq.get('pm10')

        # 한국 환경부 기준 PM2.5
        pm25_label = get_pm_grade(
            pm25,
            [15, 35, 75],
            ["좋음 💚", "보통 💛", "나쁨 🟠", "매우나쁨 🔴"]
        )
        # 한국 환경부 기준 PM10
        pm10_label = get_pm_grade(
            pm10,
            [30, 80, 150],
            ["좋음 💚", "보통 💛", "나쁨 🟠", "매우나쁨 🔴"]
        )

        pm25_str = f"{pm25:.0f}㎍/㎥ {pm25_label}" if pm25 is not None else "정보없음"
        pm10_str = f"{pm10:.0f}㎍/㎥ {pm10_label}" if pm10 is not None else "정보없음"
        dust_str = f"미세먼지(PM10): {pm10_str} | 초미세먼지(PM2.5): {pm25_str}"

    except Exception:
        dust_str = "미세먼지 정보 없음"

    return weather_str, dust_str

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
    except:
        return None

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
        url = (
            f"https://news.google.com/rss/search?q={encoded_query}"
            f"+when:{search_period}&hl={lang}&gl={region}&ceid={region}:{lang}"
        )
        return feedparser.parse(url).entries

    print(f"📡 뉴스 수집 중... (기간: {search_period})")
    raw_articles.extend(get_rss_entries(GLOBAL_TARGETS, "US", "en-US"))
    raw_articles.extend(get_rss_entries(KOREA_TARGETS, "KR", "ko"))

    valid_articles = []
    seen_links = set()

    for e in raw_articles:
        if e.link in seen_links:
            continue
        try:
            pub_date = date_parser.parse(e.published)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff_date:
                continue
        except:
            continue

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

    print(f"📰 유효 기사 수집: {len(valid_articles)}개")

    # ── 소스별로 묶기 ──
    buckets = defaultdict(list)
    for e in valid_articles:
        buckets[e['display_source']].append(e)

    sources = list(buckets.keys())
    if not sources:
        return "최근 관련 뉴스가 없습니다."

    # ── 소스 다양성을 유지하며 최대 10개 선택 (라운드-로빈) ──
    # 버그 수정: sources 리스트에서 비어있는 소스를 건너뛰도록 개선
    final_selection = []
    idx = 0
    while len(final_selection) < 10:
        # 남은 기사가 있는 소스만 추려냄
        active_sources = [s for s in sources if buckets[s]]
        if not active_sources:
            break
        src = active_sources[idx % len(active_sources)]
        final_selection.append(buckets[src].pop(0))
        idx += 1

    final_selection.sort(key=lambda x: x['parsed_date'], reverse=True)

    print(f"✅ 최종 선정 기사: {len(final_selection)}개")

    formatted_text = []
    for i, e in enumerate(final_selection):
        item = (
            f"[{i+1}] Source: {e['display_source']}\n"
            f"Title: {e.title}\n"
            f"URL: {e['clean_url']}\n"
        )
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

    # 뉴스 기사 수 파악 (프롬프트에 명시)
    article_count = news_text.count("[")
    article_count_str = f"정확히 {article_count}개" if article_count > 0 else "10개"

    prompt = f"""
    당신은 반도체 소재 개발 엔지니어이자 산업 분석가입니다.
    저작권법 준수를 위해 기사 내용을 요약하거나 재생산하지 마십시오.
    오직 기사의 '제목', '카테고리(키워드)', '출처'만 정리하여 독자가 원문을 방문하도록 유도해야 합니다.

    [작성 규칙]
    1. 기사 내용 요약 금지 (제목과 링크만 제공).
    2. Executive Summary는 전체 뉴스 제목들을 보고 느껴지는 '오늘의 반도체 키워드 및 분위기'만 3줄로 작성.
    3. Packaging Material Insight는 '반도체 후공정 소재(EMC, Underfill, Paste, Film 등)' 개발자 관점에서 오늘의 뉴스들이 소재 기술에 미칠 영향이나 중요성을 1문장으로 작성.
    4. 🌍 Headlines & Links 섹션에는 [뉴스 데이터]에 있는 모든 기사를 빠짐없이 나열해야 합니다. ({article_count_str} 전부 포함, 단 하나도 생략 금지)

    [필수 형식 - 마크다운]
    ##### {today_date} | 발행인: {publisher}

    💡 **Today's Market Mood**
    (전체적인 시장 기술 트렌드나 분위기만 3줄 작성 - 개별 기사 언급 금지)

    🌍 **Headlines & Links**
    (아래 뉴스 데이터의 모든 기사를 번호 순서대로 빠짐없이 작성 - 생략 절대 금지)
    1. **[기사 제목 그대로 작성]**
       - 🏷️ 태그: [관련 기술/기업 태그]
       - 🔗 원문: [[언론사명](URL)] (반드시 원문 링크 적용)
    2. ...
    (데이터에 있는 모든 기사 번호까지 반복)

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
            if resp.text:
                return resp.text
        except:
            continue
    return "리포트 생성 실패"

def generate_kakao_briefing(news_text, weather_str, dust_str):
    """카카오톡 브리핑 생성. 날씨 + 미세먼지 + 행복 멘트 포함."""
    print("💬 카카오톡 브리핑 생성 시도...")
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%m-%d")

    # 뉴스 기사 수 파악
    article_count = news_text.count("[")
    article_count_str = str(article_count) if article_count > 0 else "10"

    models = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]

    prompt = f"""
    당신은 따뜻하고 활기찬 테크 뉴스 알리미입니다.
    저작권 보호를 위해 기사 내용을 요약하지 말고, 헤드라인 리스트만 작성하세요.
    길이는 공백 포함 900자 이내.

    [오늘의 날씨 및 미세먼지 정보]
    - 날씨: {weather_str}
    - {dust_str}

    [형식 - 반드시 아래 형식을 그대로 따르세요]

    (첫 줄) 날씨 이모지 + 날씨 정보 한 줄 표기 (예: ☀️ 맑음, 기온 등 포함)
    (둘째 줄) 미세먼지 정보 한 줄 표기 (PM10 등급과 PM2.5 등급을 이모지와 함께)
    (셋째 줄) 빈 줄
    (넷째 줄) 날씨와 미세먼지 상태에 맞는 따뜻하고 행복을 비는 기분 좋은 인사말 1~2문장.
    (예: 미세먼지가 좋은 날이면 "오늘은 바깥 공기도 맑으니 잠깐 산책도 어떨까요? 활기찬 하루 되세요! 😊")
    (예: 미세먼지가 나쁜 날이면 "오늘은 마스크 꼭 챙기세요! 건강하고 행복한 하루 보내시길 바랍니다 💪")
    ---
    🚀 오늘의 반도체 헤드라인 ({today_str})

    (뉴스 데이터에 있는 기사 제목을 {article_count_str}개 전부 나열 - 생략 없이)
    1. (제목) - (매체명)
    2. (제목) - (매체명)
    ...
    {article_count_str}. (제목) - (매체명)

    ---
    📌 원문 링크는 아래 버튼을 눌러 리포트를 확인해주세요.

    [뉴스 데이터]:
    {news_text}
    """

    for model_name in models:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            if response.text:
                return response.text
        except Exception:
            time.sleep(1)
            continue

    # ── Fallback: AI 실패 시 수동 조합 ──
    titles = []
    for line in news_text.split('\n'):
        if line.startswith("Title:"):
            titles.append(line.replace("Title:", "").strip())

    fallback_msg = (
        f"🌤️ {weather_str}\n"
        f"🍃 {dust_str}\n\n"
        f"오늘도 건강하고 활기차게! 좋은 하루 되세요 😊\n"
        f"---\n"
        f"🚀 오늘의 반도체 헤드라인 ({today_str})\n\n"
        f"(AI 서비스 지연으로 제목만 전송합니다)\n"
    )
    for i, t in enumerate(titles[:10]):
        fallback_msg += f"{i+1}. {t}\n"
    fallback_msg += "\n---\n📌 상세 내용은 리포트를 확인해주세요."
    return fallback_msg

# =========================================================
# 5. 스타일 강제 오버라이딩 함수 (핵심)
# =========================================================
def apply_custom_css():
    css_path = "assets/css"
    if not os.path.exists(css_path):
        os.makedirs(css_path, exist_ok=True)
    
    css_content = """---
---
@import "minima";

.site-header, 
header, 
.site-title, 
.project-name,
.page-header,
.site-nav,
a.site-title,
.site-header .wrapper { 
    display: none !important; 
    visibility: hidden !important;
    opacity: 0 !important;
    height: 0 !important;
    overflow: hidden !important;
    pointer-events: none !important;
    margin: 0 !important;
    padding: 0 !important;
}

body, .page-content, .markdown-body, main {
    margin-top: 0 !important;
    padding-top: 10px !important;
}

.wrapper {
    margin-top: 0 !important;
}
"""
    with open(f"{css_path}/style.scss", "w", encoding="utf-8") as f:
        f.write(css_content)
    print("✅ 강력한 스타일 제거 파일(assets/css/style.scss) 생성 완료")

def create_config_file():
    config_content = """title: ""
description: ""
show_downloads: false
theme: minima
header_pages: []
"""
    with open("_config.yml", "w", encoding="utf-8") as f:
        f.write(config_content)
    print("✅ _config.yml 생성 완료 (사이트 제목 제거)")

def create_custom_layout():
    layout_path = "_layouts"
    if not os.path.exists(layout_path):
        os.makedirs(layout_path, exist_ok=True)
    
    layout_content = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ page.title | default: site.title }}</title>
  <link rel="stylesheet" href="{{ '/assets/css/style.css?v=' | append: site.github.build_revision | relative_url }}">
</head>
<body>
  <main class="page-content" aria-label="Content">
    <div class="wrapper">
      {{ content }}
    </div>
  </main>
</body>
</html>
"""
    with open(f"{layout_path}/default.html", "w", encoding="utf-8") as f:
        f.write(layout_content)
    print("✅ 커스텀 레이아웃(_layouts/default.html) 생성 완료")

# =========================================================
# 6. 전송 및 저장
# =========================================================
def save_newsletter(content):
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")
    
    report_title = "Semi-TFT Weekly News" if now.weekday() == 0 else "Semi-TFT Daily News"
    
    inline_css = """
<style>
.site-header, .site-title { display: none !important; }
</style>
"""
    front_matter = f"""---
layout: default
title: "{report_title} ({date_str})"
---
{inline_css}

# 📦 {report_title}
"""
    final_content = front_matter + content

    folder = f"newsletter/{date_str}"
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)

    with open(f"{folder}/index.md", "w", encoding="utf-8") as f:
        f.write(final_content)

    with open("index.md", "w", encoding="utf-8") as f:
        f.write(final_content)

    print(f"✅ 리포트 저장 완료: {folder}/index.md")

def send_kakao_message(briefing_text, report_url):
    access_token = get_new_kakao_token()
    if not access_token:
        print("❌ 카카오 토큰 갱신 실패")
        return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        short_url = shorten_url(report_url)
    except:
        short_url = report_url

    header = "📦 김동휘입니다."
    footer = f"\n\n🔗 {short_url}"
    suffix = "\n...(더보기)"

    MAX_LEN = 950
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
                "title": "📰 전체 리포트 보기",
                "link": {"web_url": report_url, "mobile_web_url": report_url}
            }
        ]
    }

    try:
        res = requests.post(url, headers=headers, data={"template_object": json.dumps(template)})
        if res.status_code == 200:
            print("✅ 카카오톡 전송 성공")
        else:
            print(f"❌ 카카오톡 전송 실패: {res.text}")
    except Exception as e:
        print(f"❌ 카카오톡 전송 에러: {e}")

def shorten_url(long_url):
    try:
        api_url = f"https://tinyurl.com/api-create.php?url={urllib.parse.quote(long_url)}"
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            return response.text
    except:
        pass
    return long_url

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
    except Exception as e:
        print(f"❌ 이메일 실패: {e}")

# =========================================================
# 7. 메인 실행 블록
# =========================================================
if __name__ == "__main__":
    try:
        print("🚀 뉴스 큐레이션 공정 시작")

        apply_custom_css()
        create_config_file()
        create_custom_layout()

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

        if not full_text or full_text == "리포트 생성 실패":
            print("❌ 리포트 생성 실패 - 종료")
            exit(1)

        save_newsletter(full_text)

        KST = timezone(timedelta(hours=9))
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        web_url = "https://semiconductortft-bit.github.io/semi-daily-news/"

        print("☕ API 보호 대기 (60초)...")
        time.sleep(60)

        # ── 날씨 + 미세먼지 정보 수집 (튜플 언패킹) ──
        weather_str, dust_str = get_weather_info()
        print(f"🌤️ {weather_str} | {dust_str}")

        kakao_msg = generate_kakao_briefing(news_text, weather_str, dust_str)
        send_kakao_message(kakao_msg, web_url)

        send_email(f"📦 [반도체 뉴스] {date_str}", full_text.replace("\n", "<br>"), "keenhwi@gmail.com")

        print("✅ 모든 공정 완료")

    except Exception as e:
        print(f"⚠️ 시스템 치명적 에러: {e}")
        import traceback
        traceback.print_exc()
