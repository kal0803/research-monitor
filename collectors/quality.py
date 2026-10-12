import re

from dataclasses import dataclass
from typing import List
from urllib.parse import urlparse

from models import Article


# ============================================================
# RESULT MODEL
# ============================================================

@dataclass
class ArticleQualityResult:
    """
    기사 하나의 데이터 품질 검사 결과.

    is_valid:
        DB / Rubric 단계로 넘겨도 되는 기사인지 여부.

    errors:
        기사를 차단해야 하는 명백한 데이터 오류.

    warnings:
        기사를 차단하지는 않지만 확인할 가치가 있는 문제.
    """

    is_valid: bool
    errors: List[str]
    warnings: List[str]


# ============================================================
# BASIC TEXT
# ============================================================

def clean_text(text):

    if not text:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(text),
    ).strip()


def get_body_text(article):
    """
    Article.paragraphs를 하나의 텍스트로 합친다.

    원본 body 확인용이다.
    실질 본문 품질 검사는
    get_meaningful_paragraphs()를 사용한다.
    """

    paragraphs = getattr(
        article,
        "paragraphs",
        None,
    )

    if not paragraphs:
        return ""

    cleaned = []

    for paragraph in paragraphs:

        text = clean_text(
            paragraph
        )

        if text:

            cleaned.append(
                text
            )

    return "\n".join(
        cleaned
    )


# ============================================================
# MOJIBAKE
# ============================================================

SUSPICIOUS_MOJIBAKE_CHARS = set(
    (
        "¼½¾¿"
        "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏ"
        "ÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞß"
        "°±²³´µ¶·¸¹º»"
    )
)


def count_hangul(text):
    """
    문자열 안의 완성형 한글 글자 수.
    """

    return len(
        re.findall(
            r"[가-힣]",
            text or "",
        )
    )


def count_mojibake_chars(text):
    """
    흔한 CP949/EUC-KR 오디코딩 결과에
    나타나는 의심 문자를 센다.
    """

    if not text:
        return 0

    return sum(
        1
        for char in text
        if char
        in SUSPICIOUS_MOJIBAKE_CHARS
    )


def looks_like_mojibake(text):
    """
    문자열이 명백하게 깨진 한글처럼 보이는지 검사한다.

    정상 기사까지 제거하지 않도록
    보수적으로 판단한다.
    """

    text = clean_text(
        text
    )

    if not text:
        return False

    if "�" in text:
        return True

    suspicious_count = (
        count_mojibake_chars(
            text
        )
    )

    hangul_count = (
        count_hangul(
            text
        )
    )

    if (
        suspicious_count >= 3
        and hangul_count <= 2
    ):
        return True

    if len(text) >= 10:

        suspicious_ratio = (
            suspicious_count
            / len(text)
        )

        if (
            suspicious_ratio >= 0.20
            and suspicious_count >= 3
        ):
            return True

    return False


# ============================================================
# URL
# ============================================================

def is_valid_http_url(url):
    """
    최소한 정상적인 HTTP/HTTPS 기사 URL 형태인지 확인한다.

    사이트별 URL 규칙은 각각의 collector가 담당하고,
    여기서는 공통적인 형식 오류만 검사한다.
    """

    url = clean_text(
        url
    )

    if not url:
        return False

    try:

        parsed = urlparse(
            url
        )

    except Exception:
        return False

    if parsed.scheme not in {
        "http",
        "https",
    }:
        return False

    if not parsed.netloc:
        return False

    return True


# ============================================================
# PARAGRAPH NORMALIZATION
# ============================================================

def normalize_paragraphs(
    paragraphs,
):
    """
    빈 문단 제거 + 완전 동일 문단 중복 제거.

    Article 자체를 수정하지 않고
    새로운 list를 반환한다.
    """

    if not paragraphs:
        return []

    result = []
    seen = set()

    for paragraph in paragraphs:

        text = clean_text(
            paragraph
        )

        if not text:
            continue

        if text in seen:
            continue

        seen.add(
            text
        )

        result.append(
            text
        )

    return result


# ============================================================
# NON-BODY DETECTION
# ============================================================

