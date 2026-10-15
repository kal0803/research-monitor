"""대한경제 건설부동산 수집기.

수집 기간 판정은 기사 상세페이지의 실제 화면에 표시된 '기사입력' 날짜를
최우선으로 사용한다. 목록/URL 날짜는 상세페이지 확인 후보를 좁히는
보조 정보이며, 둘의 날짜가 불일치해도 URL 날짜만으로 기사를 제외하지 않는다.
"""

import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from collectors.base import BaseNewsCollector
from collectors.article_subheading import (
    extract_page_subheadings,
    extract_dnews_prebody_subheadings,
)
from config import NEWS_SOURCES
from models import Article


SOURCE = NEWS_SOURCES["dnews"]
DNEWS_CONSTRUCTION_URL = SOURCE["section_url"]
ARCHIVE_URL_TEMPLATE = "https://m.dnews.co.kr/uhtml/autosec/D_S1N0_{page}.html"
ARCHIVE_START_PAGE = 2
MAX_ARCHIVE_PAGE = 60
ARCHIVE_OLD_PAGE_STREAK = 3

# 화면 표시일과 URL/목록일이 다른 기사를 찾기 위한 후보 탐색 여유.
# 이 범위는 무제한 누락 방지를 보장하지 않는다. 필요시 운영 로그를 보고 조절.
CANDIDATE_LOOKBACK_DAYS = 3
CANDIDATE_LOOKAHEAD_DAYS = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


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
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def get_idxno(url):
    if not url:
        return ""
    try:
        values = parse_qs(urlparse(url).query).get("idxno", [])
        idxno = clean_text(values[0]) if values else ""
        return idxno if re.fullmatch(r"\d+", idxno) else ""
    except (TypeError, ValueError, IndexError):
        return ""


def extract_published_at_from_url(url):
    """URL ID에 인코딩된 시각. 상세 확인 후보 탐색용으로만 사용한다."""
    idxno = get_idxno(url)
    if len(idxno) < 14:
        return None
    try:
        return datetime.strptime(idxno[:14], "%Y%m%d%H%M%S").strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return None


def published_date_from_string(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").date()
    except ValueError:
        return None


def extract_date_from_url(url):
    return published_date_from_string(extract_published_at_from_url(url))


def normalize_article_url(url):
    if not url:
        return ""
    idxno = get_idxno(urljoin(DNEWS_CONSTRUCTION_URL, url))
    return (
        f"https://m.dnews.co.kr/m_home/view.jsp?idxno={idxno}"
        if idxno else ""
    )


def is_valid_dnews_article_url(url):
    if not url:
        return False
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
    except ValueError:
        return False
    return host in {"dnews.co.kr", "m.dnews.co.kr"} and bool(get_idxno(url))


def desktop_fallback_url(url):
    idxno = get_idxno(url)
    return f"https://www.dnews.co.kr/uhtml/view.jsp?idxno={idxno}" if idxno else ""


def extract_listing_date(text):
    text = clean_text(text)
    if not text:
        return None
    match = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups())).date()
    except ValueError:
        return None


def extract_date_near_link(link):
    # 기존 5단계 상위 탐색 유지. 페이지 구조 변경에도 대응하기 위한 보조정보.
    current = link
    for _ in range(5):
        if current is None:
            break
        if isinstance(current, Tag):
            article_date = extract_listing_date(current.get_text(" ", strip=True))
            if article_date:
                return article_date
        current = current.parent
    return None


def extract_listing_entries(html):
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen = set()
    for link in soup.find_all("a", href=True):
        href = clean_text(link.get("href", ""))
        full_url = urljoin(DNEWS_CONSTRUCTION_URL, href)
        if not is_valid_dnews_article_url(full_url):
            continue
        url = normalize_article_url(full_url)
        if not url or url in seen:
            continue
        seen.add(url)
        listing_date = extract_date_near_link(link)
        url_date = extract_date_from_url(url)
        # 이 값은 탐색 힌트일 뿐, 기사 등록일의 최종 판정값이 아님.
        entries.append({
            "url": url,
            "date": listing_date or url_date,
            "listing_date": listing_date,
            "url_date": url_date,
        })
    return entries


