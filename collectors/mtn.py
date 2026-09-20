import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from collectors.base import BaseNewsCollector
from config import NEWS_SOURCES
from models import Article


SOURCE = NEWS_SOURCES["mtn"]

BASE_URL = "https://news.mtn.co.kr/"

SECTION_URL = (
    "https://news.mtn.co.kr/"
    "category-news/M0300"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def clean_text(text):
    if not text:
        return ""

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def collect_mtn_article_urls():
    """
    MTN 기사 목록은 JavaScript로 렌더링되므로
    Playwright를 사용해 기사 링크를 수집한다.
    """

    urls = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1440,
                "height": 1000,
            }
        )

        page.goto(
            SECTION_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        try:
            page.wait_for_selector(
                'a[href*="/news-detail/"]',
                timeout=15000,
            )

        except Exception:
            pass

        links = page.locator(
            'a[href*="/news-detail/"]'
        )

        count = links.count()

        for index in range(count):

            href = links.nth(
                index
            ).get_attribute(
                "href"
            )

            if not href:
                continue

            full_url = urljoin(
                BASE_URL,
                href
            )

            if full_url not in urls:
                urls.append(full_url)

        browser.close()

    return urls


def extract_title(soup):
    og_title = soup.find(
        "meta",
        property="og:title"
    )

    if og_title:

        title = clean_text(
            og_title.get(
                "content",
                ""
            )
        )

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
            h1.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    if soup.title:

        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
        )

        return re.sub(
            r"\s*[-|]\s*MTN.*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()

    return ""


def extract_published_at(page_text):
    """
    MTN 기사 발행일 추출.
    """

    match = re.search(
        r"(\d{4}-\d{2}-\d{2}"
        r"\s+\d{2}:\d{2}:\d{2})",
        page_text,
    )

    if match:
        return match.group(1)

    return None


def extract_reporter(page_text):
    """
    MTN 기자명 추출.
    """

    match = re.search(
        r"([가-힣]{2,4})\s*기자"
        r"\s+\d{4}-\d{2}-\d{2}",
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


def find_article_container(soup):
    """
    기사 본문 영역 탐색.
    """

    selectors = [
        "article",
        ".article-body",
        ".article_body",
        ".article-content",
        ".article_content",
        ".news-body",
        ".news_body",
        ".news-content",
        ".news_content",
    ]

    for selector in selectors:

        elements = soup.select(
            selector
        )

        for element in elements:

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 150:
                return element

    return None


def extract_caption(
    soup,
    container,
):
    """
    기사 이미지 캡션 추출.
    """

    if container:

        figcaption = container.find(
            "figcaption"
        )

        if figcaption:

            text = clean_text(
                figcaption.get_text(
                    " ",
                    strip=True
                )
            )

            if text:
                return text

    for img in soup.find_all("img"):

        parent = img.parent

        if not parent:
            continue

        text = clean_text(
            parent.get_text(
                " ",
                strip=True
            )
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


def extract_paragraphs(
    soup,
    container,
):
    """
    기사 본문 문단 추출.
    """

    search_root = (
        container
        if container is not None
        else soup
    )

    paragraphs = []

    for tag in search_root.find_all(
        "p"
    ):

        text = clean_text(
            tag.get_text(
                " ",
                strip=True
            )
        )

        if len(text) < 15:
            continue

        if any(
            junk in text
            for junk in (
                "저작권자",
                "무단전재",
                "재배포",
                "반론",
                "정정보도",
                "고충처리인",
                "MTN 기자실",
                "많이본뉴스",
                "많이 본 뉴스",
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


def collect_mtn_article(url):
    """
    MTN 기사 하나를 Article 객체로 변환한다.
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

    container = find_article_container(
        soup
    )

    return Article(
        source=SOURCE["name"],
        title=extract_title(
            soup
        ),
        reporter=extract_reporter(
            page_text
        ),
        published_at=extract_published_at(
            page_text
        ),
        image_caption=extract_caption(
            soup,
            container,
        ),
        paragraphs=extract_paragraphs(
            soup,
            container,
        ),
        url=url,
    )


def is_in_period(
    article,
    start_date,
    end_date,
):
    if not article.published_at:
        return False

    try:
        article_date = datetime.strptime(
            article.published_at,
            "%Y-%m-%d %H:%M:%S"
        ).date()

    except ValueError:
        return False

    return (
        start_date
        <= article_date
        <= end_date
    )


def collect_mtn_articles(
    start_date,
    end_date,
):
    urls = collect_mtn_article_urls()

    articles = []

    for url in urls:

        try:

            article = collect_mtn_article(
                url
            )

            if not article.published_at:
                continue

            if not is_in_period(
                article,
                start_date,
                end_date,
            ):
                continue

            articles.append(article)

        except Exception as e:

            print(
                "[WARNING] "
                f"MTN 기사 처리 실패: {url}"
            )

            print(e)

    articles.sort(
        key=lambda article:
            article.published_at or "",
        reverse=True,
    )

    return articles


class MTNCollector(
    BaseNewsCollector
):

    source_name = SOURCE["name"]

    def collect_articles(
        self,
        start_date,
        end_date,
    ):
        return collect_mtn_articles(
            start_date=start_date,
            end_date=end_date,
        )