import os
import time
import requests
import json
import feedparser
import urllib.parse
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from google import genai
from collections import defaultdict
from urllib.parse import urlparse
from dateutil import parser as date_parser
from googlenewsdecoder import gnewsdecoder
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# =========================================================
# 0. 로깅 설정
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

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

# Gemini 모델 우선순위 (공통 상수화)
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]

KEYWORDS = [
    'semiconductor', 'advanced packaging', 'hbm', 'tsmc', 'samsung', 'sk hynix',
    'wafer', 'chiplet', 'interposer', 'Hybrid Bonding', 'CoWoS', 'FOWLP', 'intel',
    'Glass Substrate', 'TC-NCF', 'MUF', 'EMC', 'CXL', 'BSPDN', 'Silicon Photonics',
    'Logic Semiconductor', 'Foundry', 'Automotive Chip', 'NVIDIA', 'AMD'
]
# 제외할 키워드 목록
EXCLUDE_KEYWORDS = [
    "부동산", "아파트", "분양", "임대", "전세", "매매",
    "반려동물", "강아지", "고양이", "펫",
    "주식", "코인", "암호화폐", "비트코인",
    "여행", "맛집", "레시피", "요리",
    "패션", "뷰티", "화장품",
    "육아", "임신", "출산",
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

ALL_TARGETS = {**GLOBAL_TARGETS, **KOREA_TARGETS}

# 날씨 코드 → 설명 매핑 (range 반복 생성 제거)
WEATHER_CODE_MAP = {
    0: "맑음 ☀️",
    1: "구름 조금 ⛅", 2: "구름 조금 ⛅", 3: "구름 조금 ⛅",
    45: "안개 🌫️", 48: "안개 🌫️",
}
# 51~69: 비, 70~79: 눈, 80+: 폭우/뇌우
_RAIN_CODES = {c: "비 🌧️" for c in range(51, 70)}
_SNOW_CODES = {c: "눈 ❄️" for c in range(70, 80)}
WEATHER_CODE_MAP.update(_RAIN_CODES)
WEATHER_CODE_MAP.update(_SNOW_CODES)

MAX_ARTICLES = 10

# =========================================================
# 2. 유틸리티 함수
# =========================================================
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
        res = requests.get(weather_url, timeout=10).json()
        current = res.get('current', {})
        temp = current.get('temperature_2m', 0)
        code = current.get('weather_code', 0)

        weather_desc = WEATHER_CODE_MAP.get(code)
        if weather_desc is None:
            weather_desc = "폭우/뇌우 ⛈️" if code >= 80 else "맑음 ☀️"

        weather_str = f"{temp}°C, {weather_desc}"
    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        log.warning(f"날씨 정보 수집 실패: {e}")
        weather_str = "기온 정보 없음"

    # --- 미세먼지 정보 ---
    try:
        aq_url = (
            f"https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={LAT}&longitude={LON}"
            f"&current=pm2_5,pm10"
            f"&timezone=Asia%2FSeoul"
        )
        aq_res = requests.get(aq_url, timeout=10).json()
        aq = aq_res.get('current', {})
        pm25 = aq.get('pm2_5')
        pm10 = aq.get('pm10')

        pm25_label = get_pm_grade(pm25, [15, 35, 75],
                                  ["좋음 💚", "보통 💛", "나쁨 🟠", "매우나쁨 🔴"])
        pm10_label = get_pm_grade(pm10, [30, 80, 150],
                                  ["좋음 💚", "보통 💛", "나쁨 🟠", "매우나쁨 🔴"])

        pm25_str = f"{pm25:.0f}㎍/㎥ {pm25_label}" if pm25 is not None else "정보없음"
        pm10_str = f"{pm10:.0f}㎍/㎥ {pm10_label}" if pm10 is not None else "정보없음"
        dust_str = f"미세먼지(PM10): {pm10_str} | 초미세먼지(PM2.5): {pm25_str}"

    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        log.warning(f"미세먼지 정보 수집 실패: {e}")
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
        res = requests.post(url, data=data, timeout=10)
        res.raise_for_status()
        tokens = res.json()
        return tokens.get("access_token")
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.error(f"카카오 토큰 갱신 실패: {e}")
        return None


def call_gemini(prompt, tag=""):
    """Gemini 모델 fallback 호출 공통 함수. 성공 시 텍스트, 실패 시 None."""
    for model in GEMINI_MODELS:
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            if resp.text:
                log.info(f"[{tag}] {model} 성공")
                return resp.text
        except Exception as e:
            log.warning(f"[{tag}] {model} 실패: {e}")
            time.sleep(1)
    return None


# =========================================================
# 3. 뉴스 수집 및 처리
# =========================================================
def fetch_news():
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    weekday = now_kst.weekday()

    if weekday == 6:  # 일요일
        log.info("📅 일요일은 리포트를 휴간합니다.")
        return None

    is_monday = weekday == 0
    search_period = "7d" if is_monday else "2d"
    cutoff_hours = 168 if is_monday else 48
    cutoff_date = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)

    raw_articles = []

    def get_rss_entries(targets, region, lang):
        site_query = " OR ".join(f"site:{d}" for d in targets)
        kw_query = " OR ".join(KEYWORDS)
        # ✅ 제외 키워드를 -"키워드" 형식으로 결합
        exclude_query = " ".join(f'-"{kw}"' for kw in EXCLUDE_KEYWORDS)
    
        final_query = f"({site_query}) AND ({kw_query}) {exclude_query}"
        encoded_query = urllib.parse.quote(final_query)
        url = (
            f"https://news.google.com/rss/search?q={encoded_query}"
            f"+when:{search_period}&hl={lang}&gl={region}&ceid={region}:{lang}"
        )
        try:
            feed = feedparser.parse(url)
            return feed.entries
        except Exception as e:
            log.warning(f"RSS 파싱 실패 ({region}): {e}")
            return []

    log.info(f"📡 뉴스 수집 중... (기간: {search_period})")
    raw_articles.extend(get_rss_entries(GLOBAL_TARGETS, "US", "en-US"))
    raw_articles.extend(get_rss_entries(KOREA_TARGETS, "KR", "ko"))

    valid_articles = []
    seen_links = set()

    for e in raw_articles:
        link = getattr(e, 'link', None)
        if not link or link in seen_links:
            continue

        # 날짜 필터링
        published = getattr(e, 'published', None)
        if not published:
            continue
        try:
            pub_date = date_parser.parse(published)
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff_date:
                continue
        except (ValueError, OverflowError):
            continue

        # Google News URL 디코딩
        try:
            decoded_res = gnewsdecoder(link)
            if isinstance(decoded_res, dict):
                original_url = decoded_res.get('decoded_url', link)
            else:
                original_url = decoded_res or link
        except Exception:
            original_url = link

        original_url = str(original_url)
        domain = urlparse(original_url).netloc.replace("www.", "")

        source_name = "News"
        for t_domain, t_name in ALL_TARGETS.items():
            if t_domain in domain:
                source_name = t_name
                break

        e['display_source'] = source_name
        e['parsed_date'] = pub_date
        e['clean_url'] = original_url
        valid_articles.append(e)
        seen_links.add(link)

    log.info(f"📰 유효 기사 수집: {len(valid_articles)}개")

    if not valid_articles:
        return None

    # ── 소스별로 묶기 ──
    buckets = defaultdict(list)
    for e in valid_articles:
        buckets[e['display_source']].append(e)

    # 각 소스 내 최신순 정렬
    for src in buckets:
        buckets[src].sort(key=lambda x: x['parsed_date'], reverse=True)

    # ── 라운드-로빈으로 소스 다양성 보장하며 최대 N개 선택 ──
    final_selection = []
    while len(final_selection) < MAX_ARTICLES:
        active_sources = [s for s in buckets if buckets[s]]
        if not active_sources:
            break
        # 매 라운드마다 남은 소스를 순회
        for src in active_sources:
            if len(final_selection) >= MAX_ARTICLES:
                break
            final_selection.append(buckets[src].pop(0))

    final_selection.sort(key=lambda x: x['parsed_date'], reverse=True)
    log.info(f"✅ 최종 선정 기사: {len(final_selection)}개")

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
    log.info("🤖 AI 전체 리포트 작성 중...")
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today_date = now_kst.strftime("%Y년 %m월 %d일")
    publisher = "반도체재료개발TFT 김동휘"

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

    result = call_gemini(prompt, tag="리포트")
    return result or "리포트 생성 실패"