def is_email_only(
    text,
):
    """
    이메일 주소만 있는 문단.
    """

    text = clean_text(
        text
    )

    return bool(
        re.fullmatch(
            r"[A-Za-z0-9._%+-]+@"
            r"[A-Za-z0-9.-]+"
            r"\.[A-Za-z]{2,}",
            text,
        )
    )


def is_reporter_contact_only(
    text,
):
    """
    실제 본문이 아니라 기자명/연락처만 있는 문단.

    예:
        최남영 기자
        최남영 기자 soc@mtn.co.kr
        최남영 기자soc@mtn.co.kr
    """

    text = clean_text(
        text
    )

    if not text:
        return False

    reporter_pattern = (
        r"[가-힣]{2,5}\s*기자"
    )

    email_pattern = (
        r"[A-Za-z0-9._%+-]+@"
        r"[A-Za-z0-9.-]+"
        r"\.[A-Za-z]{2,}"
    )

    if re.fullmatch(
        reporter_pattern,
        text,
    ):
        return True

    if re.fullmatch(
        reporter_pattern
        + r"\s*"
        + email_pattern,
        text,
    ):
        return True

    return False


def is_source_byline_only(
    text,
):
    """
    언론사 + 기자명만 존재하는 byline.

    예:
        [대한경제=홍길동 기자]
        [MTN뉴스=최남영 기자]
        (건설타임즈) 박상민 기자
    """

    text = clean_text(
        text
    )

    if not text:
        return False

    patterns = [
        (
            r"\[[^\]]{1,40}="
            r"[가-힣]{2,5}\s*기자\]"
        ),
        (
            r"\([^)]{1,40}\)\s*"
            r"[가-힣]{2,5}\s*기자"
        ),
    ]

    return any(
        re.fullmatch(
            pattern,
            text,
        )
        for pattern in patterns
    )


def is_boilerplate_only(
    text,
):
    """
    기사 본문이 아닌 저작권/UI/사이트 안내 문구.
    """

    text = clean_text(
        text
    )

    if not text:
        return True

    boilerplate_terms = (
        "저작권자",
        "무단전재",
        "재배포금지",
        "재배포 금지",
        "반론·정정",
        "반론ㆍ정정",
        "정정보도",
        "고충처리인",
        "개인정보처리방침",
        "청소년보호정책",
        "이용약관",
        "카카오 공유하기",
        "기자의 다른기사",
        "기자의 다른 기사",
        "많이본뉴스",
        "많이 본 뉴스",
        "MTN 기자실",
        "본문 글자크기",
        "폰트크기",
        "Please activate JavaScript",
        "Copyright",
        "ⓒ",
    )

    return any(
        term in text
        for term in boilerplate_terms
    )


def looks_like_caption_only(
    text,
):
    """
    매우 짧은 이미지 설명/출처 문구인지 검사한다.

    긴 기사 문장 속에 '사진'이나 '제공'이라는
    단어가 들어갔다는 이유만으로 제거하지 않는다.
    """

    text = clean_text(
        text
    )

    if not (
        3 <= len(text) <= 250
    ):
        return False

    strong_caption_patterns = (
        r"^사진\s*[=/]",
        r"^\[사진[=/]",
        r"^자료\s*[=/]",
        r"^\[자료[=/]",
        r".+\s사진\s*[=/]\s*.+$",
        r".+\s자료\s*[=/]\s*.+$",
        r".+\s제공\s*[=/]\s*.+$",
    )

    if any(
        re.search(
            pattern,
            text,
        )
        for pattern
        in strong_caption_patterns
    ):
        return True

    return False


def is_non_body_paragraph(
    text,
    article=None,
):
    """
    실제 기사 본문으로 보기 어려운 문단을 판별한다.

    주제 관련성은 전혀 판단하지 않는다.
    """

    text = clean_text(
        text
    )

    if not text:
        return True

    if is_email_only(
        text
    ):
        return True

    if is_reporter_contact_only(
        text
    ):
        return True

    if is_source_byline_only(
        text
    ):
        return True

    if is_boilerplate_only(
        text
    ):
        return True

    if article is not None:

        title = clean_text(
            getattr(
                article,
                "title",
                "",
            )
        )

        caption = clean_text(
            getattr(
                article,
                "image_caption",
                "",
            )
        )

        if (
            title
            and text == title
        ):
            return True

        if (
            caption
            and text == caption
        ):
            return True

    if looks_like_caption_only(
        text
    ):
        return True

    return False


