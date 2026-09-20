import re
import requests

from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from datetime import datetime

from collectors.base import BaseNewsCollector
from config import NEWS_SOURCES
from models import Article


SOURCE = NEWS_SOURCES["dnews"]

DNEWS_CONSTRUCTION_URL = SOURCE["section_url"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    )
}


def collect_dnews_article_urls():
    """
    대한경제 건설·부동산 섹션의 기사 URL을 수집한다.
    """

    response = requests.get(
        DNEWS_CONSTRUCTION_URL,
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

        if "view.jsp?idxno=" not in href:
            continue

        full_url = urljoin(
            DNEWS_CONSTRUCTION_URL,
            href
        )

        if full_url not in urls:
            urls.append(full_url)

    return urls


def extract_title(soup):
    """
    기사 제목을 추출한다.
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

    h1 = soup.find("h1")

    if h1:
        return h1.get_text(
            " ",
            strip=True
        )

    if soup.title:
        return soup.title.get_text(
            " ",
            strip=True
        )

    return ""


def extract_published_at(
    page_text
):
    """
    대한경제 기사 입력 날짜를 추출한다.
    """

    match = re.search(
        r"기사입력\s*"
        r"(\d{4}-\d{2}-\d{2}\s+"
        r"\d{2}:\d{2}:\d{2})",
        page_text,
    )

    if match:
        return match.group(1)

    return None


def extract_reporter(
    page_text
):
    """
    기자 이름을 추출한다.
    """

    match = re.search(
        r"\[대한경제=([^\]]+?)\s*기자\]",
        page_text,
    )

    if match:
        return match.group(1).strip()

    return None


def clean_paragraph(text):
    """
    문단의 불필요한 공백을 정리한다.
    """

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def extract_image_caption(
    soup,
    page_text,
):
    """
    기사 이미지 캡션을 추출한다.
    """

    reporter_pattern = re.compile(
        r"\[대한경제=[^\]]+?\s*기자\]"
    )

    reporter_text_node = soup.find(
        string=reporter_pattern
    )

    if reporter_text_node:
        current = reporter_text_node.parent

        for _ in range(8):
            if current is None:
                break

            previous = current.find_previous()

            if previous is None:
                break

            current = previous

            if not isinstance(
                current,
                Tag
            ):
                continue

            text = current.get_text(
                " ",
                strip=True
            )

            if not text:
                continue

            if "기사입력" in text:
                break

            if reporter_pattern.search(text):
                continue

            if (
                len(text) <= 250
                and any(
                    word in text
                    for word in (
                        "제공",
                        "이미지",
                        "사진",
                        "조감도",
                        "노선도",
                    )
                )
            ):
                return text

    lines = [
        line.strip()
        for line in page_text.split("\n")
        if line.strip()
    ]

    for i, line in enumerate(lines):
        if reporter_pattern.search(line):
            if i > 0:
                candidate = lines[i - 1]

                if (
                    len(candidate) <= 250
                    and "기사입력" not in candidate
                ):
                    return candidate

    return None


def extract_paragraphs_from_html(
    soup
):
    """
    HTML 문단 구조를 이용해 본문을 추출한다.
    """

    reporter_pattern = re.compile(
        r"\[대한경제=[^\]]+?\s*기자\]"
    )

    reporter_node = soup.find(
        string=reporter_pattern
    )

    if reporter_node is None:
        return []

    start_element = reporter_node.parent

    paragraphs = []

    current = start_element

    while current is not None:

        if isinstance(current, Tag):

            text = clean_paragraph(
                current.get_text(
                    " ",
                    strip=True
                )
            )

            if text:

                if "ⓒ 대한경제신문" in text:
                    break

                if re.search(
                    r"^[가-힣]{2,4}\s+기자\s+\S+@$",
                    text
                ):
                    break

                if current.name == "p":

                    if reporter_pattern.search(
                        text
                    ):
                        text = reporter_pattern.sub(
                            "",
                            text,
                            count=1
                        ).strip()

                    if text:
                        paragraphs.append(
                            text
                        )

        current = current.find_next()

    result = []
    seen = set()

    for paragraph in paragraphs:
        if paragraph in seen:
            continue

        seen.add(paragraph)
        result.append(paragraph)

    return result


def extract_paragraphs_fallback(
    page_text
):
    """
    HTML 기반 추출이 실패했을 경우 사용하는 fallback.
    """

    lines = [
        clean_paragraph(line)
        for line in page_text.split("\n")
        if clean_paragraph(line)
    ]

    reporter_pattern = re.compile(
        r"\[대한경제=[^\]]+?\s*기자\]"
    )

    paragraphs = []
    collecting = False

    for line in lines:

        if reporter_pattern.search(line):

            collecting = True

            line = reporter_pattern.sub(
                "",
                line,
                count=1
            ).strip()

            if line:
                paragraphs.append(line)

            continue

        if not collecting:
            continue

        if re.search(
            r"^[가-힣]{2,4}\s+기자\s+\S+@$",
            line
        ):
            break

        if "ⓒ 대한경제신문" in line:
            break

        paragraphs.append(line)

    return paragraphs


def collect_dnews_article(
    url
):
    """
    대한경제 기사 URL 하나를 Article 객체로 변환한다.
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

    paragraphs = (
        extract_paragraphs_from_html(
            soup
        )
    )

    if not paragraphs:
        paragraphs = (
            extract_paragraphs_fallback(
                page_text
            )
        )

    return Article(
        source=SOURCE["name"],
        title=extract_title(soup),
        reporter=extract_reporter(
            page_text
        ),
        published_at=extract_published_at(
            page_text
        ),
        image_caption=extract_image_caption(
            soup,
            page_text,
        ),
        paragraphs=paragraphs,
        url=url,
    )


def is_in_collection_period(
    article,
    start_date,
    end_date,
):
    """
    기사 날짜가 사용자가 입력한 날짜 범위 안에 있는지 확인한다.
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


def collect_dnews_articles(
    start_date,
    end_date,
):
    """
    사용자가 지정한 날짜 범위에 해당하는
    대한경제 기사를 반환한다.
    """

    urls = collect_dnews_article_urls()

    articles = []

    for url in urls:
        try:
            article = collect_dnews_article(
                url
            )

            if is_in_collection_period(
                article,
                start_date,
                end_date,
            ):
                articles.append(
                    article
                )

        except requests.RequestException as e:
            print(
                f"[WARNING] "
                f"대한경제 기사 수집 실패: "
                f"{url}"
            )
            print(e)

        except Exception as e:
            print(
                f"[WARNING] "
                f"대한경제 기사 파싱 실패: "
                f"{url}"
            )
            print(e)

    articles.sort(
        key=lambda article:
            article.published_at or "",
        reverse=True,
    )

    return articles


class DNewsCollector(
    BaseNewsCollector
):
    """
    main.py에서 사용하는
    대한경제 collector.
    """

    source_name = SOURCE["name"]

    def collect_articles(
        self,
        start_date,
        end_date,
    ):
        return collect_dnews_articles(
            start_date=start_date,
            end_date=end_date,
        )
    