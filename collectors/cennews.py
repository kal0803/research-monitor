import re
import requests

from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin

from collectors.base import BaseNewsCollector
from config import NEWS_SOURCES
from models import Article


SOURCE = NEWS_SOURCES["cennews"]

SECTION_URL = (
    "http://www.cennews.co.kr/"
    "news/articleList.html?"
    "sc_sub_section_code=S2N1"
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


def collect_cennews_article_urls():
    """
    건설산업신문 건설 섹션의
    기사 URL을 수집한다.
    """

    response = requests.get(
        SECTION_URL,
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

        href = link.get(
            "href",
            ""
        )

        if "articleView.html?idxno=" not in href:
            continue

        full_url = urljoin(
            SECTION_URL,
            href
        )

        if full_url not in urls:
            urls.append(full_url)

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
                r"\s*-\s*건설산업신문\s*$",
                "",
                title
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
            r"\s*-\s*건설산업신문\s*$",
            "",
            title
        ).strip()

    return ""


def extract_published_at(
    soup,
    page_text,
):
    """
    기사 날짜 추출.
    """

    meta_candidates = [
        soup.find(
            "meta",
            property="article:published_time"
        ),
        soup.find(
            "meta",
            attrs={
                "name":
                "article:published_time"
            }
        ),
        soup.find(
            "meta",
            attrs={
                "name":
                "date"
            }
        ),
    ]

    for tag in meta_candidates:

        if not tag:
            continue

        value = tag.get(
            "content",
            ""
        ).strip()

        if not value:
            continue

        match = re.search(
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
            r".*?"
            r"(\d{1,2}):(\d{2})(?::(\d{2}))?",
            value,
        )

        if match:

            parsed = datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6) or 0),
            )

            return parsed.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

    normalized = page_text.replace(
        "\xa0",
        " "
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized
    )

    patterns = [
        (
            r"기사승인.*?"
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
            r"\s+"
            r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
        ),
        (
            r"승인.*?"
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
            r"\s+"
            r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
        ),
        (
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})"
            r"\s+"
            r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
        ),
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            normalized
        )

        if not match:
            continue

        parsed = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6) or 0),
        )

        return parsed.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    return None


def extract_reporter(page_text):
    """
    기자명 추출.
    """

    match = re.search(
        r"([가-힣]{2,4})\s*기자",
        page_text
    )

    if match:
        return match.group(1).strip()

    return None


def find_article_container(soup):
    """
    기사 본문 영역 탐색.
    """

    selectors = [
        "#article-view-content-div",
        ".article-view-content-div",
        "#articleBody",
        ".article-body",
        ".article_body",
        ".article-content",
        ".article_content",
        "article",
    ]

    for selector in selectors:

        element = soup.select_one(
            selector
        )

        if element:

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 100:
                return element

    return None


def extract_caption(container):
    """
    이미지 캡션 추출.
    """

    if container is None:
        return None

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

    for img in container.find_all(
        "img"
    ):

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
                term in text
                for term in (
                    "사진",
                    "제공",
                    "조감도",
                    "이미지",
                )
            )
        ):
            return text

    return None


def extract_paragraphs(container):
    """
    기사 본문 문단 추출.
    """

    if container is None:
        return []

    paragraphs = []

    for tag in container.find_all(
        ["p", "div"]
    ):

        if (
            tag.name == "div"
            and tag.find(
                ["p", "div"],
                recursive=False
            )
        ):
            continue

        text = clean_text(
            tag.get_text(
                " ",
                strip=True
            )
        )

        if len(text) < 10:
            continue

        if any(
            junk in text
            for junk in (
                "저작권자",
                "무단전재",
                "재배포금지",
                "폰트키우기",
                "폰트줄이기",
                "프린트하기",
                "메일보내기",
                "신고하기",
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


def collect_cennews_article(url):
    """
    기사 하나를 Article 객체로 변환한다.
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
            soup,
            page_text,
        ),
        image_caption=extract_caption(
            container
        ),
        paragraphs=extract_paragraphs(
            container
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


def collect_cennews_articles(
    start_date,
    end_date,
):
    urls = collect_cennews_article_urls()

    articles = []

    for url in urls:

        try:

            article = (
                collect_cennews_article(
                    url
                )
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
                "건설산업신문 기사 처리 실패: "
                f"{url}"
            )

            print(e)

    articles.sort(
        key=lambda article:
            article.published_at or "",
        reverse=True,
    )

    return articles


class CENNewsCollector(
    BaseNewsCollector
):

    source_name = SOURCE["name"]

    def collect_articles(
        self,
        start_date,
        end_date,
    ):
        return collect_cennews_articles(
            start_date=start_date,
            end_date=end_date,
        )
    