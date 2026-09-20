import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin

from collectors.base import BaseNewsCollector
from config import NEWS_SOURCES
from models import Article


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    )
}


class GenericNewsCollector(BaseNewsCollector):

    source_key = None

    article_link_markers = (
        "articleView.html?idxno=",
    )

    excluded_title_terms = ()

    def __init__(self):
        source = NEWS_SOURCES[self.source_key]

        self.source_name = source["name"]
        self.base_url = source["base_url"]
        self.section_url = source["section_url"]

    def collect_article_urls(self):
        """
        해당 사이트의 섹션 페이지에서
        기사 URL을 수집한다.
        """

        response = requests.get(
            self.section_url,
            headers=HEADERS,
            timeout=30,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        urls = []

        for link in soup.find_all(
            "a",
            href=True
        ):
            href = link["href"]

            if not any(
                marker in href
                for marker in self.article_link_markers
            ):
                continue

            link_text = link.get_text(
                " ",
                strip=True
            )

            if any(
                term in link_text
                for term in self.excluded_title_terms
            ):
                continue

            full_url = urljoin(
                self.section_url,
                href
            )

            if full_url not in urls:
                urls.append(full_url)

        return urls

    def extract_title(self, soup):
        """
        기사 제목 추출.
        """

        og_title = soup.find(
            "meta",
            property="og:title"
        )

        if og_title:
            title = og_title.get(
                "content",
                ""
            ).strip()

            if title:
                return title

        for tag in (
            "h1",
            "h2",
            "h3",
        ):
            element = soup.find(tag)

            if element:
                text = element.get_text(
                    " ",
                    strip=True
                )

                if text:
                    return text

        if soup.title:
            return soup.title.get_text(
                " ",
                strip=True
            )

        return ""

    def extract_published_at(
        self,
        page_text
    ):
        """
        여러 뉴스 사이트에서 사용하는
        기사 작성 날짜 형식을 처리한다.
        """

        patterns = [
            (
                r"입력\s*"
                r"(\d{4}\.\d{2}\.\d{2}"
                r"\s+\d{2}:\d{2}(?::\d{2})?)"
            ),
            (
                r"승인\s*"
                r"(\d{4}\.\d{2}\.\d{2}"
                r"\s+\d{2}:\d{2}(?::\d{2})?)"
            ),
            (
                r"기사승인\s*"
                r"(\d{4}\.\d{2}\.\d{2}"
                r"\s+\d{2}:\d{2}(?::\d{2})?)"
            ),
            (
                r"기사입력\s*"
                r"(\d{4}-\d{2}-\d{2}"
                r"\s+\d{2}:\d{2}(?::\d{2})?)"
            ),
            (
                r"입력\s*"
                r"(\d{4}-\d{2}-\d{2}"
                r"\s+\d{2}:\d{2}(?::\d{2})?)"
            ),
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                page_text
            )

            if not match:
                continue

            raw = match.group(1)

            raw = raw.replace(
                ".",
                "-"
            )

            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ]

            for fmt in formats:
                try:
                    parsed = datetime.strptime(
                        raw,
                        fmt
                    )

                    return parsed.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

                except ValueError:
                    continue

        return None

    def extract_reporter(
        self,
        page_text
    ):
        """
        기자명 추출.
        """

        patterns = [
            r"기자명\s*([가-힣]{2,4})\s*기자",
            r"\(([^\)]+)\)\s*([가-힣]{2,4})\s*기자\s*=",
            r"([가-힣]{2,4})\s*기자\s*=",
            r"([가-힣]{2,4})\s*기자\s+다른 기사",
            r"([가-힣]{2,4})\s*기자",
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                page_text
            )

            if not match:
                continue

            if len(match.groups()) >= 2:
                return match.group(2).strip()

            return match.group(1).strip()

        return None

    def extract_caption(self, soup):
        """
        이미지 캡션 추출.
        """

        for figcaption in soup.find_all(
            "figcaption"
        ):
            text = figcaption.get_text(
                " ",
                strip=True
            )

            if text:
                return text

        for img in soup.find_all("img"):
            parent = img.parent

            if not parent:
                continue

            text = parent.get_text(
                " ",
                strip=True
            )

            if (
                text
                and len(text) <= 300
                and any(
                    word in text
                    for word in (
                        "사진",
                        "제공",
                        "조감도",
                        "노선도",
                        "이미지",
                    )
                )
            ):
                return text

        return None

    def extract_paragraphs(
        self,
        soup
    ):
        """
        기사 본문의 문단들을 추출한다.
        """

        candidates = [
            "#article-view-content-div",
            ".article-view-content-div",
            "#articleBody",
            ".article-body",
            ".article_body",
            ".view-content",
            ".news-body",
            ".news_text",
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

        for element in container.find_all(
            ["p", "div"]
        ):
            text = element.get_text(
                " ",
                strip=True
            )

            text = re.sub(
                r"\s+",
                " ",
                text
            ).strip()

            if not text:
                continue

            if len(text) < 15:
                continue

            if any(
                junk in text
                for junk in (
                    "저작권자",
                    "무단전재",
                    "다른기사 보기",
                    "다른 기사보기",
                    "기사공유하기",
                    "댓글",
                    "스크롤 이동",
                    "본문 글씨",
                )
            ):
                continue

            paragraphs.append(text)

        result = []
        seen = set()

        for paragraph in paragraphs:
            if paragraph in seen:
                continue

            seen.add(paragraph)
            result.append(paragraph)

        return result

    def collect_article(
        self,
        url
    ):
        """
        기사 URL 하나를 Article 객체로 변환한다.
        """

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        page_text = soup.get_text(
            "\n",
            strip=True
        )

        return Article(
            source=self.source_name,
            title=self.extract_title(
                soup
            ),
            reporter=self.extract_reporter(
                page_text
            ),
            published_at=self.extract_published_at(
                page_text
            ),
            image_caption=self.extract_caption(
                soup
            ),
            paragraphs=self.extract_paragraphs(
                soup
            ),
            url=url,
        )

    def article_in_period(
        self,
        article,
        start_date,
        end_date,
    ):
        """
        기사 날짜가 사용자가 입력한 범위에
        포함되는지 확인한다.
        """

        if not article.published_at:
            return False

        try:
            published_date = datetime.strptime(
                article.published_at,
                "%Y-%m-%d %H:%M:%S"
            ).date()

        except ValueError:
            return False

        return (
            start_date
            <= published_date
            <= end_date
        )

    def collect_articles(
        self,
        start_date,
        end_date,
    ):
        """
        사용자가 지정한 날짜 범위의
        기사만 수집한다.
        """

        urls = self.collect_article_urls()

        articles = []

        for url in urls:
            try:
                article = self.collect_article(
                    url
                )

                if any(
                    term in article.title
                    for term in self.excluded_title_terms
                ):
                    continue

                if self.article_in_period(
                    article,
                    start_date,
                    end_date,
                ):
                    articles.append(
                        article
                    )

            except Exception as e:
                print(
                    f"[WARNING] "
                    f"{self.source_name} "
                    f"기사 처리 실패: {url}"
                )

                print(e)

        articles.sort(
            key=lambda article:
                article.published_at or "",
            reverse=True,
        )

        return articles