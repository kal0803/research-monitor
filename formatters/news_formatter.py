from collectors.article_subheading import (
    is_rejected_subheading,
    is_site_ui_text,
)
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

    if not text or is_site_ui_text(text):
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
    """수집된 소제목과 중복된 문단을 건너뛰고 최대 3문단을 발췌한다.

    긴 단일 문단은 원문을 바꾸지 않고 표시용 문단으로 재구성할 수 있다.
    """
    if limit <= 0:
        return []

    known_subheadings = set(article_subheadings(article))
    cleaned = []

    for raw in article.paragraphs or []:
        text = strip_duplicate_byline(
            raw,
            article.source,
            article.reporter,
        )

        text = " ".join(text.split())

        if (
            text
            and text not in known_subheadings
            and not is_site_ui_text(text)
        ):
            cleaned.append(text)

    excerpt = []

    for paragraph in cleaned:
        for part in split_long_paragraph(paragraph):
            if part:
                excerpt.append(part)

            if len(excerpt) >= limit:
                return excerpt

    return excerpt



def article_subheadings(article):
    """수집기가 원문 HTML에서 명시적으로 찾은 부제목만 반환한다."""
    result = []
    seen = set()

    for raw in getattr(article, "subheadings", []) or []:
        text = " ".join(str(raw).split())
        if text and text not in seen and not is_rejected_subheading(text):
            result.append(text)
            seen.add(text)

    return result[:8]


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

    for subheading in article_subheadings(article):
        lines.extend([f"**{subheading}**", ""])

    if article.image_url:
        lines.extend([
            f"이미지 보기: {article.image_url}",
            "",
        ])

    caption = format_caption(article.image_caption)
    if caption:
        lines.extend([caption, ""])

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
