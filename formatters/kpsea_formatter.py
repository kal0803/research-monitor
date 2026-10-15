"""KPSEA 게시판용 HTML: 사진, 캡션, 소제목, 본문, 출처."""

from html import escape
from urllib.parse import urlsplit

from formatters.news_formatter import (
    excerpt_paragraphs,
    article_subheadings,
    format_caption,
    make_byline,
)
from formatters.source_links import public_article_url


def safe_url(value):
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
        ):
            return url
    except ValueError:
        pass
    return ""


def kpsea_html(article, image_url=None):
    blocks = []
    title = escape(str(article.title or ""), quote=True)

    # 1. 기사 부제목을 사진보다 먼저 표시
    for subtitle in article_subheadings(article):
        blocks.append(
            f'<strong>{escape(subtitle)}</strong>'
        )

    # 2. 사진 바로 다음 줄에 설명을 붙여 표시
    image = safe_url(image_url)
    caption = format_caption(article.image_caption)

    if image:
        photo_html = (
            f'<img src="{escape(image, quote=True)}" '
            f'alt="{title}" '
            'style="display:block;max-width:100%;height:auto;'
            'margin:0;padding:0;">'
        )

        if caption:
            # 이미지와 설명 사이에는 br 태그를 넣지 않는다.
            photo_html += escape(caption)

        blocks.append(photo_html)

    elif caption:
        blocks.append(escape(caption))

    # 4. 본문 최대 3문단
    paragraphs = excerpt_paragraphs(article)
    byline = make_byline(article.source, article.reporter)

    if paragraphs:
        blocks.append(
            escape(byline + " " + paragraphs[0])
        )
        for paragraph in paragraphs[1:]:
            blocks.append(escape(paragraph))
    else:
        blocks.append(
            "[본문 발췌 실패: 원문 확인 필요]"
        )

    # 5. 원문 링크
    link = safe_url(public_article_url(article.url))

    if link:
        safe_link = escape(link, quote=True)
        source = escape(str(article.source or ""))

        blocks.append(
            f'원문보기[{source}] '
            f'<a href="{safe_link}" target="_blank" '
            f'rel="noopener noreferrer">{safe_link}</a>'
        )

    # <p>의 기본 margin이 제거되는 게시판을 고려해
    # 실제 줄바꿈 태그를 명시적으로 사용한다.
    return "<br><br>\n".join(blocks)