def generate_kakao_briefing(news_text, weather_str, dust_str):
    """카카오톡 브리핑 생성. 날씨 + 미세먼지 + 행복 멘트 포함."""
    log.info("💬 카카오톡 브리핑 생성 시도...")
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today_str = now_kst.strftime("%m-%d")

    article_count = news_text.count("[")
    article_count_str = str(article_count) if article_count > 0 else "10"

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

    (마지막 줄은 절대 작성하지 마세요. 원문 링크는 시스템이 자동으로 붙입니다.)

    [뉴스 데이터]:
    {news_text}
    """

    result = call_gemini(prompt, tag="카카오 브리핑")
    if result:
        return result

    # ── Fallback: AI 실패 시 수동 조합 ──
    log.warning("AI 브리핑 전체 실패 → fallback 메시지 생성")
    titles = [
        line.replace("Title:", "").strip()
        for line in news_text.split('\n')
        if line.strip().startswith("Title:")
    ]

    lines = [
        f"🌤️ {weather_str}",
        f"🍃 {dust_str}",
        "",
        "오늘도 건강하고 활기차게! 좋은 하루 되세요 😊",
        "---",
        f"🚀 오늘의 반도체 헤드라인 ({today_str})",
        "",
        "(AI 서비스 지연으로 제목만 전송합니다)",
    ]
    for i, t in enumerate(titles[:MAX_ARTICLES]):
        lines.append(f"{i+1}. {t}")

    return "\n".join(lines)


# =========================================================
# 5. 스타일 강제 오버라이딩 함수 (GitHub Pages)
# =========================================================
def apply_custom_css():
    css_path = "assets/css"
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
    log.info("✅ style.scss 생성 완료")


def create_config_file():
    config_content = """title: ""