def fetch_listing_page(session, url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def _entry_dates(entry):
    return [
        value for value in (entry.get("listing_date"), entry.get("url_date"))
        if value is not None
    ]


def _is_candidate(entry, start_date, end_date):
    """상세페이지 확인 대상. 기간 판단은 상세페이지를 읽은 뒤에 한다."""
    earliest = start_date - timedelta(days=CANDIDATE_LOOKBACK_DAYS)
    latest = end_date + timedelta(days=CANDIDATE_LOOKAHEAD_DAYS)
    hints = _entry_dates(entry)
    return not hints or any(earliest <= day <= latest for day in hints)


def _candidate_page_dates(entries):
    return [day for entry in entries for day in _entry_dates(entry)]


def _add_candidates(entries, start_date, end_date, candidates, seen):
    added = 0
    for entry in entries:
        if not _is_candidate(entry, start_date, end_date):
            continue
        url = entry["url"]
        if url in seen:
            # 같은 기사가 다른 archive에 있을 때 목록 날짜를 보완한다.
            original = next((x for x in candidates if x["url"] == url), None)
            if original and original.get("listing_date") is None:
                original["listing_date"] = entry.get("listing_date")
            continue
        seen.add(url)
        candidates.append(dict(entry))
        added += 1
    return added


def collect_dnews_article_candidates(start_date, end_date, session=None):
    """URL/목록 날짜를 넉넉히 검사해 상세페이지 확인 후보를 수집한다."""
    own_session = session is None
    if own_session:
        session = create_session()

    candidates = []
    seen = set()
    try:
        print("[대한경제] 현재 목록 확인")
        current_entries = extract_listing_entries(
            fetch_listing_page(session, DNEWS_CONSTRUCTION_URL)
        )
        current_dates = _candidate_page_dates(current_entries)
        added = _add_candidates(
            current_entries, start_date, end_date, candidates, seen
        )
        if current_dates:
            print(
                f"[대한경제] 현재 목록 {len(current_entries)}개 | "
                f"{min(current_dates)} ~ {max(current_dates)} | "
                f"상세 확인 후보 {added}개"
            )
        else:
            print("[대한경제] 현재 목록 날짜 분석 실패")

        print("[대한경제] 과거 archive 탐색 시작")
        old_page_streak = 0
        earliest_hint = start_date - timedelta(days=CANDIDATE_LOOKBACK_DAYS)

        for page_number in range(ARCHIVE_START_PAGE, MAX_ARCHIVE_PAGE + 1):
            archive_url = ARCHIVE_URL_TEMPLATE.format(page=page_number)
            try:
                entries = extract_listing_entries(fetch_listing_page(session, archive_url))
            except requests.RequestException as exc:
                print(f"[WARNING] 대한경제 archive {page_number} 요청 실패: {exc}")
                continue

            if not entries:
                print(f"[대한경제] archive {page_number}: 기사 없음")
                continue

            dates = _candidate_page_dates(entries)
            added = _add_candidates(entries, start_date, end_date, candidates, seen)
            if dates:
                page_newest = max(dates)
                print(
                    f"[대한경제] archive {page_number}: "
                    f"{min(dates)} ~ {page_newest} | 상세 확인 후보 +{added}개"
                )
                if page_newest < earliest_hint:
                    old_page_streak += 1
                else:
                    old_page_streak = 0
            else:
                print(f"[대한경제] archive {page_number}: 날짜 미확인")
                old_page_streak = 0

            if old_page_streak >= ARCHIVE_OLD_PAGE_STREAK:
                print(
                    "[대한경제] 후보 탐색 여유 기간보다 오래된 archive가 "
                    f"{ARCHIVE_OLD_PAGE_STREAK}개 연속 확인되어 종료"
                )
                break

        print(f"[대한경제] 상세페이지 확인 후보 {len(candidates)}개 확보")
        return candidates
    finally:
        if own_session:
            session.close()


def collect_dnews_article_urls(start_date, end_date, session=None):
    """호환용: 상세페이지 확인 후보 URL을 반환한다 (최종 기간 필터 전)."""
    return [
        entry["url"] for entry in collect_dnews_article_candidates(
            start_date, end_date, session=session
        )
    ]


def extract_title(soup):
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = clean_text(og_title.get("content", ""))
        if title:
            return re.sub(r"\s*[-|]\s*대한경제\s*$", "", title).strip()
    h1 = soup.find("h1")
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))
        if title:
            return title
    if soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
        return re.sub(r"\s*[-|]\s*대한경제\s*$", "", title).strip()
    return ""


