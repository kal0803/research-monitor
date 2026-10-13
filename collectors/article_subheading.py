"""기사 원문에서 명시적인 소제목을 보수적으로 추출한다."""

import re
from bs4 import Tag

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

SUBTITLE_NAME = re.compile(
    r"(?:sub[-_ ]?(?:tit(?:le)?|head(?:line)?|ject)|"
    r"(?:tit(?:le)?|head(?:line)?|subject)[-_ ]?sub|"
    r"news[-_ ]?deck|article[-_ ]?deck)",
    re.I,
)

REJECT_TEXT = re.compile(
    r"기자|무단전재|저작권|댓글|관련기사|기사입력|기사수정|"
    r"공유하기|글자크기|폰트|로그인|구독|"
    r"\b(?:SNS|URL)\b|"
    r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}",
    re.I,
)


def clean(value):
    return " ".join(str(value or "").split())


def acceptable(tag, title, existing, seen):
    if not isinstance(tag, Tag):
        return False

    if tag.find_parent(["nav", "footer", "aside"]):
        return False

    if tag.find(["a", "img", "time", "button"]):
        return False

    text = clean(tag.get_text(" ", strip=True))

    return (
        5 <= len(text) <= 140
        and text != title
        and text not in existing
        and text not in seen
        and not REJECT_TEXT.search(text)
    )


def extract_page_subheadings(soup, article):
    title = clean(article.title)
    existing = {
        clean(p) for p in (article.paragraphs or [])
    }

    found = []
    seen = set()

    def add(tag):
        if acceptable(tag, title, existing, seen):
            text = clean(tag.get_text(" ", strip=True))
            found.append(text)
            seen.add(text)
            return True
        return False

    # 1. 기존의 명시적인 소제목 클래스
    for selector in SUBTITLE_SELECTORS:
        for tag in soup.select(selector):
            add(tag)

            if len(found) >= 3:
                return found

    if article.source != "대한경제":
        return found

    # 2. 대한경제: 제목 근처 HTML 구조 탐색
    heading = next(
        (
            tag
            for tag in soup.find_all(["h1", "h2", "h3"])
            if clean(tag.get_text(" ", strip=True)) == title
        ),
        None,
    )

    if heading is None:
        return found

    anchors = [heading]

    if isinstance(heading.parent, Tag):
        anchors.append(heading.parent)

    if (
        isinstance(heading.parent, Tag)
        and isinstance(heading.parent.parent, Tag)
    ):
        anchors.append(heading.parent.parent)

    for anchor in anchors:
        if (
            anchor is not heading
            and len(clean(anchor.get_text(" ", strip=True))) > 1000
        ):
            continue

        count = 0

        for sibling in anchor.next_siblings:
            if not isinstance(sibling, Tag):
                continue

            count += 1
            if count > 4:
                break

            candidates = [sibling] + sibling.find_all(
                ["div", "p", "span", "h2", "h3", "h4"],
                limit=12,
            )

            for tag in candidates:
                class_name = " ".join(tag.get("class", []))
                element_id = str(tag.get("id") or "")
                name = class_name + " " + element_id

                hinted = bool(SUBTITLE_NAME.search(name))
                heading_like = (
                    tag is sibling
                    and tag.name in ("h2", "h3", "h4")
                )

                if hinted or heading_like:
                    add(tag)

                    if len(found) >= 3:
                        return found

    return found


DNEWS_BYLINE = re.compile(r"\[\s*대한경제\s*=")

DNEWS_NON_SUBTITLE = re.compile(
    r"^(?:기사\s*입력|기사\s*수정|폰트\s*크기|글자\s*크기|"
    r"사진\s*[=:]|이미지|공유|댓글|광고|관련기사|"
    r"로그인|구독|이전기사|다음기사)|"
    r"^\d{4}[./-]\d{1,2}[./-]\d{1,2}|"
    r"무단전재|재배포금지|저작권|SNS|기사제보|"
    r"글자크기|폰트크기",
    re.I,
)


def extract_dnews_prebody_subheadings(soup, article):
    """대한경제 제목~첫 기자명 사이에 표시된 부제목을 보존."""
    lines = [
        clean(line)
        for line in soup.get_text("\n", strip=True).splitlines()
    ]
    lines = [line for line in lines if line]

    title = clean(article.title)
    title_indices = [
        i for i, line in enumerate(lines)
        if line == title
    ]
    byline_indices = [
        i for i, line in enumerate(lines)
        if DNEWS_BYLINE.search(line)
    ]

    # 기자명과 가까운 기사 제목을 기준으로 영역을 결정한다.
    pair = next(
        (
            (i, j)
            for j in byline_indices
            for i in reversed(title_indices)
            if i < j and 1 < j - i <= 80
        ),
        None,
    )

    if pair is None:
        return []

    start, end = pair
    region = lines[start + 1:end]

    # 대한경제의 소제목은 일반적으로 기사입력 시간 아래에 있다.
    date_index = next(
        (
            k for k, line in enumerate(region[:18])
            if re.search(r"기사\s*입력", line)
        ),
        None,
    )
    if date_index is not None:
        region = region[date_index + 1:]

    existing = {
        clean(article.title),
        clean(article.image_caption),
        *(clean(p) for p in (article.paragraphs or [])),
    }

    subtitles = []

    for line in region:
        if not 5 <= len(line) <= 145:
            continue
        if line in existing:
            continue
        if DNEWS_NON_SUBTITLE.search(line):
            continue
        if line not in subtitles:
            subtitles.append(line)

    # 너무 많은 후보는 영역을 잘못 인식한 것으로 간주한다.
    if len(subtitles) > 8:
        return []

    return subtitles
