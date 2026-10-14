from collectors.article_subheading import extract_mtn_prebody_subheadings, is_site_ui_text
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from collectors.browser import launch_chromium
from collectors.base import BaseNewsCollector
from config import NEWS_SOURCES
from models import Article


SOURCE = NEWS_SOURCES["mtn"]

BASE_URL = "https://news.mtn.co.kr/"
SECTION_URL = "https://news.mtn.co.kr/category-news/M0300"

ARTICLES_PER_PAGE = 20
MAX_PAGE = 100
OLD_PAGE_STREAK_LIMIT = 2

# 목록 날짜는 상세페이지를 확인할 후보를 좁히는 힌트다.
# 최종 수집 날짜는 상세페이지에 표시된 날짜로 결정한다.
CANDIDATE_LOOKBACK_DAYS = 3
CANDIDATE_LOOKAHEAD_DAYS = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


# ============================================================
# HTTP SESSION
# ============================================================

def create_session():
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry)

    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


# ============================================================
# BASIC TEXT
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", str(text)).strip()


# ============================================================
# URL
# ============================================================

def is_valid_mtn_article_url(url):
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False

    host = parsed.netloc.lower().replace("www.", "")

    if host != "news.mtn.co.kr":
        return False

    return bool(
        re.fullmatch(
            r"/news-detail/\d+/?",
            parsed.path,
        )
    )


def normalize_mtn_article_url(url):
    if not url:
        return ""

    full_url = urljoin(BASE_URL, url)

    try:
        parsed = urlparse(full_url)
    except (ValueError, TypeError):
        return ""

    clean_url = (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    )

    if not is_valid_mtn_article_url(clean_url):
        return ""

    return clean_url.rstrip("/")


# ============================================================
# DATE HELPERS
# ============================================================

DATE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    r"(?:\s*[T ]?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)


def parse_datetime_text(value):
    """
    2026-10-08 15:03:15
    2026.10.08 15:03
    2026-10-08T15:03:15
    등의 표시를 통일한다.
    """

    if not value:
        return None

    match = DATE_PATTERN.search(clean_text(value))

    if not match:
        return None

    year, month, day, hour, minute, second = match.groups()

    try:
        parsed = datetime(
            int(year),
            int(month),
            int(day),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
        )

    except ValueError:
        return None

    return parsed


def parse_listing_date(text):
    parsed = parse_datetime_text(text)
    return parsed.date() if parsed else None


def extract_date_from_url(url):
    """
    MTN URL 숫자의 앞 8자리를 날짜 힌트로 사용한다.

    이것은 화면 표시일이 아니라 후보 탐색용이며,
    최종 published_at의 근거로 사용하지 않는다.
    """

    try:
        path = urlparse(url).path
    except (TypeError, ValueError):
        return None

    match = re.search(r"/news-detail/(\d{8})\d*", path)

    if not match:
        return None

    try:
        return datetime.strptime(
            match.group(1), "%Y%m%d"
        ).date()
    except ValueError:
        return None


