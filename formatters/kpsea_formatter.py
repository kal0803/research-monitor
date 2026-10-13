"""KPSEA 게시판 본문 입력용 HTML."""
from html import escape
from urllib.parse import urlsplit

from formatters.news_formatter import (
    excerpt_paragraphs, format_caption, make_byline,
)
from formatters.source_links import public_article_url


def safe_url(value):
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        return url if parsed.scheme in ("http", "https") and parsed.hostname else ""
    except ValueError:
        return ""


def kpsea_html(article, image_url=None):
    parts = []
    title = escape(str(article.title or ""), quote=True)

    image = safe_url(image_url)
    if image:
        parts.append(
            f'<p><img src="{escape(image, quote=True)}" '
            f'alt="{title}" width="700" '
            'style="max-width:100%;height:auto;"></p>'
        )

    caption = format_caption(article.image_caption)
    if caption:
        parts.append(f"<p>{escape(caption)}</p>")

    paragraphs = excerpt_paragraphs(article)
    byline = make_byline(article.source, article.reporter)

    if paragraphs:
        parts.append(f"<p>{escape(byline + ' ' + paragraphs[0])}</p>")
        for paragraph in paragraphs[1:]:
            parts.append(f"<p>{escape(paragraph)}</p>")
    else:
        parts.append("<p>[본문 발췌 실패: 원문 확인 필요]</p>")

    link = safe_url(public_article_url(article.url))
    if link:
        safe_link = escape(link, quote=True)
        source = escape(str(article.source or ""))
        parts.append(
            f'<p>원문보기[{source}] '
            f'<a href="{safe_link}" target="_blank" '
            f'rel="noopener noreferrer">{safe_link}</a></p>'
        )

    return "\n".join(parts)
