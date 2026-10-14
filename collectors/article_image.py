"""선별 기사에 한해서 대표 이미지 한 장을 다운로드한다.

이미지는 HTTPS/HTTP 공개 URL에서만 가져온다. 다운로드 실패는 허용한다.
서로 다른 기사에서 동일한 이미지가 반복되면 공통 로고/기본 사진으로
간주하여 보고서에서 제외할 수 있도록 digest를 제공한다.
"""

import base64
from io import BytesIO
from PIL import Image
from collectors.article_subheading import extract_page_subheadings
import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup


MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_CANDIDATES = 8
HTTP_TIMEOUT = (5, 15)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
BODY_IMAGE_SELECTORS = (
    "#article-view-content-div img", ".article-view-content-div img",
    "#articleBody img", "#articleContent img", ".article-body img",
    ".article_body img", ".article-content img", ".article_content img",
    ".article_txt img", ".article-text img", ".news_text img",
    ".news-content img", ".view_cont img", ".news_detail img",
    "article figure img", "article img",
)
BAD_IMAGE_TERMS = (
    "logo", "icon", "avatar", "favicon", "banner", "sprite",
    "share", "sns", "facebook", "instagram", "kakao", "youtube",
    "loading", "placeholder", "advert", "spinner", "button",
    "og_default", "default_og", "site_default", "site-logo",
    "dnews.png",
)


@dataclass(frozen=True)
class ArticleImage:
    url: str
    data_uri: str
    digest: str
    width: int = 0
    height: int = 0


def usable_url(url):
    """로컬 주소, URL 내 인증정보, 비표준 포트는 허용하지 않는다."""
    try:
        parsed = urlsplit(str(url))
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return False
        if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False


def normalize_image_url(value, base_url):
    raw = str(value or "").strip()
    if not raw or raw.startswith(("data:", "blob:", "javascript:")):
        return None
    url = urljoin(base_url, raw)
    return url if usable_url(url) else None


def candidate_urls(soup, base_url):
    """기사 본문에 있는 사진을 우선하고, 메타 이미지로 보완한다."""
    found, seen = [], set()

    def add(value):
        image_url = normalize_image_url(value, base_url)
        if not image_url or image_url in seen:
            return
        path = urlsplit(image_url).path.lower()
        if any(term in path for term in BAD_IMAGE_TERMS):
            return
        seen.add(image_url)
        found.append(image_url)

    # 대한경제: td 내부의 기사별 실제 사진을 우선 수집
    domain = (urlsplit(base_url).hostname or "").lower()
    if domain == "dnews.co.kr" or domain.endswith(".dnews.co.kr"):
        idxno = (
            parse_qs(urlsplit(base_url).query).get("idxno")
            or [""]
        )[0]

        for tag in soup.find_all("img"):
            for attr in (
                "data-original", "data-src",
                "data-lazy-src", "src"
            ):
                value = normalize_image_url(tag.get(attr), base_url)
                if not value:
                    continue

                path = urlsplit(value).path.lower()
                filename = path.rsplit("/", 1)[-1]

                if "/photo/photo/" in path and (
                    not idxno or filename.startswith(f"{idxno}-")
                ):
                    add(value)
                    if len(found) >= MAX_CANDIDATES:
                        return found[:MAX_CANDIDATES]

    for selector in BODY_IMAGE_SELECTORS:
        for tag in soup.select(selector):
            width, height = str(tag.get("width", "")), str(tag.get("height", ""))
            if width.isdigit() and int(width) < 120:
                continue
            if height.isdigit() and int(height) < 80:
                continue
            if any(term in str(tag.get("alt", "")).lower() for term in ("로고", "logo", "광고")):
                continue
            for attr in ("data-original", "data-src", "data-lazy-src", "src"):
                add(tag.get(attr))
            if len(found) >= MAX_CANDIDATES:
                return found[:MAX_CANDIDATES]

    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
        {"property": "twitter:image"},
    ):
        for meta in soup.find_all("meta", attrs=attrs):
            add(meta.get("content"))
    return found[:MAX_CANDIDATES]


def detect_image_mime(payload):
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_image(session, image_url, referer):
    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Accept"] = "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.5"

    with session.get(image_url, headers=headers, timeout=HTTP_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        if not usable_url(response.url):
            return None
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > MAX_IMAGE_BYTES:
            return None
        chunks, count = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            count += len(chunk)
            if count > MAX_IMAGE_BYTES:
                return None
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) < 1024:
            return None
        mime = detect_image_mime(payload)
        if mime is None:
            return None

        try:
            with Image.open(BytesIO(payload)) as image:
                width, height = image.size
        except (OSError, ValueError, Image.DecompressionBombError):
            return None

        if width < 200 or height < 120:
            return None

        return ArticleImage(
            url=response.url,
            data_uri=f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}",
            digest=hashlib.sha256(payload).hexdigest(),
            width=width,
            height=height,
        )


def fetch_representative_image(article, excluded_digests=None):
    excluded_digests = set(excluded_digests or ())
    url = str(getattr(article, "url", "") or "").strip()
    if not usable_url(url):
        return None
    try:
        with requests.Session() as session:
            page = session.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
            page.raise_for_status()
            if not usable_url(page.url):
                return None
            soup = BeautifulSoup(page.content, "html.parser")

            # 사진 수집과 함께 소제목도 추출한다.
            detected_subheadings = extract_page_subheadings(
                soup, article
            )

            existing_subheadings = (
                getattr(article, "subheadings", []) or []
            )

            if (
                not existing_subheadings
                and article.source != "MTN뉴스"
            ):
                article.subheadings = detected_subheadings[:8]

            best = None

            for image_url in candidate_urls(soup, page.url):
                try:
                    result = read_image(session, image_url, page.url)

                    if not result:
                        continue
                    if result.digest in excluded_digests:
                        continue

                    if best is None:
                        best = result
                    elif (
                        result.width >= best.width * 1.25
                        and result.height >= best.height * 0.7
                    ):
                        best = result

                    # 충분히 큰 원문 사진을 확보하면 종료.
                    if best.width >= 1200 and best.height >= 600:
                        break

                except (requests.RequestException, ValueError):
                    continue

            return best
    except (requests.RequestException, ValueError) as exc:
        print(f"  [IMAGE WARNING] 원문 접근 실패: {exc}")
    return None


def remove_repeated_site_images(articles, assets):
    """동일 언론사에서 서로 다른 기사에 반복된 동일 사진은 제외한다.

    한 기사에만 나온 로고는 자동 판별하기 어렵고, 동일 기관 사진이
    실제로 두 기사에서 재사용된 경우에도 이 보수 규칙이 제외할 수 있다.
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for article in articles:
        asset = assets.get(article.url)
        if asset:
            groups[(article.source, asset.digest)].append(article)

    removed = []
    for items in groups.values():
        if len({article.url for article in items}) < 2:
            continue
        for article in items:
            if article.url in assets:
                assets.pop(article.url)
                article.image_url = None
                removed.append(article)
    return removed
