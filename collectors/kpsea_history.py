import re
import time

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright


BASE_URL = "https://kpsea.org"

BOARD_URL = (
    "https://kpsea.org/bbs/"
    "board.php?bo_table=news"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)


def clean_text(text):
    if not text:
        return ""

    return re.sub(
        r"[ \t]+",
        " ",
        text,
    ).strip()


def create_browser_page(browser):
    return browser.new_page(
        user_agent=USER_AGENT,
        locale="ko-KR",
    )


def load_page_html(
    page,
    url,
):
    """
    Playwright로 페이지를 실제 브라우저처럼 렌더링하고
    최종 HTML을 반환한다.
    """

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    page.wait_for_timeout(
        800
    )

    return page.content()


def collect_list_page(
    page_number
):
    """
    KPSEA 목록 페이지를 Playwright로 읽어
    게시글 제목과 URL을 수집한다.
    """

    url = (
        f"{BOARD_URL}"
        f"&page={page_number}"
    )

    articles = []
    seen_urls = set()

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = create_browser_page(
            browser
        )

        try:

            load_page_html(
                page,
                url,
            )

            links = page.locator(
                'a[href*="bo_table=news"]'
                '[href*="wr_id="]'
            )

            count = links.count()

            for index in range(count):

                link = links.nth(
                    index
                )

                href = link.get_attribute(
                    "href"
                )

                if not href:
                    continue

                title = clean_text(
                    link.inner_text()
                )

                if not title:
                    continue

                if title in (
                    "이전글",
                    "다음글",
                    "목록",
                ):
                    continue

                full_url = urljoin(
                    BASE_URL,
                    href,
                )

                full_url = full_url.split(
                    "#"
                )[0]

                if full_url in seen_urls:
                    continue

                seen_urls.add(
                    full_url
                )

                articles.append(
                    {
                        "title_from_list": title,
                        "url": full_url,
                    }
                )

        finally:

            browser.close()

    return articles


def extract_title(
    soup,
):
    """
    상세 페이지 제목 추출.
    """

    selectors = [
        "#bo_v_title",
        ".bo_v_tit",
        ".view-title",
        ".board-view-title",
        "h1",
        "h2",
    ]

    for selector in selectors:

        element = soup.select_one(
            selector
        )

        if not element:
            continue

        title = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if not title:
            continue

        title = re.sub(
            r"\s*>\s*뉴스/이슈.*$",
            "",
            title,
        ).strip()

        if title:
            return title

    og_title = soup.find(
        "meta",
        property="og:title",
    )

    if og_title:

        title = clean_text(
            og_title.get(
                "content",
                "",
            )
        )

        title = re.sub(
            r"\s*>\s*뉴스/이슈.*$",
            "",
            title,
        ).strip()

        return title

    return ""