# ============================================================
# MEANINGFUL BODY
# ============================================================

def get_meaningful_paragraphs(
    article,
):
    """
    Article.paragraphs 중
    실제 기사 본문 후보만 반환한다.

    원본 Article.paragraphs 자체는 수정하지 않는다.
    """

    paragraphs = (
        normalize_paragraphs(
            getattr(
                article,
                "paragraphs",
                [],
            )
        )
    )

    meaningful = []

    for paragraph in paragraphs:

        if is_non_body_paragraph(
            paragraph,
            article=article,
        ):
            continue

        meaningful.append(
            paragraph
        )

    return meaningful


def get_meaningful_body_text(
    article,
):
    """
    품질검사용 실질 본문 문자열.
    """

    return "\n".join(
        get_meaningful_paragraphs(
            article
        )
    )


def has_reasonable_body(
    article,
):
    """
    classifier에 넘길 수 있을 정도의
    실질 기사 본문이 존재하는지 판단한다.

    중요:
    관련성은 판단하지 않는다.

    이미 앞 단계에서 다음을 제거한다.

    - 기자명
    - 이메일
    - byline
    - 이미지 캡션
    - UI 문구
    - 저작권 문구
    - 제목 중복

    따라서 길이 기준은
    마지막 최소 안전장치로만 사용한다.

    기준:
    - 실질 문단 하나 이상
    - 전체 실질 본문 40자 이상
    - 가장 긴 실질 문단 25자 이상
    """

    paragraphs = (
        get_meaningful_paragraphs(
            article
        )
    )

    if not paragraphs:
        return False

    total_chars = sum(
        len(paragraph)
        for paragraph
        in paragraphs
    )

    longest_paragraph = max(
        (
            len(paragraph)
            for paragraph
            in paragraphs
        ),
        default=0,
    )

    if total_chars < 40:
        return False

    if longest_paragraph < 25:
        return False

    return True


# ============================================================
# TITLE QUALITY
# ============================================================

def has_valid_title(
    article,
):

    title = clean_text(
        getattr(
            article,
            "title",
            "",
        )
    )

    if not title:
        return False

    if len(title) < 4:
        return False

    return True


# ============================================================
# DATE
# ============================================================

def has_published_at(
    article,
):

    published_at = clean_text(
        getattr(
            article,
            "published_at",
            "",
        )
    )

    return bool(
        published_at
    )


# ============================================================
# MAIN QUALITY CHECK
# ============================================================

def check_article_quality(
    article,
):
    """
    기사 하나의 공통 데이터 품질을 검사한다.

    중요:
    여기서는 뉴스의 '관련성'을 판단하지 않는다.

    예:
        아파트 재건축
        병원 건설
        전력
        일반 산업안전
        AI
        BIM

    같은 주제는 정상 데이터라면 통과시킨다.

    KEEP / DROP 판단은 Rubric v2의 역할이다.
    """

    errors = []
    warnings = []

    title = clean_text(
        getattr(
            article,
            "title",
            "",
        )
    )

    url = clean_text(
        getattr(
            article,
            "url",
            "",
        )
    )

    raw_body = get_body_text(
        article
    )

    meaningful_body = (
        get_meaningful_body_text(
            article
        )
    )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    if not url:

        errors.append(
            "URL_MISSING"
        )

    elif not is_valid_http_url(
        url
    ):

        errors.append(
            "URL_INVALID"
        )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    if not has_valid_title(
        article
    ):

        errors.append(
            "TITLE_MISSING_OR_INVALID"
        )

    elif looks_like_mojibake(
        title
    ):

        errors.append(
            "TITLE_MOJIBAKE"
        )

    # --------------------------------------------------------
    # BODY
    # --------------------------------------------------------

    if not raw_body:

        errors.append(
            "BODY_MISSING"
        )

    elif not meaningful_body:

        errors.append(
            "BODY_NO_MEANINGFUL_CONTENT"
        )

    elif not has_reasonable_body(
        article
    ):

        errors.append(
            "BODY_TOO_SHORT_OR_LOW_INFORMATION"
        )

    elif looks_like_mojibake(
        meaningful_body
    ):

        errors.append(
            "BODY_MOJIBAKE"
        )

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    if not has_published_at(
        article
    ):

        warnings.append(
            "PUBLISHED_AT_MISSING"
        )

    # --------------------------------------------------------
    # REPORTER
    # --------------------------------------------------------

    reporter = clean_text(
        getattr(
            article,
            "reporter",
            "",
        )
    )

    if not reporter:

        warnings.append(
            "REPORTER_MISSING"
        )

    # image_caption은 필수 데이터가 아니다.

    return ArticleQualityResult(
        is_valid=(
            len(errors) == 0
        ),
        errors=errors,
        warnings=warnings,
    )


