import os
import time
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
    'wafer', 'chiplet', 'interposer', 'Hybrid Bonding', 'CoWoS', 'FOWLP', 
    'Glass Substrate', 'TC-NCF', 'MUF', 'EMC', 'CXL', 'BSPDN', 'Silicon Photonics'
]

GLOBAL_TARGETS = {
    "semiengineering.com": "Semiconductor Engineering",
    "3dincites.com": "3D InCites",
    "digitimes.com": "Digitimes",
    "eetimes.com": "EE Times",
    "trendforce.com": "TrendForce",
    "semiconductor-digest.com": "Semi Digest",
    "yolegroup.com": "Yole Group",
    "kipost.net": "KIPOST"
}

KOREA_TARGETS = {
    "thelec.kr": "TheElec",
    "etnews.com": "ETNews",
    "zdnet.co.kr": "ZDNet Korea",
    "hankyung.com": "Hankyung Insight"
}

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
    
    **1. 헤더 (Header)**
    - `# 📦 오늘의 반도체 뉴스` (제목)
    - `##### {today_date} | 발행인: 반도체재료개발TFT 김동휘` (날짜 및 발행인, 작게)
    - 구분선(`---`) 삽입   
    
    **2. Executive Summary (요약)**
    - `### 💡 Executive Summary`
    - 전체 시장 흐름을 5줄 이내로 요약하세요. 핵심 키워드는 **볼드체**로 강조하세요.
    
    **3. Key Insights (핵심 뉴스)**
    - `### 🌍 Market & Tech Insights`
    - 수집된 데이터 중 가장 중요한 뉴스를 꼭 10개 선정해주세요. "관련 뉴스 없음"과 같은 불필요한 문구는 절대 포함하지 마십시오.
    - **각 항목 작성 포맷 (엄수)**:
        **|기업 또는 업체명|뉴스 제목**
          뉴스를 3문장으로 요약하세요. *[출처: 언론사명]* (기울임꼴) 써주세요.
          항목 간 한 줄 띄웁니다.
    
    **4. Technical Term (용어 해설)**
    - `### 📚 Technical Term`
    - **[용어명 (한글/영어)]**
    - Technical Term: 'BSPDN', 'Glass Substrate', 'Hybrid Bonding' 등 반도체 전문가 수준의 심도 있는 기술 용어 1개를 선정해 상세히 설명하세요.

    **5. Footer (저작권 및 보안 경고)**
    - 리포트 맨 마지막에 반드시 다음 문구를 볼드체로 포함하세요:
    `ⓒ 2026 {publisher}. All rights reserved.`
    `[보안 경고] 본 리포트는 사내 보안 자료입니다. 무단 전재, 복사, 외부 배포를 엄격히 금지합니다.`
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

if __name__ == "__main__":
    # 이 부분을 추가하여 프로그램 전체에서 사용할 한국 날짜를 고정합니다.
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    date_str = now_kst.strftime("%Y-%m-%d")
    print("🚀 반도체 리포트 생산 공정 개시\n")
    try:
        raw_data = fetch_news()
        
        # 일요일이거나 데이터가 없는 경우 종료
        if raw_data is None:
            print("🛑 발행 조건 미충족(일요일 등)으로 공정을 종료합니다.")
            exit(0) 

        # 월요일 주간 뉴스 대응을 위한 데이터 포맷팅
        if isinstance(raw_data, list):
            # 뉴스 개수가 10개보다 많을 수 있으므로 최종 선별된 리스트 처리
            formatted_news = []
            for i, e in enumerate(raw_data[:10]): # 최대 10개 제한
                clean_summ = e.summary.replace("<b>", "").replace("</b>", "") if hasattr(e, 'summary') else ""
                item = (
                    f"[{i+1}] Source: {e['display_source']}\n"
                    f"Date: {e['parsed_date'].strftime('%Y-%m-%d %H:%M')}\n"
                    f"Title: {e.title}\n"
                    f"URL: {e.link}\n"
                    f"Summary: {clean_summ[:300]}\n"
                )
                formatted_news.append(item)
            news_text = "\n".join(formatted_news)
        else:
            news_text = raw_data

        # AI 컨텐츠 생성 및 이후 공정 진행
        full_text = generate_content(news_text)
        
        print(f"✅ {len(full_text)} 바이트의 컨텐츠 생성 완료")
        
        # 라디오 스크립트 추출
        if "라디오 스크립트" in full_text:
            script = full_text.split("라디오 스크립트")[-1].strip()
            print(f"✅ 라디오 스크립트 추출 완료 ({len(script)} 문자)")
        else:
            script = full_text[:500]
            print(f"⚠️ '라디오 스크립트' 섹션을 찾지 못해 처음 500자 사용")
        
        print("\n🎙️ AI 라디오 음성 합성 중...")
        generate_audio(script)
        
        print("\n📝 뉴스레터 마크다운 생성 중...")
        save_newsletter(full_text)

        # --- [추가] 이메일 발송 단계 ---
        print("\n📧 이메일 발송 준비 중...")
        
        # 1. 메일 제목 설정 (날짜 포함)
        KST = timezone(timedelta(hours=9))
        today_str = datetime.now(KST).strftime("%Y-%m-%d")
        mail_subject = f"📦 [반도체 데일리 뉴스] {today_str} 리포트"
        
        # 2. 메일 본문 가독성 처리 (마크다운의 줄바꿈을 HTML의 <br>로 변환)
        # full_text는 AI가 생성한 전체 내용입니다.
        email_body = full_text.replace("\n", "<br>")
        
        # 3. 실제 발송 대상 설정 및 함수 실행
        target_email = "keenhwi@gmail.com"
        send_email(mail_subject, email_body, target_email)
        
        print("\n✅✅✅ 모든 공정이 성공적으로 완료되었습니다! ✅✅✅")
        
    except Exception as error:
        print(f"\n⚠️ 시스템 경보: {error}")
        raise error