description: ""
show_downloads: false
theme: minima
header_pages: []
"""
    with open("_config.yml", "w", encoding="utf-8") as f:
        f.write(config_content)
    log.info("✅ _config.yml 생성 완료")


def create_custom_layout():
    layout_path = "_layouts"
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
    log.info("✅ 커스텀 레이아웃 생성 완료")


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
    os.makedirs(folder, exist_ok=True)

    with open(f"{folder}/index.md", "w", encoding="utf-8") as f:
        f.write(final_content)

    with open("index.md", "w", encoding="utf-8") as f:
        f.write(final_content)

    log.info(f"✅ 리포트 저장 완료: {folder}/index.md")


def send_kakao_message(briefing_text, report_url):
    access_token = get_new_kakao_token()
    if not access_token:
        log.error("❌ 카카오 토큰 갱신 실패")
        return

    # 원본 URL 직접 사용 (TinyURL Preview 대기 페이지 방지)
    footer = f"\n\n---\n📌 원문 링크는 아래 버튼을 눌러 리포트를 확인해주세요.\n🔗 {report_url}"
    suffix = "\n...(더보기)"

    MAX_LEN = 950
    footer_len = len(footer)
    max_body = MAX_LEN - footer_len - len(suffix)

    if len(briefing_text) > max_body:
        final_text = briefing_text[:max_body] + suffix + footer
    else:
        final_text = briefing_text + footer

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

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    try:
        res = requests.post(url, headers=headers,
                            data={"template_object": json.dumps(template)},
                            timeout=10)
        if res.status_code == 200:
            log.info("✅ 카카오톡 전송 성공")
        else:
            log.error(f"❌ 카카오톡 전송 실패 ({res.status_code}): {res.text}")
    except requests.RequestException as e:
        log.error(f"❌ 카카오톡 전송 에러: {e}")


def send_email(subject, body_md, to_email):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log.warning("⚠️ 이메일 설정 누락으로 전송 건너뜀")
        return

    # 마크다운 → 간단 HTML 변환 (줄바꿈 + 기본 래핑)
    html_body = (
        "<html><body style='font-family:sans-serif; line-height:1.6;'>"
        + body_md.replace("\n", "<br>")
        + "</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg['From'] = GMAIL_USER
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body_md, 'plain', 'utf-8'))   # 플레인 텍스트 fallback
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))   # HTML 본문

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)
        log.info("📧 이메일 전송 성공")
    except Exception as e:
        log.error(f"❌ 이메일 실패: {e}")


# =========================================================
# 7. 메인 실행 블록
# =========================================================
def main():
    log.info("🚀 뉴스 큐레이션 공정 시작")

    # GitHub Pages 스타일 파일 생성
    apply_custom_css()
    create_config_file()
    create_custom_layout()

    # 뉴스 수집
    news_text = fetch_news()
    if not news_text:
        log.info("뉴스 없음 또는 휴간일 → 종료")
        return

    # AI 리포트 생성
    full_text = generate_content(news_text)
    if full_text == "리포트 생성 실패":
        log.error("❌ 리포트 생성 실패 - 종료")
        raise SystemExit(1)

    save_newsletter(full_text)

    KST = timezone(timedelta(hours=9))
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    web_url = "https://semiconductortft-bit.github.io/semi-daily-news/"

    # API Rate Limit 보호 대기
    log.info("☕ API 보호 대기 (60초)...")
    time.sleep(60)

    # 날씨 + 미세먼지 정보 수집
    weather_str, dust_str = get_weather_info()
    log.info(f"🌤️ {weather_str} | {dust_str}")

    # 카카오톡 전송
    kakao_msg = generate_kakao_briefing(news_text, weather_str, dust_str)
    send_kakao_message(kakao_msg, web_url)

    # 이메일 전송
    send_email(
        f"📦 [반도체 뉴스] {date_str}",
        full_text,
        "keenhwi@gmail.com"
    )

    log.info("✅ 모든 공정 완료")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log.critical(f"⚠️ 시스템 치명적 에러: {e}", exc_info=True)
        raise SystemExit(1)
