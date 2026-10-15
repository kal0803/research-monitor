import html
import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import (
    parse_qsl,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)

from collectors.article_subheading import (extract_article_prebody_subheadings, extract_page_subheadings)
from collectors.base import BaseNewsCollector
from config import NEWS_SOURCES
from models import Article


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    )
}


class GenericNewsCollector(
    BaseNewsCollector
):

    source_key = None

    article_link_markers = (
        "articleView.html?idxno=",
    )

    excluded_title_terms = ()

    pagination_enabled = False

    max_pages = 50

    old_page_stop_streak = 2

    request_timeout = 30

    def __init__(self):

        if not self.source_key:
            raise ValueError(
                "source_key가 설정되지 않았습니다."
            )

        source = NEWS_SOURCES[
            self.source_key
        ]

        self.source_name = source[
            "name"
        ]

        self.base_url = source[
            "base_url"
        ]

        self.section_url = source[
            "section_url"
        ]

        self.session = requests.Session()

        self.session.headers.update(
            HEADERS
        )

        self._listing_metadata = {}

        self._active_start_date = None
        self._active_end_date = None

    # ========================================================
    # TEXT CLEANUP
    # ========================================================

    def clean_extracted_text(
        self,
        value,
    ):

        if value is None:
            return ""

        text = str(
            value
        )

        text = html.unescape(
            text
        )

        text = html.unescape(
            text
        )

        text = (
            text
            .replace(
                "\ufeff",
                ""
            )
            .replace(
                "\u200b",
                ""
            )
            .replace(
                "\u200c",
                ""
            )
            .replace(
                "\u200d",
                ""
            )
            .replace(
                "\u2060",
                ""
            )
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        return text

    def clean_text(
        self,
        value,
    ):
        """
        기존 site-specific collector들과의
        backward compatibility용 helper.

        cenews.py / safetynews.py에서
        self.clean_text(...)를 사용한다.
        """

        return self.clean_extracted_text(
            value
        )

    # ========================================================
    # HTTP / ENCODING
    # ========================================================

    def get_response(
        self,
        url,
    ):

        response = self.session.get(
            url,
            timeout=self.request_timeout,
        )

        response.raise_for_status()

        return response

    def decode_response(
        self,
        response,
    ):

        content = response.content

        candidates = []

        response_encoding = (
            response.encoding
            or ""
        ).strip()

        apparent_encoding = (
            response.apparent_encoding
            or ""
        ).strip()

        if (
            response_encoding
            and response_encoding.lower()
            not in (
                "iso-8859-1",
                "ascii",
            )
        ):

            candidates.append(
                response_encoding
            )

        if apparent_encoding:

            candidates.append(
                apparent_encoding
            )

        candidates.extend(
            [
                "utf-8",
                "cp949",
                "euc-kr",
            ]
        )

        seen = set()

        for encoding in candidates:

            normalized = (
                encoding
                .lower()
                .replace(
                    "_",
                    "-",
                )
            )

            if normalized in seen:
                continue

            seen.add(
                normalized
            )

            try:

                text = content.decode(
                    encoding
                )

            except (
                UnicodeDecodeError,
                LookupError,
            ):
                continue

            replacement_count = (
                text.count(
                    "�"
                )
            )

            if replacement_count <= 2:
                return text

        return content.decode(
            "utf-8",
            errors="replace",
        )

    def get_soup(
        self,
        url,
    ):

        response = self.get_response(
            url
        )

        html_text = self.decode_response(
            response
        )

        return BeautifulSoup(
            html_text,
            "html.parser",
        )

    # ========================================================
    # PAGINATION
    # ========================================================

    def build_page_url(
        self,
        page,
    ):

        if not self.pagination_enabled:
            return self.section_url

        parts = urlsplit(
            self.section_url
        )

        query_items = dict(
            parse_qsl(
                parts.query,
                keep_blank_values=True,
            )
        )

        query_items[
            "page"
        ] = str(
            page
        )

        new_query = urlencode(
            query_items
        )

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                new_query,
                parts.fragment,
            )
        )

    # ========================================================
    # DATE HELPERS
    # ========================================================

    def normalize_datetime(
        self,
        raw,
    ):

        if not raw:
            return None

        text = self.clean_extracted_text(
            raw
        )

        text = (
            text
            .replace(
                "년",
                "-"
            )
            .replace(
                "월",
                "-"
            )
            .replace(
                "일",
                ""
            )
            .replace(
                ".",
                "-"
            )
            .replace(
                "/",
                "-"
            )
        )

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]

        for fmt in formats:

            try:

                parsed = datetime.strptime(
                    text,
                    fmt,
                )

                return parsed.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

            except ValueError:
                continue

        return None

    def published_at_to_date(
        self,
        published_at,
    ):

        if not published_at:
            return None

        try:

            return datetime.strptime(
                published_at,
                "%Y-%m-%d %H:%M:%S",
            ).date()

        except ValueError:
            return None

    # ========================================================
    # LISTING DATE
    # ========================================================

    def extract_date_from_text(
        self,
        text,
    ):

        if not text:
            return None

        text = self.clean_extracted_text(
            text
        )

        patterns = [
            (
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
            (
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r")"
            ),
            (
                r"("
                r"\d{4}\s*년\s*"
                r"\d{1,2}\s*월\s*"
                r"\d{1,2}\s*일"
                r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
                r")"
            ),
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
            )

            if not match:
                continue

            normalized = (
                self.normalize_datetime(
                    match.group(
                        1
                    )
                )
            )

            if normalized:
                return normalized

        return None

    def extract_listing_date(
        self,
        link,
    ):

        current = link

        for _ in range(
            5
        ):

            if current is None:
                break

            try:

                text = current.get_text(
                    " ",
                    strip=True,
                )

            except Exception:
                text = ""

            published_at = (
                self.extract_date_from_text(
                    text
                )
            )

            if published_at:
                return published_at

            current = current.parent

        return None

    # ========================================================
    # LIST PAGE
    # ========================================================

    def extract_listing_entries(
        self,
        soup,
        page_url,
    ):

        entries = []

        seen_urls = set()

        for link in soup.find_all(
            "a",
            href=True,
        ):

            href = (
                link.get(
                    "href"
                )
                or ""
            ).strip()

            if not any(
                marker in href
                for marker
                in self.article_link_markers
            ):
                continue

            link_text = (
                self.clean_extracted_text(
                    link.get_text(
                        " ",
                        strip=True,
                    )
                )
            )

            if any(
                term in link_text
                for term
                in self.excluded_title_terms
            ):
                continue

            full_url = urljoin(
                page_url,
                href,
            )

            if full_url in seen_urls:
                continue

            seen_urls.add(
                full_url
            )

            published_at = (
                self.extract_listing_date(
                    link
                )
            )

            entries.append(
                {
                    "url":
                        full_url,

                    "title":
                        link_text,

                    "published_at":
                        published_at,
                }
            )

        return entries

    def collect_article_urls(
        self,
        start_date=None,
        end_date=None,
    ):

        self._listing_metadata = {}

        if start_date is None:
            start_date = (
                self._active_start_date
            )

        if end_date is None:
            end_date = (
                self._active_end_date
            )

        urls = []

        seen_urls = set()

        old_page_streak = 0

        if self.pagination_enabled:

            page_numbers = range(
                1,
                self.max_pages + 1,
            )

        else:

            page_numbers = [
                1
            ]

        for page in page_numbers:

            page_url = (
                self.build_page_url(
                    page
                )
            )

            try:

                soup = self.get_soup(
                    page_url
                )

            except Exception as error:

                print(
                    f"[WARNING] "
                    f"{self.source_name} "
                    f"page {page} 요청 실패"
                )

                print(
                    f"  URL: {page_url}"
                )

                print(
                    f"  {error}"
                )

                if not self.pagination_enabled:
                    break

                continue

            entries = (
                self.extract_listing_entries(
                    soup=soup,
                    page_url=page_url,
                )
            )

            if not entries:

                print(
                    f"[{self.source_name}] "
                    f"page {page}: "
                    "기사 URL 없음"
                )

                break

            known_dates = []

            matched_count = 0

            missing_date_count = 0

            for entry in entries:

                url = entry[
                    "url"
                ]

                published_at = (
                    entry.get(
                        "published_at"
                    )
                )

                published_date = (
                    self.published_at_to_date(
                        published_at
                    )
                )

                if published_date:

                    known_dates.append(
                        published_date
                    )

                else:

                    missing_date_count += 1

                self._listing_metadata[
                    url
                ] = {
                    "title":
                        entry.get(
                            "title",
                            "",
                        ),

                    "published_at":
                        published_at,
                }

                include = True

                if (
                    start_date is not None
                    and end_date is not None
                    and published_date is not None
                ):

                    include = (
                        start_date
                        <= published_date
                        <= end_date
                    )

                if include:

                    if url not in seen_urls:

                        seen_urls.add(
                            url
                        )

                        urls.append(
                            url
                        )

                    if (
                        published_date is not None
                        and start_date is not None
                        and end_date is not None
                        and start_date
                        <= published_date
                        <= end_date
                    ):

                        matched_count += 1

            if known_dates:

                minimum_date = min(
                    known_dates
                )

                maximum_date = max(
                    known_dates
                )

                print(
                    f"[{self.source_name}] "
                    f"page {page}: "
                    f"{minimum_date} ~ "
                    f"{maximum_date} | "
                    f"기사 {len(entries)}개 | "
                    f"기간 일치 "
                    f"{matched_count}개"
                )

            else:

                print(
                    f"[WARNING] "
                    f"{self.source_name} "
                    f"page {page}: "
                    "목록 날짜를 하나도 "
                    "찾지 못했습니다."
                )

                print(
                    f"  URL: {page_url}"
                )

            if missing_date_count:

                print(
                    f"[{self.source_name}] "
                    f"page {page}: "
                    f"목록 날짜 누락 "
                    f"{missing_date_count}개"
                )

            if (
                self.pagination_enabled
                and start_date is not None
                and known_dates
            ):

                page_is_old = (
                    max(
                        known_dates
                    )
                    < start_date
                )

                if page_is_old:

                    old_page_streak += 1

                else:

                    old_page_streak = 0

                if (
                    old_page_streak
                    >= self.old_page_stop_streak
                ):

                    print(
                        f"[{self.source_name}] "
                        "요청 기간보다 과거인 "
                        f"페이지가 "
                        f"{old_page_streak}개 "
                        "연속 확인되어 "
                        "탐색 종료"
                    )

                    break

            if not self.pagination_enabled:
                break

        print(
            f"[{self.source_name}] "
            f"요청 기간 기사 URL "
            f"{len(urls)}개 확보"
        )

        return urls

    # ========================================================
    # DETAIL TITLE
    # ========================================================

    def extract_title(
        self,
        soup,
    ):

        og_title = soup.find(
            "meta",
            property="og:title",
        )

        if og_title:

            title = (
                self.clean_extracted_text(
                    og_title.get(
                        "content",
                        ""
                    )
                )
            )

            if title:
                return title

        selectors = [
            ".article-view-header h1",
            ".article-view-header h2",
            ".article-head-title",
            ".article-title",
            ".view-title",
            "h1",
            "h2",
            "h3",
        ]

        for selector in selectors:

            element = (
                soup.select_one(
                    selector
                )
            )

            if not element:
                continue

            text = (
                self.clean_extracted_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )
            )

            if text:
                return text

        if soup.title:

            return self.clean_extracted_text(
                soup.title.get_text(
                    " ",
                    strip=True,
                )
            )

        return ""

    # ========================================================
    # DETAIL DATE
    # ========================================================

    def extract_published_at(
        self,
        page_text,
    ):

        page_text = (
            self.clean_extracted_text(
                page_text
            )
        )

        patterns = [
            (
                r"기사입력\s*[:：]?\s*"
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
            (
                r"기사승인\s*[:：]?\s*"
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
            (
                r"승인\s*[:：]?\s*"
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
            (
                r"입력\s*[:：]?\s*"
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
            (
                r"등록\s*[:：]?\s*"
                r"("
                r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
            (
                r"("
                r"\d{4}\s*년\s*"
                r"\d{1,2}\s*월\s*"
                r"\d{1,2}\s*일"
                r"\s+\d{1,2}:\d{2}(?::\d{2})?"
                r")"
            ),
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                page_text,
            )

            if not match:
                continue

            normalized = (
                self.normalize_datetime(
                    match.group(
                        1
                    )
                )
            )

            if normalized:
                return normalized

        return None

    # ========================================================
    # REPORTER
    # ========================================================

    def extract_reporter(
        self,
        page_text,
    ):

        page_text = (
            self.clean_extracted_text(
                page_text
            )
        )

        patterns = [
            (
                r"기자명\s*[:：]?\s*"
                r"([가-힣]{2,5})\s*기자"
            ),
            (
                r"\([^)]+\)\s*"
                r"([가-힣]{2,5})\s*기자\s*="
            ),
            (
                r"([가-힣]{2,5})\s*기자\s*="
            ),
            (
                r"([가-힣]{2,5})\s*기자"
                r"\s+다른\s*기사"
            ),
            (
                r"([가-힣]{2,5})\s*기자"
            ),
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                page_text,
            )

            if not match:
                continue

            return (
                self.clean_extracted_text(
                    match.group(
                        1
                    )
                )
            )

        return None

    # ========================================================
    # CAPTION
    # ========================================================

    def extract_caption(
        self,
        soup,
    ):

        for figcaption in soup.find_all(
            "figcaption"
        ):

            text = (
                self.clean_extracted_text(
                    figcaption.get_text(
                        " ",
                        strip=True,
                    )
                )
            )

            if text:
                return text

        caption_selectors = [
            ".photo-caption",
            ".image-caption",
            ".img-caption",
            ".caption",
        ]

        for selector in caption_selectors:

            element = (
                soup.select_one(
                    selector
                )
            )

            if not element:
                continue

            text = (
                self.clean_extracted_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )
            )

            if text:
                return text

        return None

    # ========================================================
    # BODY
    # ========================================================

    def extract_paragraphs(
        self,
        soup,
    ):

        candidates = [
            "#article-view-content-div",
            ".article-view-content-div",
            "#articleBody",
            "#article-body",
            "#articleBodyContents",
            ".article-body",
            ".article_body",
            ".article-view-body",
            ".article-content",
            ".view-content",
            ".news-body",
            ".news_text",
            ".news-content",
            "article",
        ]

        container = None

        for selector in candidates:

            container = soup.select_one(
                selector
            )

            if container:
                break

        if container is None:
            container = soup

        paragraphs = []

        elements = container.find_all(
            "p"
        )

        if not elements:

            elements = container.find_all(
                "div"
            )

        for element in elements:

            text = (
                self.clean_extracted_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )
            )

            if not text:
                continue

            if len(text) < 15:
                continue

            if any(
                junk in text
                for junk in (
                    "저작권자",
                    "무단전재",
                    "재배포금지",
                    "재배포 금지",
                    "다른기사 보기",
                    "다른 기사보기",
                    "기자의 다른기사",
                    "기사공유하기",
                    "기사 공유하기",
                    "댓글",
                    "스크롤 이동",
                    "본문 글씨",
                    "본문 글자크기",
                    "개인정보처리방침",
                    "청소년보호정책",
                )
            ):
                continue

            paragraphs.append(
                text
            )

        result = []

        seen = set()

        for paragraph in paragraphs:

            if paragraph in seen:
                continue

            seen.add(
                paragraph
            )

            result.append(
                paragraph
            )

        return result

    # ========================================================
    # DATE SELECTION
    # ========================================================

    def choose_published_at(
        self,
        url,
        detail_published_at,
    ):

        metadata = (
            self._listing_metadata.get(
                url,
                {}
            )
        )

        listing_published_at = (
            metadata.get(
                "published_at"
            )
        )

        if (
            detail_published_at
            and listing_published_at
        ):

            detail_date = (
                self.published_at_to_date(
                    detail_published_at
                )
            )

            listing_date = (
                self.published_at_to_date(
                    listing_published_at
                )
            )

            if (
                detail_date
                and listing_date
                and detail_date
                == listing_date
            ):

                return detail_published_at

            if (
                detail_date
                and listing_date
                and detail_date
                != listing_date
            ):

                print(
                    f"[{self.source_name}] "
                    "날짜 불일치 | "
                    f"목록 {listing_published_at} | "
                    f"상세 {detail_published_at} | "
                    f"{url}"
                )

                return listing_published_at

        if detail_published_at:
            return detail_published_at

        if listing_published_at:

            print(
                f"[{self.source_name}] "
                "상세 날짜 누락 → "
                "목록 날짜 사용 | "
                f"{listing_published_at} | "
                f"{url}"
            )

            return listing_published_at

        return None

    # ========================================================
    # DETAIL ARTICLE
    # ========================================================

    def collect_article(
        self,
        url,
    ):

        soup = self.get_soup(
            url
        )

        page_text = soup.get_text(
            "\n",
            strip=True,
        )

        detail_published_at = (
            self.extract_published_at(
                page_text
            )
        )

        published_at = (
            self.choose_published_at(
                url=url,
                detail_published_at=detail_published_at,
            )
        )

        article = Article(
            source=self.source_name,

            title=self.extract_title(
                soup
            ),

            reporter=self.extract_reporter(
                page_text
            ),

            published_at=published_at,

            image_caption=self.extract_caption(
                soup
            ),

            paragraphs=self.extract_paragraphs(
                soup
            ),

            url=url,
        )


        # 이미지 수집 단계에 의존하지 않고 소제목을 저장.
        if self.source_name in ("건설타임즈", "안전신문"):
            article.subheadings = (
                extract_article_prebody_subheadings(soup, article)
                or extract_page_subheadings(soup, article)
            )

        return article

    # ========================================================
    # PERIOD FILTER
    # ========================================================

    def article_in_period(
        self,
        article,
        start_date,
        end_date,
    ):

        published_date = (
            self.published_at_to_date(
                article.published_at
            )
        )

        if published_date is None:
            return False

        return (
            start_date
            <= published_date
            <= end_date
        )

    # ========================================================
    # MAIN COLLECTION
    # ========================================================

    def collect_articles(
        self,
        start_date,
        end_date,
    ):

        self._active_start_date = (
            start_date
        )

        self._active_end_date = (
            end_date
        )

        try:

            urls = (
                self.collect_article_urls(
                    start_date=start_date,
                    end_date=end_date,
                )
            )

        finally:

            self._active_start_date = None
            self._active_end_date = None

        articles = []

        for url in urls:

            try:

                article = (
                    self.collect_article(
                        url
                    )
                )

                if any(
                    term in (
                        article.title
                        or ""
                    )
                    for term
                    in self.excluded_title_terms
                ):

                    continue

                if not article.published_at:

                    print(
                        f"[WARNING] "
                        f"{self.source_name} "
                        "날짜 누락: "
                        f"{url}"
                    )

                    continue

                if self.article_in_period(
                    article=article,
                    start_date=start_date,
                    end_date=end_date,
                ):

                    articles.append(
                        article
                    )

            except Exception as error:

                print(
                    f"[WARNING] "
                    f"{self.source_name} "
                    f"기사 처리 실패: "
                    f"{url}"
                )

                print(
                    f"  {error}"
                )

        articles.sort(
            key=lambda article:
                article.published_at
                or "",
            reverse=True,
        )

        print(
            f"[{self.source_name}] "
            f"기간 내 기사 "
            f"{len(articles)}개"
        )

        return articles