def extract_published_at(page_text):
    """상세페이지의 기사입력/등록/입력 날짜를 추출한다.

    대한경제의 공백 없는 날짜 표기와 같은 날짜의 24시 표기를 지원한다.
    예: 2026-10-08 24:00:10 -> 2026-10-08 00:00:10.
    수정일과 목록/URL 날짜는 사용하지 않는다.
    """
    patterns = (
        r"기사\s*입력",
        r"기사\s*등록",
        r"등록일?",
        r"입력일?",
    )
    date_tail = (
        r"\s*[:：]?\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
        r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?"
    )
    for prefix in patterns:
        match = re.search(prefix + date_tail, page_text)
        if not match:
            continue
        y, mo, d, hh, mm, ss = match.groups()
        try:
            hour = int(hh)
            # 대한경제는 자정 시간을 '24:xx:xx'로 표시하면서
            # 목록에는 동일 날짜 '00:xx:xx'로 표시하는 사례가 있다.
            # 날짜를 다음 날로 넘기지 않고 시간 부분만 정규화한다.
            if hour == 24:
                hour = 0
            return datetime(
                int(y), int(mo), int(d), hour, int(mm), int(ss or 0)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def extract_reporter(page_text):
    match = re.search(r"\[대한경제=([^\]]+?)\s*기자\]", page_text)
    if match:
        return clean_text(match.group(1))
    matches = re.findall(
        r"([가-힣]{2,4})\s+기자(?:\s+[A-Za-z0-9._-]+@)?", page_text
    )
    return clean_text(matches[-1]) if matches else None


def extract_image_caption(soup, page_text):
    terms = ("제공", "이미지", "사진", "조감도", "노선도", "전경")
    for tag in soup.find_all(("figcaption", "p", "div", "span")):
        text = clean_text(tag.get_text(" ", strip=True))
        if 3 <= len(text) <= 250 and any(term in text for term in terms):
            if "기사입력" not in text:
                return text
    lines = [clean_text(line) for line in page_text.split("\n") if clean_text(line)]
    for line in lines:
        if 3 <= len(line) <= 250 and any(term in line for term in terms):
            if "기사입력" not in line:
                return line
    return None


def clean_paragraph(text):
    return clean_text(text)


def is_junk_paragraph(text):
    terms = (
        "ⓒ 대한경제신문", "무단전재", "재배포금지", "폰트크기", "글자크기",
        "카카오 공유하기", "기자의 다른기사", "Please activate JavaScript",
        "개인정보처리방침", "이용약관",
    )
    return any(term in text for term in terms)


def is_reporter_line(text):
    return bool(re.fullmatch(r"[가-힣]{2,4}\s+기자(?:\s+\S+@)?", clean_text(text)))


def deduplicate_paragraphs(paragraphs):
    result = []
    seen = set()
    for paragraph in paragraphs:
        text = clean_text(paragraph)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def extract_paragraphs_from_reporter_marker(soup):
    reporter_pattern = re.compile(r"\[대한경제=[^\]]+?\s*기자\]")
    reporter_node = soup.find(string=reporter_pattern)
    if reporter_node is None:
        return []
    paragraphs = []
    current = reporter_node.parent
    while current is not None:
        if isinstance(current, Tag):
            text = clean_paragraph(current.get_text(" ", strip=True))
            if text:
                if "ⓒ 대한경제신문" in text or is_reporter_line(text):
                    break
                if current.name == "p":
                    text = reporter_pattern.sub("", text, count=1).strip()
                    if text and not is_junk_paragraph(text):
                        paragraphs.append(text)
        current = current.find_next()
    return deduplicate_paragraphs(paragraphs)


def extract_paragraphs_from_page_text(page_text, title, image_caption=None):
    """화면 텍스트에서 본문 문단을 추출한다.

    기사 본문 시작의 '[대한경제=... 기자]' 마커를 우선 사용하므로
    기자명 위에 있는 부제목을 본문에 혼입하지 않는다.
    """
    raw_lines = [
        clean_text(line)
        for line in (page_text or "").split("\n")
        if clean_text(line)
    ]
    reporter_pattern = re.compile(r"\[대한경제=[^\]]+?\s*기자\]")

    # 본문 시작을 확실히 찾을 수 있으면 바로 그 위치부터 추출한다.
    reporter_start = next(
        (i for i, line in enumerate(raw_lines) if reporter_pattern.search(line)),
        None,
    )

    paragraphs = []
    collecting = False

    for index, line in enumerate(raw_lines):
        if not collecting:
            if reporter_start is not None:
                if index < reporter_start:
                    continue
                collecting = True
            else:
                # 기사 마커가 없는 페이지에 대한 기존 fallback을 보존한다.
                if re.search(
                    r"기사\s*입력\s*[:：]?\s*\d{4}[-./]\d{1,2}[-./]\d{1,2}"
                    r"\s*\d{1,2}:\d{2}", line
                ):
                    collecting = True
                continue

        if "ⓒ 대한경제신문" in line or "본문 글자크기" in line:
            break

        if is_junk_paragraph(line) or is_reporter_line(line):
            continue

        if title and clean_text(line) == clean_text(title):
            continue

        if image_caption and clean_text(line) == clean_text(image_caption):
            continue

        # 본문의 맨 앞 기자 표기는 최종 출력 시 별도로 붙이므로 제거한다.
        line = reporter_pattern.sub("", line, count=1).strip()

        if len(line) < 15:
            continue

        if re.fullmatch(r"[A-Za-z0-9._-]+@(?:dnews\.co\.kr)?", line):
            continue

        paragraphs.append(line)

    return deduplicate_paragraphs(paragraphs)


def extract_paragraphs(soup, page_text, title, image_caption):
    """HTML 문단과 화면 텍스트를 비교하여 원문 보존도가 높은 쪽을 사용한다.

    대한경제에서는 하나의 p 태그가 여러 문단을 합쳐 담는 경우가 있어
    HTML 추출 결과가 존재하더라도 곧바로 반환하면 본문이 누락된다.
    """
    html_paragraphs = extract_paragraphs_from_reporter_marker(soup)
    text_paragraphs = extract_paragraphs_from_page_text(
        page_text, title, image_caption
    )

    if not html_paragraphs:
        return text_paragraphs

    if not text_paragraphs:
        return html_paragraphs

    html_chars = sum(len(paragraph) for paragraph in html_paragraphs)
    text_chars = sum(len(paragraph) for paragraph in text_paragraphs)

    # 텍스트 추출에서 문단이 더 세분화되고, 내용도 충분히 보존될 때
    # HTML의 합쳐진 문단보다 텍스트 결과를 우선한다.
    if (
        len(text_paragraphs) > len(html_paragraphs)
        and text_chars >= html_chars * 0.8
    ):
        return text_paragraphs

    if text_chars > html_chars and len(text_paragraphs) >= len(html_paragraphs):
        return text_paragraphs

    return html_paragraphs


def fetch_article_response(session, url):
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response
    except requests.RequestException as first_error:
        fallback_url = desktop_fallback_url(url)
        if not fallback_url:
            raise first_error
        print("[대한경제] 모바일 상세페이지 실패 → PC 페이지 재시도")
        response = session.get(fallback_url, timeout=30)
        response.raise_for_status()
        return response


def collect_dnews_article(url, session=None, listing_date=None):
    own_session = session is None
    if own_session:
        session = create_session()
    try:
        response = fetch_article_response(session, url)
        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text("\n", strip=True)
        title = extract_title(soup)
        image_caption = extract_image_caption(soup, page_text)
        paragraphs = extract_paragraphs(soup, page_text, title, image_caption)

        # 상세페이지 입력일만 최종 날짜로 사용한다.
        # 목록 및 URL 시각은 비교·탐색용 정보일 뿐 대체값이 아니다.
        page_published_at = extract_published_at(page_text)
        url_published_at = extract_published_at_from_url(url)
        published_at = page_published_at

        if page_published_at and url_published_at:
            if published_date_from_string(page_published_at) != published_date_from_string(
                url_published_at
            ):
                print(
                    "[대한경제] 표시일/URL일 불일치 → 상세페이지 우선 | "
                    f"표시 {page_published_at} | URL {url_published_at} | {title}"
                )
        elif not page_published_at:
            print(
                "[WARNING] 대한경제 상세 표시일 미확인 → 날짜 대체 없이 제외 | "
                f"{title} | {url}"
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

        article.subheadings = (
            extract_dnews_prebody_subheadings(soup, article)
            or extract_page_subheadings(soup, article)
        )

        # 모바일 기사에서 부제목을 찾지 못한 경우
        # 같은 기사의 PC 원문을 한 번 더 확인한다.
        if not article.subheadings:
            pc_url = desktop_fallback_url(url)

            if pc_url and pc_url != response.url:
                try:
                    pc_response = session.get(
                        pc_url, timeout=20
                    )
                    pc_response.raise_for_status()

                    pc_soup = BeautifulSoup(
                        pc_response.text, "html.parser"
                    )

                    pc_article = Article(
                        source=article.source,
                        title=extract_title(pc_soup) or article.title,
                        reporter=article.reporter,
                        published_at=article.published_at,
                        image_caption=article.image_caption,
                        paragraphs=article.paragraphs,
                        url=pc_url,
                    )

                    article.subheadings = (
                        extract_dnews_prebody_subheadings(
                            pc_soup, pc_article
                        )
                        or extract_page_subheadings(
                            pc_soup, pc_article
                        )
                    )

                except requests.RequestException as exc:
                    print(
                        f"[대한경제 SUBTITLE WARNING] {exc}"
                    )

        if article.subheadings:
            print(
                "[대한경제 SUBTITLE] "
                + " / ".join(article.subheadings)
            )

        return article
    finally:
        if own_session:
            session.close()


def is_in_collection_period(article, start_date, end_date):
    article_date = published_date_from_string(article.published_at)
    return bool(article_date and start_date <= article_date <= end_date)


def collect_dnews_articles(start_date, end_date):
    session = create_session()
    try:
        candidates = collect_dnews_article_candidates(
            start_date=start_date, end_date=end_date, session=session
        )
        articles = []
        for candidate in candidates:
            url = candidate["url"]
            try:
                article = collect_dnews_article(
                    url=url, session=session, listing_date=candidate.get("listing_date")
                )
                if not article.title:
                    print(f"[WARNING] 대한경제 제목 누락: {url}")
                    continue
                if not article.published_at:
                    print(f"[WARNING] 대한경제 날짜 누락: {url}")
                    continue
                if not article.paragraphs:
                    print(f"[WARNING] 대한경제 본문 누락: {url}")
                    continue
                if not is_in_collection_period(article, start_date, end_date):
                    print(
                        "[대한경제] 표시 등록일 기준 기간 외 제외 | "
                        f"{article.published_at} | {article.title}"
                    )
                    continue
                articles.append(article)
            except requests.RequestException as exc:
                print(f"[WARNING] 대한경제 기사 수집 실패: {url} | {exc}")
            except Exception as exc:
                print(f"[WARNING] 대한경제 기사 파싱 실패: {url} | {exc}")
        articles.sort(key=lambda article: article.published_at or "", reverse=True)
        print(f"[대한경제] 상세 표시일 기준 기간 내 기사 {len(articles)}개")
        return articles
    finally:
        session.close()


class DNewsCollector(BaseNewsCollector):
    source_name = SOURCE["name"]

    def collect_articles(self, start_date, end_date):
        return collect_dnews_articles(start_date=start_date, end_date=end_date)