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
# 5. 스타일 강제 오버라이딩 함수 (핵심)
# =========================================================
def apply_custom_css():
    """
    GitHub Pages의 기본 테마 CSS보다 우선 적용되는 커스텀 스타일 파일을 생성합니다.
    이 함수는 'assets/css/style.scss' 파일을 생성하여 헤더를 물리적으로 숨깁니다.
    """
    css_path = "assets/css"
    if not os.path.exists(css_path):
        os.makedirs(css_path, exist_ok=True)
    
    # Minima 테마의 헤더(.site-header)를 강제로 숨기는 SCSS 코드
    # YAML Front Matter (---)를 포함해야 Jekyll이 처리합니다.
    css_content = """---
---
@import "minima";

/* 헤더 강제 삭제 구문 - 모든 가능한 선택자 포함 */
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

/* 헤더 삭제 후 상단 여백 제거 */
body, .page-content, .markdown-body, main {
    margin-top: 0 !important;
    padding-top: 10px !important;
}

/* 추가: 전체 페이지 상단 여백 제거 */
.wrapper {
    margin-top: 0 !important;
}
"""
    with open(f"{css_path}/style.scss", "w", encoding="utf-8") as f:
        f.write(css_content)
    print("✅ 강력한 스타일 제거 파일(assets/css/style.scss) 생성 완료")

def create_config_file():
    """
    _config.yml 파일을 생성하여 사이트 제목을 빈 값으로 설정합니다.
    """
    config_content = """title: ""
description: ""
show_downloads: false
theme: minima

# 헤더 완전 비활성화
header_pages: []
"""
    with open("_config.yml", "w", encoding="utf-8") as f:
        f.write(config_content)
    print("✅ _config.yml 생성 완료 (사이트 제목 제거)")

def create_custom_layout():
    """
    커스텀 레이아웃 파일을 생성하여 헤더를 물리적으로 제거합니다.
    """
    layout_path = "_layouts"
    if not os.path.exists(layout_path):
        os.makedirs(layout_path, exist_ok=True)
    
    # 헤더가 없는 minimal한 레이아웃
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
    
    # 안전장치: Markdown 파일 내에도 CSS 주입 (이중 잠금)
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

    # 날짜별 폴더에 index.md 저장
    folder = f"newsletter/{date_str}"
    if not os.path.exists(folder): 
        os.makedirs(folder, exist_ok=True)

    with open(f"{folder}/index.md", "w", encoding="utf-8") as f: 
        f.write(final_content)
    
    # 루트 index.md도 동일하게 업데이트
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

    # 짧은 URL로 변경 (bit.ly 사용)
    try:
        short_url = shorten_url(report_url)
    except:
        short_url = report_url

    header = "📦 김동휘입니다."
    footer = f"\n\n🔗 {short_url}"
    suffix = "\n...(더보기)"

    MAX_LEN = 950  # 여유 확보
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
    """bit.ly API를 사용하여 URL 단축"""
    try:
        # bit.ly 무료 API (인증 없이 사용 가능한 대안)
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
        
        # [중요] 실행 시 스타일 강제 덮어쓰기 수행
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
        
        # 리포트가 제대로 생성되었는지 확인
        if not full_text or full_text == "리포트 생성 실패":
            print("❌ 리포트 생성 실패 - 종료")
            exit(1)
        
        # 저장
        save_newsletter(full_text)
        
        KST = timezone(timedelta(hours=9))
        date_str = datetime.now(KST).strftime("%Y-%m-%d")
        
        # 루트 URL로 간단하게 변경
        web_url = f"https://semiconductortft-bit.github.io/semi-daily-news/"

        print("☕ API 보호 대기 (60초)...")
        time.sleep(60)

        weather = get_weather_info()
        kakao_msg = generate_kakao_briefing(news_text, weather)
        send_kakao_message(kakao_msg, web_url)

        send_email(f"📦 [반도체 뉴스] {date_str}", full_text.replace("\n", "<br>"), "keenhwi@gmail.com")
        
        print("✅ 모든 공정 완료")
        
    except Exception as e:
        print(f"⚠️ 시스템 치명적 에러: {e}")
        import traceback
        traceback.print_exc()