def extract_published_at(
    page_text,
):
    """
    작성일 추출.

    예:
    작성일 2026.09.21
    """

    patterns = [
        (
            r"작성일\s*"
            r"(20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})"
        ),
        (
            r"등록일\s*"
            r"(20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})"
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
            match.group(1)
            .replace(".", "-")
            .replace("/", "-")
        )

    return None


def extract_source(
    page_text,
):
    """
    원문보기[대한경제]에서
    언론사명 추출.
    """

    match = re.search(
        r"원문보기\s*\[([^\]]+)\]",
        page_text,
    )

    if not match:
        return None

    return clean_text(
        match.group(1)
    )


def extract_original_url(
    soup,
):
    """
    원문보기 뒤의 외부 언론사 URL 추출.
    """

    links = soup.find_all(
        "a",
        href=True,
    )

    # -------------------------
    # 1. 원문보기라는 글자가
    #    링크 내부에 있는 경우
    # -------------------------

    for link in links:

        text = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        href = link.get(
            "href",
            "",
        ).strip()

        if (
            "원문보기" in text
            and href.startswith("http")
            and "kpsea.org" not in href
        ):
            return href

    # -------------------------
    # 2. KPSEA가 아닌 외부 URL
    # -------------------------

    for link in links:

        href = link.get(
            "href",
            "",
        ).strip()

        if not href.startswith(
            "http"
        ):
            continue

        if "kpsea.org" in href:
            continue

        # SNS / 일반 사이트 링크를 피하고
        # 뉴스 원문 가능성이 높은 URL
        # 또는 URL이 화면에 그대로 표시된 경우 사용

        text = clean_text(
            link.get_text(
                " ",
                strip=True,
            )
        )

        known_news_domains = (
            "dnews.co.kr",
            "mtn.co.kr",
            "constimes.co.kr",
            "cenews.kr",
            "cennews.co.kr",
            "safetynews.co.kr",
            "naver.com",
            "newsis.com",
            "yna.co.kr",
            "mk.co.kr",
            "hankyung.com",
            "sedaily.com",
            "etoday.co.kr",
        )

        if (
            text.startswith("http")
            or any(
                domain in href
                for domain
                in known_news_domains
            )
        ):
            return href

    return None


def find_content_container(
    soup,
):
    """
    KPSEA 본문 영역 탐색.
    """

    selectors = [
        "#bo_v_con",
        ".bo_v_con",
        "#bo_v_atc",
        ".view-content",
        ".board-view-content",
        ".bo_v_content",
        ".view_con",
    ]

    for selector in selectors:

        element = soup.select_one(
            selector
        )

        if element:
            return element

    return None


def extract_body_from_container(
    container,
):
    """
    본문 container가 발견된 경우
    그 내부 텍스트를 사용한다.
    """

    if container is None:
        return ""

    raw_text = container.get_text(
        "\n",
        strip=True,
    )

    lines = []

    for raw_line in raw_text.splitlines():

        line = clean_text(
            raw_line
        )

        if not line:
            continue

        if "원문보기" in line:
            break

        if any(
            junk in line
            for junk in (
                "저작권자",
                "무단전재",
                "재배포",
            )
        ):
            continue

        lines.append(
            line
        )

    return "\n\n".join(
        lines
    )


def extract_body_from_page(
    page_text,
    title,
):
    """
    CSS selector가 실패할 경우
    전체 페이지 텍스트에서 본문을 복구한다.

    제목
    ↓
    작성자/작성일
    ↓
    기사 본문
    ↓
    원문보기

    구조를 이용한다.
    """

    raw_lines = (
        page_text.splitlines()
    )

    lines = []

    for raw_line in raw_lines:

        line = clean_text(
            raw_line
        )

        if line:
            lines.append(
                line
            )

    if not lines:
        return ""

    # -------------------------
    # 원문보기 위치
    # -------------------------

    end_index = None

    for index, line in enumerate(
        lines
    ):

        if "원문보기" in line:

            end_index = index
            break

    if end_index is None:
        return ""

    # -------------------------
    # 제목 위치
    # -------------------------

    title_index = None

    normalized_title = clean_text(
        title
    )

    if normalized_title:

        for index, line in enumerate(
            lines[:end_index]
        ):

            if normalized_title in line:

                title_index = index

    if title_index is None:
        return ""

    candidate_lines = lines[
        title_index + 1:
        end_index
    ]

    body_lines = []

    for line in candidate_lines:

        # 작성자 + 작성일 + 조회수 등의
        # 메타데이터 줄 제거
        if (
            "작성자" in line
            and "작성일" in line
        ):
            continue

        if re.match(
            r"^작성일\s*"
            r"20\d{2}",
            line,
        ):
            continue

        if re.fullmatch(
            r"\d+\s*회",
            line,
        ):
            continue

        if line in (
            "뉴스/이슈",
            "이전글",
            "다음글",
            "목록",
        ):
            continue

        if any(
            junk in line
            for junk in (
                "저작권자",
                "무단전재",
                "재배포",
            )
        ):
            continue

        body_lines.append(
            line
        )

    return "\n\n".join(
        body_lines
    )


def extract_body(
    soup,
    page_text,
    title,
):
    """
    본문 추출.

    1. 본문 HTML container
    2. 전체 페이지 텍스트 fallback
    """

    container = (
        find_content_container(
            soup
        )
    )

    body = (
        extract_body_from_container(
            container
        )
    )

    if body:
        return body

    return extract_body_from_page(
        page_text,
        title,
    )


def parse_article_html(
    html,
    fallback_title="",
):
    """
    렌더링된 상세 페이지 HTML을
    구조화된 기사 정보로 변환한다.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    page_text = soup.get_text(
        "\n",
        strip=True,
    )

    title = extract_title(
        soup
    )

    if not title:
        title = fallback_title

    return {
        "title": title,

        "published_at":
            extract_published_at(
                page_text
            ),

        "source":
            extract_source(
                page_text
            ),

        "original_url":
            extract_original_url(
                soup
            ),

        "body":
            extract_body(
                soup,
                page_text,
                title,
            ),
    }


def collect_page_articles(
    page_number,
    delay=0.4,
):
    """
    한 게시판 페이지의 기사들을 수집한다.

    목록뿐 아니라 상세 페이지도
    Playwright를 사용한다.
    """

    list_items = (
        collect_list_page(
            page_number
        )
    )

    results = []

    if not list_items:
        return results

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = create_browser_page(
            browser
        )

        try:

            for item in list_items:

                url = item["url"]

                try:

                    html = load_page_html(
                        page,
                        url,
                    )

                    article_data = (
                        parse_article_html(
                            html,
                            fallback_title=(
                                item[
                                    "title_from_list"
                                ]
                            ),
                        )
                    )

                    article = {
                        "kpsea_url":
                            url,

                        "title":
                            article_data[
                                "title"
                            ],

                        "published_at":
                            article_data[
                                "published_at"
                            ],

                        "source":
                            article_data[
                                "source"
                            ],

                        "original_url":
                            article_data[
                                "original_url"
                            ],

                        "body":
                            article_data[
                                "body"
                            ],
                    }

                    results.append(
                        article
                    )

                except Exception as e:

                    print(
                        "[WARNING] "
                        "KPSEA 상세기사 "
                        "수집 실패: "
                        f"{url}"
                    )

                    print(e)

                time.sleep(
                    delay
                )

        finally:

            browser.close()

    return results