# ============================================================
# ARTICLE CLEANUP
# ============================================================

def clean_article(
    article,
):
    """
    Article의 텍스트를 최소한으로 정리한다.

    관련 내용은 절대 삭제하지 않는다.

    quality 검사에서 기자명/캡션/UI 문구를
    실질 본문에서 제외하더라도
    Article.paragraphs 자체에서는 삭제하지 않는다.

    원문 보존을 우선한다.
    """

    article.title = clean_text(
        getattr(
            article,
            "title",
            "",
        )
    )

    article.reporter = (
        clean_text(
            getattr(
                article,
                "reporter",
                "",
            )
        )
        or None
    )

    article.published_at = (
        clean_text(
            getattr(
                article,
                "published_at",
                "",
            )
        )
        or None
    )

    article.image_caption = (
        clean_text(
            getattr(
                article,
                "image_caption",
                "",
            )
        )
        or None
    )

    article.url = clean_text(
        getattr(
            article,
            "url",
            "",
        )
    )

    article.paragraphs = (
        normalize_paragraphs(
            getattr(
                article,
                "paragraphs",
                [],
            )
        )
    )

    return article


# ============================================================
# BATCH FILTER
# ============================================================

def filter_articles_by_quality(
    articles,
    verbose=True,
):
    """
    여러 collector에서 받은 Article들을
    공통 품질 기준으로 검사한다.

    Returns
    -------
    valid_articles:
        다음 단계(DB / Rubric)로 넘길 기사.

    rejected:
        데이터 품질 문제로 차단된 기사 정보.
    """

    valid_articles = []
    rejected = []

    warning_count = 0

    for article in articles:

        article = clean_article(
            article
        )

        result = (
            check_article_quality(
                article
            )
        )

        if result.warnings:

            warning_count += 1

        if result.is_valid:

            valid_articles.append(
                article
            )

            continue

        rejected.append(
            {
                "source":
                    getattr(
                        article,
                        "source",
                        "",
                    ),

                "title":
                    getattr(
                        article,
                        "title",
                        "",
                    ),

                "url":
                    getattr(
                        article,
                        "url",
                        "",
                    ),

                "errors":
                    result.errors,

                "warnings":
                    result.warnings,
            }
        )

        if verbose:

            print(
                "[QUALITY DROP] "
                f"{getattr(article, 'source', '')}"
            )

            print(
                "  title: "
                f"{getattr(article, 'title', '')}"
            )

            print(
                "  errors: "
                + ", ".join(
                    result.errors
                )
            )

            print(
                "  url: "
                f"{getattr(article, 'url', '')}"
            )

    if verbose:

        print()
        print(
            "[QUALITY CHECK]"
        )

        print(
            f"입력: {len(articles)}"
        )

        print(
            f"통과: {len(valid_articles)}"
        )

        print(
            f"제외: {len(rejected)}"
        )

        print(
            f"warning 보유 기사: "
            f"{warning_count}"
        )

    return (
        valid_articles,
        rejected,
    )
