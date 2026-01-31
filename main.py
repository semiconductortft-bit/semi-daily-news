import os
import time
import feedparser
import urllib.parse
from datetime import datetime, timedelta, timezone
from google import genai
from elevenlabs.client import ElevenLabs
from collections import defaultdict
from urllib.parse import urlparse
from dateutil import parser as date_parser # 날짜 파싱용

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

def fetch_news():
    print("📡 뉴스 데이터 수집 및 정밀 필터링 중... (최근 24시간 이내 + 10개 제한)")
    
    # 한국 시간(KST) 기준 현재 요일 확인 (0: 월, 1: 화, ..., 5: 토, 6: 일)
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    weekday = now_kst.weekday()

    # 1. 일요일 발행 중단 로직
    if weekday == 6:
        print("📅 오늘은 일요일입니다. 리포트를 발행하지 않습니다.")
        return None

    # 2. 요일에 따른 검색 기간(when) 설정
    # 월요일(0)이면 7일(7d), 그 외 평일은 1일(1d)
    search_period = "7d" if weekday == 0 else "1d"
    print(f"📡 뉴스 데이터 수집 중... (검색 기간: {search_period})")
    
    # 2. 타겟 매체 설정
    GLOBAL_TARGETS = {
    "digitimes.com": "Digitimes",
    "electronicsweekly.com": "Electronics Weekly",
    "eetimes.com": "EE Times",
    "trendforce.com": "TrendForce",
    "semiconductor-digest.com": "Semi Digest",
    "semiengineering.com": "Semiconductor Engineering",
    "3dincites.com": "3D InCites",
    "yolegroup.com": "Yole Group",
    "ddaily.co.kr": "Digital Daily"
    }
    KOREA_TARGETS = {
        "thelec.kr": "TheElec",
        "zdnet.co.kr": "ZDNet Korea",
        "dt.co.kr": "Digital Times",
        "hankyung.com": "Hankyung Insight",
        "etnews.com": "ETNews",
        "kipost.net": "KIPOST"
    }
    ALL_TARGETS = {**GLOBAL_TARGETS, **KOREA_TARGETS}

KEYWORDS = [
    # 기존 핵심 키워드
    'semiconductor', 'advanced packaging', 'hbm', 'tsmc', 'samsung', 'sk hynix', 'micron', 'hbf',
    'wafer', 'chiplet', 'interposer','intel'
    
    # 공정 및 구조 확장
    'Hybrid Bonding', 'CoWoS', 'FOWLP', 'PLP', '3D IC', 'TSV',
    
    # 소재 및 재료개발 (TFT 핵심)
    'Glass Substrate', 'TC-NCF', 'MUF', 'EMC', 'Substrate material',
    
    # 차세대 아키텍처
    'CXL', 'BSPDN', 'UCIe', 'Silicon Photonics', 'Heterogeneous Integration'
    ]
    
