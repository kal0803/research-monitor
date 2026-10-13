"""기사 원문 HTML에서 명시적인 부제목을 보수적으로 추출한다."""

import re


SUBTITLE_SELECTORS = (
    ".article-view-header .sub-title",
    ".article-view-header .subtitle",
    ".article-view-header .article-sub-title",
    ".article-head .sub-title",
    ".article-head .subtitle",
    ".article-header .sub-title",
    ".article-header .subtitle",
    ".article-sub-title",
    ".article-subtitle",
    ".article-subject-sub",
    ".news-subtitle",
    ".view-subtitle",
    ".view-subject-sub",
    "h2.sub-title",
    "h2.subtitle",
    "p.sub-title",
)


def extract_page_subheadings(soup, article):
    title = " ".join(str(article.title or "").split())
    existing = {
        " ".join(str(p).split())
        for p in (article.paragraphs or [])
    }

    result = []
    seen = set()

    for selector in SUBTITLE_SELECTORS:
        for tag in soup.select(selector):
            if tag.find_parent(["nav", "footer"]):
                continue

            text = " ".join(tag.get_text(" ", strip=True).split())

            if not 5 <= len(text) <= 140:
                continue
            if text == title or text in existing or text in seen:
                continue
            if tag.find("a", href=True):
                continue
            if re.search(
                r"기자|무단전재|저작권|댓글|관련기사|기사입력|"
                r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}",
                text,
            ):
                continue

            seen.add(text)
            result.append(text)

            if len(result) >= 3:
                return result

    return result