def extract_published_at(page_text, soup=None):
    """
    MTN 상세페이지의 기사 표시 시각을 추출한다.

    1. 제목/기자명 부근의 time 태그
    2. 기사 메타정보의 publication date
    3. 화면의 입력/등록/발행 날짜
    4. 화면에서 확인되는 일반 날짜

    수정일(modified)은 발행일로 사용하지 않는다.
    """

    if soup is not None:
        # 명시적인 최초 발행 메타정보
        for attr, value in (
            ("property", "article:published_time"),
            ("name", "article:published_time"),
            ("name", "pubdate"),
            ("itemprop", "datePublished"),
        ):
            tag = soup.find("meta", attrs={attr: value})

            if tag:
                raw = tag.get("content", "")
                parsed = parse_datetime_text(raw)

                if parsed:
                    return parsed.strftime("%Y-%m-%d %H:%M:%S")

        # 화면의 기사 제목 주변과 기사 헤더를 우선 검색
        header_roots = []

        title_tag = soup.find("h1")

        if title_tag:
            header_roots.append(title_tag.parent)

            if title_tag.parent is not None:
                header_roots.append(title_tag.parent.parent)

        for selector in (
            ".article-info",
            ".article_info",
            ".news-info",
            ".news_info",
            ".article-date",
            ".article_date",
            ".date",
        ):
            header_roots.extend(soup.select(selector))

        for root in header_roots:
            if root is None:
                continue

            for tag in root.find_all("time"):
                raw = (
                    tag.get("datetime")
                    or tag.get_text(" ", strip=True)
                )

                parsed = parse_datetime_text(raw)

                if parsed:
                    return parsed.strftime("%Y-%m-%d %H:%M:%S")

        # 수정일과 명시적으로 구분되는 날짜 표현
        for prefix in (
            r"기사\s*입력",
            r"기사\s*등록",
            r"등록일?",
            r"입력일?",
            r"발행일?",
        ):
            match = re.search(
                prefix + r"\s*[:：]?\s*" + DATE_PATTERN.pattern,
                page_text,
            )

            if match:
                parsed = parse_datetime_text(match.group(0))

                if parsed:
                    return parsed.strftime("%Y-%m-%d %H:%M:%S")

    # MTN의 기존 화면 표기: 기자명 바로 아래 시각
    lines = [
        clean_text(line)
        for line in (page_text or "").splitlines()
        if clean_text(line)
    ]

    # 화면에서 기자명에 가까운 날짜를 우선한다.
    for index, line in enumerate(lines):
        if not re.search(r"[가-힣]{2,4}\s*기자", line):
            continue

        for candidate in lines[index:index + 4]:
            if re.search(r"수정|업데이트", candidate):
                continue

            parsed = parse_datetime_text(candidate)

            if parsed and parsed.year >= 2000:
                return parsed.strftime("%Y-%m-%d %H:%M:%S")

    # 마지막 fallback: 명확한 수정일이 아닌 시간 표기
    for line in lines:
        if re.search(r"수정|업데이트", line):
            continue

        parsed = parse_datetime_text(line)

        if parsed and parsed.year >= 2000:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")

    return None


# ============================================================
# ONE LIST PAGE
# ============================================================