# 3. RSS 수집 함수 (search_period 반영)
def fetch_rss(targets, region, lang):
        site_query = " OR ".join([f"site:{d}" for d in targets.keys()])
        kw_query = " OR ".join(KEYWORDS)
        final_query = f"({site_query}) AND ({kw_query})"
        encoded_query = urllib.parse.quote(final_query)
        # 설정된 기간(search_period)을 URL에 반영
        url = f"https://news.google.com/rss/search?q={encoded_query}+when:{search_period}&hl={lang}&gl={region}&ceid={region}:{lang}"
        return feedparser.parse(url).entriesentries

    raw_articles = []
    print("   - 글로벌/국내 소스 스캔 중...")
    raw_articles.extend(fetch_rss(GLOBAL_TARGETS, "US", "en-US"))
    raw_articles.extend(fetch_rss(KOREA_TARGETS, "KR", "ko"))

    # 4. [핵심] 날짜 기반 강제 필터링 & 정제
    valid_articles = []
    seen_links = set()
    # --- 추가: 제외 키워드 설정 (주식, 증권, 투자 유도 등) ---
    EXCLUDE_KEYWORDS = [
        '주가', '증시', '종목', '상한가', '하한가', '매수', '매도', '수익률', 
        '개미', '외인', '기관', '테마주', '급등', '급락', '투자정보', '증권사',
        'stock', 'shares', 'trading', 'investment', 'price target', 'buy rating'
    ]
    # ----------------------------------------------------
    print(f"   - 1차 수집된 기사 수: {len(raw_articles)}개")

    for e in raw_articles:
        if e.link in seen_links: continue

        # URL 정제: 구글 뉴스 리디렉션 파라미터를 최소화하고 안전하게 인코딩
        original_link = e.link
        # 만약 URL에 한글이나 특수문자가 섞여 리디렉션 오류가 난다면 아래와 같이 처리
        clean_url = urllib.parse.unquote(original_link).split("&url=")[-1].split("&")[0] if "&url=" in original_link else original_link
    
        # (A) 날짜 파싱 및 검증
        try:
            # feedparser가 파싱해준 날짜가 있으면 사용, 없으면 문자열 파싱 시도
            if hasattr(e, 'published_parsed') and e.published_parsed:
                # struct_time을 datetime 객체로 변환
                pub_date = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            else:
                pub_date = date_parser.parse(e.published)
                # timezone 정보가 없으면 UTC로 가정
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
            
            # (B) 24시간 이내인지 확인 (오래된 기사 즉시 폐기)
            if pub_date < cutoff_date:
                continue

        except Exception as err:
            # 날짜 파싱 실패 시 안전하게 스킵 (오래된 기사일 확률 높음)
            continue

        # --- 추가: 순수 반도체 뉴스 필터링 (주식 관련 내용 제외) ---
        title = e.title.lower()
        summary = e.summary.lower() if hasattr(e, 'summary') else ""
        
        # 제외 키워드가 제목이나 요약에 포함되어 있는지 확인
        is_stock_news = any(kw in title or kw in summary for kw in EXCLUDE_KEYWORDS)
        
        if is_stock_news:
            # 주식 관련 기사는 건너뜁니다.
            continue
        # -------------------------------------------------------
        
        seen_links.add(e.link)
        e['parsed_date'] = pub_date # 정렬을 위해 저장
        
        # (C) 출처명 매핑
        domain = urlparse(e.link).netloc.replace("www.", "")
        source_name = "News"
        for t_domain, t_name in ALL_TARGETS.items():
            if t_domain in domain:
                source_name = t_name
                break
        if source_name == "News" and hasattr(e, 'source'):
            source_name = e.source.title
        
        e['display_source'] = source_name
        valid_articles.append(e)

        # (C) 출처명 매핑 부분에서 URL 저장 시 clean_url 사용
        e['link'] = original_link # 또는 정제된 clean_url

    print(f"   - 24시간 이내 유효 기사: {len(valid_articles)}개")

    # 5. 매체별 쿼터제 (다양성 확보)
    buckets = defaultdict(list)
    for e in valid_articles:
        buckets[e['display_source']].append(e)
    
    # 각 버킷 최신순 정렬
    for s in buckets:
        buckets[s].sort(key=lambda x: x['parsed_date'], reverse=True)

    final_selection = []
    selected_titles = set()
    
    # 우선순위: 지정 매체 리스트 순서대로 1개씩 뽑기
    priority_order = list(ALL_TARGETS.values())
    
    # 1라운드: 매체별 1개씩 (최대 2개까지 허용)
    for _ in range(2): # 최대 2바퀴를 돕니다.
        for source_name in priority_order:
            if buckets[source_name]:
                article = buckets[source_name].pop(0)
                if article.title not in selected_titles:
                    final_selection.append(article)
                    selected_titles.add(article.title)
            if len(final_selection) >= 10: break
        if len(final_selection) >= 10: break

    # 만약 10개가 안 채워졌다면 나머지에서 최신순으로 보충
    if len(final_selection) < 10:
        remaining = []
        for s_list in buckets.values(): remaining.extend(s_list)
        remaining.sort(key=lambda x: x['parsed_date'], reverse=True)
        for article in remaining:
            if len(final_selection) >= 10: break
            if article.title not in selected_titles:
                final_selection.append(article)
                selected_titles.add(article.title)

    # [핵심] URL 리디렉션 해결을 위해 google news 링크 대신 'clean_url' 전달 로직 확인
    # RSS에서 제공하는 link가 가끔 인코딩 이슈를 일으키므로 
    # 프롬프트에서 HTML <a> 태그 형식을 직접 쓰도록 유도합니다.
    return final_selection # 객체 리스트 형태로 반환하여 generate_content에 전달

    # 2라운드: 남은 기사 중 최신순으로 채우기
    remaining = []
    for source_list in buckets.values():
        remaining.extend(source_list)
    remaining.sort(key=lambda x: x['parsed_date'], reverse=True)

    # ★ 10개 제한 설정
    TARGET_COUNT = 10
    
    for article in remaining:
        if len(final_selection) >= TARGET_COUNT: break
        if article.title not in selected_titles:
            final_selection.append(article)
            selected_titles.add(article.title)

    # 6. 최종 텍스트 생성
    formatted_text = []
    # 결과 보여줄 때도 최신순 정렬
    final_selection.sort(key=lambda x: x['parsed_date'], reverse=True)

    for i, e in enumerate(final_selection):
        clean_summ = e.summary.replace("<b>", "").replace("</b>", "").replace("&nbsp;", " ") if hasattr(e, 'summary') else ""
        
        # AI 프롬프트에 들어갈 포맷
        item = (
            f"[{i+1}] Source: {e['display_source']}\n"
            f"Date: {e['parsed_date'].strftime('%Y-%m-%d %H:%M')}\n"
            f"Title: {e.title}\n"
            f"URL: {e.link}\n"
            f"Summary: {clean_summ[:300]}\n"
        )
        formatted_text.append(item)

    if not formatted_text:
        return "최근 24시간 이내의 관련 뉴스가 없습니다."

    print(f"✅ 최종 선별 완료: {len(formatted_text)}개 (10개 제한, 24시간 이내 엄수)")
    return "\n".join(formatted_text)

