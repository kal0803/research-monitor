from models import Article


def shorten_text(
    text: str,
    limit: int = 300
) -> str:
    """
    긴 텍스트를 audit 화면에서
    확인하기 좋은 길이로 줄인다.
    """

    if not text:
        return "(없음)"

    text = text.strip()

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def audit_article(
    article: Article
) -> str:
    """
    Article 한 건의 파싱 상태를
    사람이 확인할 수 있는 형태로 출력한다.
    """

    output = []

    output.append(
        f"SOURCE: {article.source}"
    )

    output.append(
        f"TITLE: {article.title or '(없음)'}"
    )

    output.append(
        f"DATE: {article.published_at or '(없음)'}"
    )

    output.append(
        f"REPORTER: {article.reporter or '(없음)'}"
    )

    output.append(
        "CAPTION: "
        + (
            article.image_caption
            if article.image_caption
            else "(없음)"
        )
    )

    output.append(
        f"PARAGRAPHS: "
        f"{len(article.paragraphs or [])}"
    )

    output.append("")

    output.append(
        "FIRST PARAGRAPH:"
    )

    if article.paragraphs:
        output.append(
            shorten_text(
                article.paragraphs[0]
            )
        )
    else:
        output.append("(없음)")

    output.append("")

    output.append(
        "SECOND PARAGRAPH:"
    )

    if (
        article.paragraphs
        and len(article.paragraphs) >= 2
    ):
        output.append(
            shorten_text(
                article.paragraphs[1]
            )
        )
    else:
        output.append("(없음)")

    output.append("")

    output.append(
        f"URL: {article.url}"
    )

    return "\n".join(output)


def audit_articles_by_source(
    articles: list[Article],
    samples_per_source: int = 2,
) -> str:
    """
    각 언론사별로 몇 개의 기사만 뽑아
    파싱 결과를 확인한다.
    """

    grouped = {}

    for article in articles:
        grouped.setdefault(
            article.source,
            []
        ).append(article)

    output = []

    for source, source_articles in grouped.items():

        output.append(
            "=" * 80
        )

        output.append(
            f"[{source}]"
        )

        output.append(
            f"수집 기사 수: "
            f"{len(source_articles)}"
        )

        output.append(
            "=" * 80
        )

        output.append("")

        samples = source_articles[
            :samples_per_source
        ]

        for index, article in enumerate(
            samples,
            start=1
        ):
            output.append(
                f"[샘플 {index}]"
            )

            output.append(
                audit_article(
                    article
                )
            )

            output.append("")
            output.append(
                "-" * 80
            )
            output.append("")

    return "\n".join(output)
