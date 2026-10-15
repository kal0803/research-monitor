"""KPSEA 게시판에 옮기기 편한 독립형 HTML 보고서 생성."""

from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from formatters.source_links import public_article_url
from formatters.news_formatter import (
    excerpt_paragraphs, article_subheadings,
    format_caption, make_byline,
)

OUTPUT_DIR = Path("outputs")

CSS = """
:root { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #202938; background: #f5f7fa; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 870px; margin: 0 auto; padding: 38px 20px 80px; }
header { padding-bottom: 25px; margin-bottom: 28px; border-bottom: 1px solid #dfe5eb; }
h1 { font-size: 28px; margin: 0 0 8px; }
.sub { color: #627084; font-size: 14px; }
article { background: white; border: 1px solid #e2e7ed; border-radius: 14px;
          padding: 27px 30px; margin-bottom: 22px; }
h2 { font-size: 21px; margin: 0 0 14px; line-height: 1.48; word-break: keep-all; }
.meta { color: #64748b; font-size: 13px; margin-bottom: 15px; }
figure { margin: 8px 0 21px; }
figure img { width: 100%; max-height: 540px; object-fit: contain;
             border-radius: 8px; background: #f1f3f6; }
figcaption { color: #202938; font-size: 15px; line-height: 1.8; margin-top: 0; }
p { font-size: 15px; line-height: 1.86; margin: 0 0 15px; overflow-wrap: anywhere; }
.byline { font-weight: 650; }
.source-line { font-size: 14px; margin-top: 18px; color: #475569; }
a { color: #1756a9; overflow-wrap: anywhere; }
.tag { display: inline-block; font-size: 12px; border-radius: 6px;
       padding: 3px 7px; background: #edf3fc; color: #234879; }
.review { background: #fff0dc; color: #8b5608; }
@media (max-width: 600px) { main { padding: 22px 12px 50px; }
  article { padding: 20px 17px; } h1 { font-size: 24px; } h2 { font-size: 18px; } }
@media print { :root { background: white; } main { padding: 0; }
  article { border: 0; border-radius: 0; break-inside: avoid; padding: 15px 0; }
  .source-line a { color: black; } }
"""


def safe_link(value):
    link = str(value or "").strip()
    return escape(link, quote=True) if urlsplit(link).scheme.lower() in ("http", "https") else ""


def render_article(article, asset=None, decision="KEEP"):
    title = escape(str(article.title or ""))
    source = escape(str(article.source or ""))
    stamp = escape(str(article.published_at or "날짜 미상"))
    byline = escape(make_byline(article.source, article.reporter))
    caption = format_caption(article.image_caption)
    paragraphs = excerpt_paragraphs(article)
    link = safe_link(public_article_url(article.url))
    tag = (
        "<span class='tag review'>REVIEW · 확인 필요</span>"
        if decision == "REVIEW" else "<span class='tag'>KEEP</span>"
    )

    html = [
        "<article>", f"<h2>{title}</h2>",
        f"<div class='meta'>{source} · {stamp} &nbsp; {tag}</div>",
        "<section class='post-body'>",
    ]
    for subtitle in article_subheadings(article):
        html.append(
            f"<p><strong>{escape(subtitle)}</strong></p>"
        )

    if asset:
        html.append(
            f'<figure><img src="{asset.data_uri}" alt="{title}">'
            + (
                f"<figcaption>{escape(caption)}</figcaption>"
                if caption else ""
            )
            + "</figure>"
        )

    if paragraphs:
        html.append(f"<p><span class='byline'>{byline}</span> {escape(paragraphs[0])}</p>")
        html.extend(f"<p>{escape(text)}</p>" for text in paragraphs[1:])
    else:
        html.append(f"<p><span class='byline'>{byline}</span> [본문 발췌 실패: 원문 확인 필요]</p>")

    # KPSEA 게시판과 비슷한 출처 표기이며 URL도 텍스트로 표시한다.
    if link:
        html.append(
            f'<p class="source-line">원문출처 : [{source}][{stamp}] '
            f'<a href="{link}" target="_blank" rel="noopener noreferrer">{link}</a></p>'
        )
    html.extend(["</section>", "</article>"])
    return "\n".join(html)


def save_html_report(articles, image_assets, decisions_by_url, start_date, end_date):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"news_{start_date}_to_{end_date}.html"
    keep = sum(decisions_by_url.get(a.url) == "KEEP" for a in articles)
    review = sum(decisions_by_url.get(a.url) == "REVIEW" for a in articles)
    cards = "\n".join(
        render_article(a, image_assets.get(a.url), decisions_by_url.get(a.url, "KEEP"))
        for a in articles
    )
    text = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research Monitor | {start_date} ~ {end_date}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
  <h1>Research Monitor</h1>
  <div class="sub">{start_date} ~ {end_date} · {len(articles)}건 · KEEP {keep} / REVIEW {review}</div>
  <div class="sub">원문 앞부분을 최대 3문단 발췌했습니다. 긴 문단은 문장 경계에서 나눌 수 있습니다.</div>
</header>
{cards}
</main>
</body>
</html>"""
    path.write_text(text, encoding="utf-8")
    return path