def collect_mtn_page_entries(page, page_number):
    """
    MTN 건설부동산 목록에서 실제 메인 기사 링크를 수집한다.

    건설부동산 H1의 부모 DIV.flex_1 영역으로 검색을 제한한다.

    날짜를 못 읽어도 URL은 버리지 않는다.
    """

    page_url = f"{SECTION_URL}?page={page_number}"

    page.goto(
        page_url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    try:
        page.get_by_role(
            "heading",
            name="건설부동산",
            exact=True,
        ).last.wait_for(
            state="visible",
            timeout=15000,
        )

    except Exception:
        print(
            f"[WARNING] MTN page {page_number}: "
            "건설부동산 heading 없음"
        )
        return []

    try:
        page.wait_for_selector(
            'a[href*="/news-detail/"]',
            timeout=15000,
        )

    except Exception:
        print(
            f"[WARNING] MTN page {page_number}: 기사 링크 없음"
        )
        return []

    headings = page.get_by_role(
        "heading",
        name="건설부동산",
        exact=True,
    )

    if headings.count() == 0:
        return []

    section_heading = headings.last

    # --------------------------------------------------------
    # SECTION CONTAINER
    # --------------------------------------------------------
    #
    # 기존 문제:
    #
    # xpath=following::a
    #
    # 이 선택자는 건설부동산 heading 이후의
    # 모든 기사 링크를 가져와 다른 카테고리까지 혼입했다.
    #
    # 확인된 MTN DOM 구조:
    #
    # DIV.flex_1
    #     H1 건설부동산
    #     건설부동산 기사 목록
    #
    # 따라서 heading의 직속 부모 DIV.flex_1로
    # 검색 범위를 제한한다.
    # --------------------------------------------------------

    section_container = section_heading.locator(
        "xpath=.."
    )

    container_class = (
        section_container.get_attribute("class")
        or ""
    )

    if "flex_1" not in container_class.split():
        print(
            f"[WARNING] MTN page {page_number}: "
            "건설부동산 DIV.flex_1 컨테이너 확인 실패 | "
            f"class={container_class}"
        )
        return []

    # 중요:
    # section_container 내부 링크만 탐색한다.
    # following::a를 절대 사용하지 않는다.

    links = section_container.locator(
        'a[href*="/news-detail/"]'
    )

    raw_link_count = links.count()

    print(
        f"[MTN] page {page_number}: "
        "건설부동산 전용 컨테이너 | "
        f"기사 링크 {raw_link_count}개"
    )

    entries = []
    seen = set()

    # 같은 기사에 여러 링크가 있는 경우도 있으므로
    # 원시 링크 20개가 아니라 고유 기사 20개까지 수집한다.

    for index in range(raw_link_count):
        if len(entries) >= ARTICLES_PER_PAGE:
            break

        link = links.nth(index)

        try:
            href = link.get_attribute("href")
        except Exception:
            continue

        url = normalize_mtn_article_url(href)

        if not url or url in seen:
            continue

        listing_date = None

        try:
            article_li = link.locator("xpath=ancestor::li[1]")

            if article_li.count():
                time_element = article_li.locator("time")

                if time_element.count():
                    time_tag = time_element.first

                    raw_time = (
                        time_tag.get_attribute("datetime")
                        or time_tag.inner_text()
                    )

                    listing_date = parse_listing_date(raw_time)

        except Exception:
            listing_date = None

        if listing_date is None:
            try:
                listing_date = parse_listing_date(
                    link.inner_text()
                )
            except Exception:
                listing_date = None

        url_date = extract_date_from_url(url)

        if listing_date is None:
            print(
                f"[MTN] page {page_number}: "
                "목록 날짜 미확인 → 상세 확인 후보 유지 | "
                f"{url}"
            )

        seen.add(url)

        entries.append({
            "url": url,
            "date": listing_date or url_date,
            "listing_date": listing_date,
            "url_date": url_date,
        })

    return entries


# ============================================================
# MULTI-PAGE CANDIDATE COLLECTION
# ============================================================

def is_candidate(entry, start_date, end_date):
    earliest = start_date - timedelta(
        days=CANDIDATE_LOOKBACK_DAYS
    )
    latest = end_date + timedelta(
        days=CANDIDATE_LOOKAHEAD_DAYS
    )

    dates = [
        day for day in (
            entry.get("listing_date"),
            entry.get("url_date"),
        )
        if day is not None
    ]

    # 목록/URL 모두 날짜가 없다면 버리지 않는다.
    if not dates:
        return True

    return any(earliest <= day <= latest for day in dates)


def collect_mtn_article_candidates(start_date, end_date):
    """
    목록 날짜를 이용해 상세페이지 확인 후보를 수집한다.
    최종 날짜 판정은 collect_mtn_articles에서 수행한다.
    """

    candidates = []
    seen_urls = set()

    previous_signature = None
    old_page_streak = 0

    earliest = start_date - timedelta(
        days=CANDIDATE_LOOKBACK_DAYS
    )

    with sync_playwright() as playwright:
        browser = launch_chromium(playwright)

        try:
            page = browser.new_page(
                viewport={
                    "width": 1440,
                    "height": 1200,
                }
            )

            for page_number in range(1, MAX_PAGE + 1):
                try:
                    entries = collect_mtn_page_entries(
                        page=page,
                        page_number=page_number,
                    )

                except Exception as exc:
                    print(
                        f"[WARNING] MTN page {page_number} "
                        f"수집 실패: {exc}"
                    )
                    continue

                if not entries:
                    print(
                        f"[MTN] page {page_number}: "
                        "기사 없음 → 탐색 종료"
                    )
                    break

                signature = tuple(
                    entry["url"] for entry in entries
                )

                if signature == previous_signature:
                    print(
                        f"[MTN] page {page_number}: "
                        "이전 페이지와 동일 → 탐색 종료"
                    )
                    break

                previous_signature = signature

                page_dates = [
                    entry["date"]
                    for entry in entries
                    if entry["date"] is not None
                ]

                matched = 0

                for entry in entries:
                    if not is_candidate(
                        entry, start_date, end_date
                    ):
                        continue

                    url = entry["url"]

                    if url in seen_urls:
                        continue

                    seen_urls.add(url)
                    candidates.append(entry)
                    matched += 1

                if page_dates:
                    newest_date = max(page_dates)
                    oldest_date = min(page_dates)

                    date_label = (
                        f"{oldest_date} ~ {newest_date}"
                    )

                    if newest_date < earliest:
                        old_page_streak += 1
                    else:
                        old_page_streak = 0

                else:
                    date_label = "날짜 미확인"
                    old_page_streak = 0

                print(
                    f"[MTN] page {page_number}: "
                    f"{date_label} | "
                    f"메인 {len(entries)}개 | "
                    f"상세 확인 후보 +{matched}개"
                )

                if old_page_streak >= OLD_PAGE_STREAK_LIMIT:
                    print(
                        "[MTN] 후보 탐색 기간보다 오래된 "
                        f"페이지가 {OLD_PAGE_STREAK_LIMIT}개 "
                        "연속 확인되어 탐색 종료"
                    )
                    break

        finally:
            browser.close()

    print(
        f"[MTN] 상세페이지 확인 후보 "
        f"{len(candidates)}개 확보"
    )

    return candidates


def collect_mtn_article_urls(start_date, end_date):
    """
    기존 함수명 호환용.

    반환되는 URL은 최종 기간 일치 기사 목록이 아니라
    상세페이지 날짜를 확인할 후보 목록이다.
    """

    return [
        entry["url"]
        for entry in collect_mtn_article_candidates(
            start_date, end_date
        )
    ]


# ============================================================
# TITLE
# ============================================================

def extract_title(soup):
    og_title = soup.find("meta", property="og:title")

    if og_title:
        title = clean_text(og_title.get("content", ""))

        if title:
            return re.sub(
                r"\s*[-|]\s*MTN.*$",
                "",
                title,
                flags=re.IGNORECASE,
            ).strip()

    h1 = soup.find("h1")

    if h1:
        title = clean_text(
            h1.get_text(" ", strip=True)
        )

        if title:
            return title

    if soup.title:
        title = clean_text(
            soup.title.get_text(" ", strip=True)
        )

        return re.sub(
            r"\s*[-|]\s*MTN.*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()

    return ""


# ============================================================
# REPORTER
# ============================================================

def extract_reporter(page_text):
    match = re.search(
        r"([가-힣]{2,4})\s*기자\s+"
        r"\d{4}-\d{2}-\d{2}",
        page_text,
    )

    if match:
        return match.group(1).strip()

    match = re.search(
        r"([가-힣]{2,4})\s*기자",
        page_text,
    )

    if match:
        return match.group(1).strip()

    return None


# ============================================================
# ARTICLE CONTAINER
# ============================================================

def find_article_container(soup):
    selectors = (
        "article",
        ".article-body",
        ".article_body",
        ".article-content",
        ".article_content",
        ".news-body",
        ".news_body",
        ".news-content",
        ".news_content",
    )

    for selector in selectors:
        for element in soup.select(selector):
            text = clean_text(
                element.get_text(" ", strip=True)
            )

            if len(text) >= 150:
                return element

    return None


# ============================================================
# CAPTION
# ============================================================


def extract_caption_near_body(page_text, paragraphs):
    """첫 본문 문단 바로 앞에 있는 사진 설명을 찾는다."""
    import re

    if not paragraphs:
        return None

    lines = [
        clean_text(line)
        for line in (page_text or "").splitlines()
        if clean_text(line)
    ]

    first_body = clean_text(paragraphs[0])[:45]
    if not first_body:
        return None

    body_index = next(
        (
            i for i, line in enumerate(lines)
            if first_body in line
        ),
        None,
    )

    if body_index is None:
        return None

    # 첫 본문 직전의 제한된 영역만 확인한다.
    region = lines[max(0, body_index - 18):body_index]

    caption_pattern = re.compile(
        r"(?:제공|사진|촬영)\\s*[=:：]"
        r"|노선\\s*예정도|노선도|조감도",
        re.I,
    )

    for line in reversed(region):
        if not 7 <= len(line) <= 200:
            continue

        if is_site_ui_text(line):
            continue

        if caption_pattern.search(line):
            return line

    return None



def extract_mtn_table_caption(soup):
    """사진 바로 아래의 표 행에서 실제 사진 설명 추출."""

    markers = (
        "/제공=",
        "/제공:",
        "/사진=",
        "/사진:",
        "/자료=",
        "/촬영=",
        "노선 예정도",
        "노선도",
        "조감도",
    )

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            if row.find("img") is None:
                continue

            caption_row = row.find_next_sibling("tr")

            if caption_row is None:
                continue

            caption = clean_text(
                caption_row.get_text(" ", strip=True)
            )

            if not 5 <= len(caption) <= 250:
                continue

            if is_site_ui_text(caption):
                continue

            if any(marker in caption for marker in markers):
                return caption

    return None


def extract_caption(soup, container):
    if container:
        figcaption = container.find("figcaption")

        if figcaption:
            text = clean_text(
                figcaption.get_text(" ", strip=True)
            )

            if text:
                return text

    for img in soup.find_all("img"):
        parent = img.parent

        if not parent:
            continue

        text = clean_text(
            parent.get_text(" ", strip=True)
        )

        if (
            3 <= len(text) <= 250
            and any(
                word in text
                for word in (
                    "사진",
                    "자료",
                    "제공",
                    "조감도",
                    "노선도",
                )
            )
        ):
            return text

    return None


# ============================================================
# BODY HELPERS
# ============================================================

def is_reporter_contact_line(text):
    text = clean_text(text)

    if not text:
        return True

    if re.fullmatch(
        r"[가-힣]{2,4}\s*기자"
        r"(?:\s+"
        r"[A-Za-z0-9._%+-]+@"
        r"[A-Za-z0-9.-]+\."
        r"[A-Za-z]{2,})?",
        text,
    ):
        return True

    if re.fullmatch(
        r"[A-Za-z0-9._%+-]+@"
        r"[A-Za-z0-9.-]+\."
        r"[A-Za-z]{2,}",
        text,
    ):
        return True

    if re.fullmatch(
        r"[가-힣]{2,4}\s+"
        r"머니투데이방송\s+MTN\s+기자",
        text,
    ):
        return True

    return False


def is_body_junk_line(text):
    text = clean_text(text)

    if not text:
        return True

    if is_reporter_contact_line(text) or is_site_ui_text(text):
        return True

    junk_terms = (
        "저작권자",
        "무단전재",
        "재배포",
        "MTN 기자실",
        "많이본뉴스",
        "많이 본 뉴스",
        "고충처리인",
        "개인정보처리방침",
        "청소년보호정책",
        "머니투데이방송의 기사에 대해",
        "반론·정정",
        "반론ㆍ정정",
        "열린보도원칙",
        "Copyright",
    )

    return any(
        term in text
        for term in junk_terms
    )


def looks_like_caption(text):
    text = clean_text(text)

    if not 3 <= len(text) <= 250:
        return False

    caption_terms = (
        "/제공=",
        "제공=",
        "[사진=",
        "사진=",
        "노선도",
        "조감도",
        "전경",
    )

    return any(
        term in text
        for term in caption_terms
    )


def deduplicate_paragraphs(paragraphs):
    result = []
    seen = set()

    for paragraph in paragraphs:
        text = clean_text(paragraph)

        if not text:
            continue

        if is_body_junk_line(text):
            continue

        if text in seen:
            continue

        seen.add(text)
        result.append(text)

    return result


def is_meaningful_body(paragraphs):
    cleaned = deduplicate_paragraphs(paragraphs)

    if not cleaned:
        return False

    total_chars = sum(
        len(paragraph) for paragraph in cleaned
    )

    longest = max(
        (len(paragraph) for paragraph in cleaned),
        default=0,
    )

    if total_chars < 120:
        return False

    if len(cleaned) < 2 and longest < 120:
        return False

    return True


# ============================================================
# BODY - HTML
# ============================================================

def extract_paragraphs_from_html(soup, container):
    search_root = (
        container if container is not None else soup
    )

    paragraphs = []

    for tag in search_root.find_all("p"):
        text = clean_text(
            tag.get_text(" ", strip=True)
        )

        if len(text) < 15:
            continue

        if is_body_junk_line(text):
            continue

        paragraphs.append(text)

    return deduplicate_paragraphs(paragraphs)


# ============================================================
# BODY - TEXT FALLBACK
# ============================================================

def extract_paragraphs_from_page_text(
    page_text,
    title,
    image_caption=None,
):
    raw_lines = [
        clean_text(line)
        for line in (page_text or "").splitlines()
        if clean_text(line)
    ]

    paragraphs = []
    collecting = False

    for line in raw_lines:
        if not collecting:
            if re.fullmatch(
                r"\d{4}[-./]\d{1,2}[-./]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?",
                line,
            ):
                collecting = True

            continue

        if re.fullmatch(
            r"[가-힣]{2,4}\s+"
            r"머니투데이방송\s+MTN\s+기자",
            line,
        ):
            break

        if "머니투데이방송의 기사에 대해" in line:
            break

        if "고충처리인" in line:
            break

        if title and clean_text(title) == line:
            continue

        if (
            image_caption
            and clean_text(image_caption) == line
        ):
            continue

        if not paragraphs and looks_like_caption(line):
            continue

        if is_body_junk_line(line):
            continue

        if len(line) < 15:
            continue

        paragraphs.append(line)

    return deduplicate_paragraphs(paragraphs)


def extract_paragraphs(
    soup,
    container,
    page_text,
    title,
    image_caption,
):
    html_paragraphs = extract_paragraphs_from_html(
        soup=soup,
        container=container,
    )

    if is_meaningful_body(html_paragraphs):
        return html_paragraphs

    text_paragraphs = extract_paragraphs_from_page_text(
        page_text=page_text,
        title=title,
        image_caption=image_caption,
    )

    if is_meaningful_body(text_paragraphs):
        return text_paragraphs

    html_chars = sum(len(text) for text in html_paragraphs)
    text_chars = sum(len(text) for text in text_paragraphs)

    if text_chars > html_chars:
        return text_paragraphs

    return html_paragraphs


# ============================================================
# SINGLE ARTICLE
# ============================================================

def collect_mtn_article(url, session=None):
    own_session = session is None

    if own_session:
        session = create_session()

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        page_text = soup.get_text("\n", strip=True)

        container = find_article_container(soup)
        title = extract_title(soup)

        image_caption = (
            extract_mtn_table_caption(soup)
            or extract_caption(soup, container)
        )

        paragraphs = extract_paragraphs(
            soup=soup,
            container=container,
            page_text=page_text,
            title=title,
            image_caption=image_caption,
        )

        if not image_caption or is_site_ui_text(image_caption):
            image_caption = extract_caption_near_body(
                page_text, paragraphs
            )

        published_at = extract_published_at(
            page_text,
            soup,
        )

        article = Article(
            source=SOURCE["name"],
            title=title,
            reporter=extract_reporter(page_text),
            published_at=published_at,
            image_caption=image_caption,
            paragraphs=paragraphs,
            url=url,
        )

        article.subheadings = extract_mtn_prebody_subheadings(
            soup, article
        )

        return article

    finally:
        if own_session:
            session.close()


# ============================================================
# PERIOD
# ============================================================

def is_in_period(article, start_date, end_date):
    if not article.published_at:
        return False

    parsed = parse_datetime_text(article.published_at)

    if parsed is None:
        return False

    return start_date <= parsed.date() <= end_date


# ============================================================
# MAIN COLLECTION
# ============================================================

def collect_mtn_articles(start_date, end_date):
    """
    목록에서는 상세 확인 후보만 고르고,
    최종 날짜 판정은 상세페이지에서 수행한다.
    """

    candidates = collect_mtn_article_candidates(
        start_date=start_date,
        end_date=end_date,
    )

    session = create_session()
    articles = []

    try:
        for candidate in candidates:
            url = candidate["url"]

            try:
                article = collect_mtn_article(
                    url=url,
                    session=session,
                )

                if not article.title:
                    print(
                        f"[WARNING] MTN 제목 누락: {url}"
                    )
                    continue

                if not article.published_at:
                    print(
                        f"[WARNING] MTN 상세페이지 날짜 누락: {url}"
                    )
                    continue

                if not is_in_period(
                    article,
                    start_date,
                    end_date,
                ):
                    print(
                        "[MTN] 상세 표시일 기준 기간 외 제외 | "
                        f"{article.published_at} | "
                        f"{article.title}"
                    )
                    continue

                if not is_meaningful_body(article.paragraphs):
                    print(
                        "[WARNING] MTN 실제 본문 부족: "
                        f"{article.title} | {url}"
                    )
                    continue

                articles.append(article)

            except requests.RequestException as exc:
                print(
                    f"[WARNING] MTN 기사 요청 실패: "
                    f"{url} | {exc}"
                )

            except Exception as exc:
                print(
                    f"[WARNING] MTN 기사 처리 실패: "
                    f"{url} | {exc}"
                )

    finally:
        session.close()

    articles.sort(
        key=lambda article: article.published_at or "",
        reverse=True,
    )

    print(
        f"[MTN] 상세 표시일 기준 기간 내 기사 "
        f"{len(articles)}개"
    )

    return articles


# ============================================================
# COLLECTOR CLASS
# ============================================================

class MTNCollector(BaseNewsCollector):
    source_name = SOURCE["name"]

    def collect_articles(self, start_date, end_date):
        return collect_mtn_articles(
            start_date=start_date,
            end_date=end_date,
        )