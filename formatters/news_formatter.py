from models import Article


def format_caption(caption):
    """
    이미지 캡션을 출력용으로 정리한다.
    캡션이 없으면 None을 반환한다.
    """

    if not caption:
        return None

    return caption.strip()


def format_article(article: Article) -> str:
    """
    Article 하나를 지정된 뉴스 출력 양식으로 변환한다.

    출력 형식:

    **제목**

    이미지 캡션

    [언론사명=OOO 기자]본문 첫 문단

    본문 두 번째 문단

    본문 세 번째 문단

    원문보기[언론사명] URL
    """

    output = []

    # 1. 제목
    output.append(
        f"**{article.title}**"
    )

    # 제목과 캡션 사이 빈 줄
    output.append("")

    # 2. 이미지 캡션
    caption = format_caption(
        article.image_caption
    )

    if caption:
        output.append(caption)
        output.append("")

    # 3. 기자 명선
    if article.reporter:
        byline = (
            f"[{article.source}="
            f"{article.reporter} 기자]"
        )
    else:
        byline = (
            f"[{article.source}]"
        )

    # 4. 본문
    paragraphs = (
        article.paragraphs or []
    )

    if paragraphs:

        first_paragraph = (
            paragraphs[0].strip()
        )

        if first_paragraph:
            output.append(
                f"{byline}{first_paragraph}"
            )
        else:
            output.append(byline)

        for paragraph in paragraphs[1:]:

            paragraph = paragraph.strip()

            if not paragraph:
                continue

            # 문단 사이 빈 줄 하나
            output.append("")
            output.append(paragraph)

    else:
        output.append(byline)

    # 5. 원문 링크 앞 빈 줄
    output.append("")

    output.append(
        f"원문보기[{article.source}] "
        f"{article.url}"
    )

    return "\n".join(output)


def format_articles(
    articles: list[Article]
) -> str:
    """
    여러 기사를 하나의 텍스트로 변환한다.

    기사와 기사 사이에는
    빈 줄 두 개를 둔다.
    """

    formatted_articles = []

    for article in articles:

        formatted = format_article(
            article
        )

        formatted_articles.append(
            formatted
        )

    return "\n\n\n".join(
        formatted_articles
    )