def generate_content(news_text):
    """Gemini를 이용해 뉴스레터와 라디오 스크립트 생성"""
    print("🤖 AI 분석 및 집필 중... (가독성 최적화 모드)")
    # 한국 시간(KST, UTC+9) 설정
    KST = timezone(timedelta(hours=9))
    today_date = datetime.now(KST).strftime("%Y년 %m월 %d일")
    publisher = "반도체재료개발TFT 김동휘"
  
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
    KST = timezone(timedelta(hours=9))
    date_str = datetime.now(KST).strftime("%Y-%m-%d")
    
    # 1. 날짜별 저장 폴더 경로 설정 (예: newsletter/2026-01-29)
    folder_path = f"newsletter/{date_str}"
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    # 2. 오디오 파일 이동 (폴더 안으로)
    audio_filename = "radio.mp3"
    audio_path = os.path.join(folder_path, audio_filename)
    if os.path.exists("radio.mp3"):
        os.rename("radio.mp3", audio_path)
    
    # 3. 뉴스레터 내용에 오디오 플레이어 경로 수정
    # 배포용 index.md에서 이 파일을 참조할 수 있게 경로를 설정합니다.
    audio_player = ""
    if os.path.exists(audio_path):
        audio_player = f"<audio controls style='width: 100%;'><source src='{folder_path}/{audio_filename}' type='audio/mpeg'></audio>\n\n---\n\n"

    # 4. 아카이빙용 파일 저장 (폴더 내부)
    with open(os.path.join(folder_path, "index.md"), "w", encoding="utf-8") as f:
        f.write(content)

    # 5. 최신 배포용 파일 저장 (저장소 최상위 루트)
    # GitHub Pages는 보통 루트의 index.md를 첫 화면으로 보여줍니다.
    with open("index.md", "w", encoding="utf-8") as f:
        f.write(audio_player + content)

if __name__ == "__main__":
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
        print("\n✅✅✅ 모든 공정이 성공적으로 완료되었습니다! ✅✅✅")
    except Exception as error:
        print(f"\n⚠️ 시스템 경보: {error}")
        raise error



