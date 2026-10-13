"""최종 출력용 원문 발췌. Rubric 분류에 사용하는 전체 본문은 수정하지 않는다."""

import re
from models import Article
from formatters.source_links import public_article_url


INVALID_REPORTERS = {
    "", "-", "없음", "미상", "사설", "기자", "unknown", "none", "null", "nan",
}
EXCERPT_PARAGRAPHS = 3

# 크롤러가 문단을 한 덩어리로 합친 경우에만 문장 경계로 나눈다.
LONG_PARAGRAPH_THRESHOLD = 400
TARGET_PARAGRAPH_CHARS = 330


def normalize_reporter(reporter):
    if reporter is None:
        return None

    name = re.sub(
        r"\s*기자\s*$",
        "",
        str(reporter).strip(),
    ).strip()

    return None if name.lower() in INVALID_REPORTERS else name


def format_caption(caption):
    text = " ".join(str(caption or "").split())

    if not text:
        return None

    # 일부 기사에서 이메일이 'ays77@'처럼 잘린 채
    # 캡션 끝에 붙는 경우만 보수적으로 제거한다.
    text = re.sub(
        r"\s+[A-Za-z0-9._%+-]+@\s*$",
        "",
        text,
    ).strip()

    return text or None


def make_byline(source, reporter):
    source = str(source or "").strip()
    name = normalize_reporter(reporter)

    return (
        f"[{source}={name} 기자]"
        if name
        else f"[{source}]"
    )


def strip_duplicate_byline(paragraph, source, reporter):
    """문두에 수집되어 들어온 같은 매체의 기자명 표기를 지운다."""
    text = str(paragraph or "").strip()
    source = str(source or "").strip()

    if not text or not source:
        return text

    pattern = re.compile(
        rf"^(?:\[\s*{re.escape(source)}\s*(?:=\s*[^\]\r\n]+)?\s*\]"
        rf"|\(\s*{re.escape(source)}\s*\))\s*"
    )

    removed_source = False

    for _ in range(10):
        found = pattern.match(text)

        if not found:
            break

        text = text[found.end():].lstrip()
        removed_source = True

    name = normalize_reporter(reporter)

    if name:
        text = re.sub(
            rf"^{re.escape(name)}\s*기자\s*[:=]\s*",
            "",
            text,
            count=1,
        ).lstrip()

    elif removed_source:
        text = re.sub(
            r"^[가-힣A-Za-z·]{2,18}\s*기자\s*[:=]\s*",
            "",
            text,
            count=1,
        ).lstrip()

    return text


def is_leading_subheading(text):
    """본문 앞의 짧은 부제·소제목만 보수적으로 제외한다.

    짧더라도 서술형 기사 문장(예: '~ 밝혔다')이면 본문으로 인정한다.
    """
    compact = " ".join(str(text or "").split())

    if not compact or len(compact) > 85:
        return False

    if compact.endswith((".", "!", "?", "。", "다", "요")):
        return False

    # 짧은 단편형 구절은 부제일 가능성이 크다.
    # 원문에서 삭제하는 것이 아니라 보고서에서만 건너뛴다.
    return True


def split_long_paragraph(text):
    """길게 합쳐진 한 문단을 원문 문장/띄어쓰기 경계에서만 구분한다."""
    text = " ".join(str(text or "").split())

    if len(text) <= LONG_PARAGRAPH_THRESHOLD:
        return [text] if text else []

    # 띄어쓰기 뒤 새 문장이 시작하는 위치에서만 나눈다.
    sentences = re.split(
        r'(?<=[.!?。])\s+(?=[가-힣A-Za-z0-9“‘"(\[])',
        text,
    )

    pieces = []

    for sentence in sentences:
        remaining = sentence.strip()

        while len(remaining) > TARGET_PARAGRAPH_CHARS:
            cut = remaining.rfind(
                " ",
                0,
                TARGET_PARAGRAPH_CHARS + 1,
            )

            if cut < TARGET_PARAGRAPH_CHARS // 2:
                cut = remaining.find(
                    " ",
                    TARGET_PARAGRAPH_CHARS,
                )

            if cut < 0:
                # 긴 어절은 강제로 잘라 훼손하지 않는다.
                break

            pieces.append(
                remaining[:cut].strip()
            )

            remaining = remaining[cut:].strip()

        if remaining:
            pieces.append(remaining)

    grouped = []
    current = ""

    for piece in pieces:
        combined = (
            (current + " " + piece).strip()
            if current
            else piece
        )

        if current and len(combined) > TARGET_PARAGRAPH_CHARS:
            grouped.append(current)
            current = piece
        else:
            current = combined

    if current:
        grouped.append(current)

    return grouped


def excerpt_paragraphs(
    article: Article,
    limit=EXCERPT_PARAGRAPHS,
):
    """앞쪽 소제목을 건너뛰고 실제 기사 문장 최대 3문단을 발췌한다.

    긴 단일 문단은 원문을 바꾸지 않고 표시용 문단으로 재구성할 수 있다.
    """
    if limit <= 0:
        return []

    cleaned = []

    for raw in article.paragraphs or []:
        text = strip_duplicate_byline(
            raw,
            article.source,
            article.reporter,
        )

        text = " ".join(text.split())

        if text:
            cleaned.append(text)

    # 본문이 시작되기 전의 짧은 부제/소제목만 제외한다.
    while cleaned and is_leading_subheading(cleaned[0]):
        cleaned.pop(0)

    excerpt = []

    for paragraph in cleaned:
        for part in split_long_paragraph(paragraph):
            if part:
                excerpt.append(part)

            if len(excerpt) >= limit:
                return excerpt

    return excerpt


def source_line(article: Article):
    """기존 뉴스 원문보기 형식으로 출처를 출력한다."""
    source = str(article.source or "").strip()

    return f"원문보기[{source}] {public_article_url(article.url)}"


def format_article(article: Article) -> str:
    """게시판용 제목/캡션/본문/원문 링크 순서의 TXT."""
    lines = [
        f"**{article.title}**",
        "",
    ]

    # 이미지 URL이 없어도 캡션 자체가 있으면 출력한다.
    caption = format_caption(article.image_caption)

    if caption:
        lines.extend([
            caption,
            "",
        ])

    # 기존 이미지 링크 출력 기능은 유지한다.
    if article.image_url:
        lines.extend([
            f"이미지 보기: {article.image_url}",
            "",
        ])

    paragraphs = excerpt_paragraphs(article)
    byline = make_byline(
        article.source,
        article.reporter,
    )

    if paragraphs:
        lines.append(
            f"{byline} {paragraphs[0]}"
        )

        for paragraph in paragraphs[1:]:
            lines.extend([
                "",
                paragraph,
            ])

    else:
        lines.append(
            f"{byline} [본문 발췌 실패: 원문 확인 필요]"
        )

    lines.extend([
        "",
        source_line(article),
    ])

    return "\n".join(lines)


def format_articles(articles: list[Article]) -> str:
    return "\n\n\n".join(
        format_article(article)
        for article in articles
    )