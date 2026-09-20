from datetime import date, datetime

from collectors.dnews import (
    DNewsCollector,
)

from collectors.mtn import (
    MTNCollector,
)

from collectors.constimes import (
    ConTimesCollector,
)

from collectors.cenews import (
    CENewsCollector,
)

from collectors.cennews import (
    CENNewsCollector,
)

from collectors.safetynews import (
    SafetyNewsCollector,
)

from storage.database import (
    initialize_database,
    save_articles,
)

from formatters.news_formatter import (
    format_articles,
)

from formatters.text_writer import (
    save_text_report,
)


def parse_date_range(text):
    """
    날짜 입력 예시:

    09.19
    09.19~21
    09.19~09.21
    09.30~10.02

    연도는 현재 연도를 자동으로 사용한다.
    """

    current_year = date.today().year

    text = (
        text.strip()
        .replace(" ", "")
    )

    if "~" not in text:
        parsed = datetime.strptime(
            f"{current_year}.{text}",
            "%Y.%m.%d",
        ).date()

        return parsed, parsed

    start_text, end_text = (
        text.split("~", 1)
    )

    start_parts = (
        start_text.split(".")
    )

    if len(start_parts) != 2:
        raise ValueError(
            "시작 날짜 형식이 올바르지 않습니다."
        )

    start_month = int(
        start_parts[0]
    )

    start_day = int(
        start_parts[1]
    )

    if "." not in end_text:
        end_month = start_month
        end_day = int(
            end_text
        )

    else:
        end_parts = (
            end_text.split(".")
        )

        if len(end_parts) != 2:
            raise ValueError(
                "종료 날짜 형식이 올바르지 않습니다."
            )

        end_month = int(
            end_parts[0]
        )

        end_day = int(
            end_parts[1]
        )

    start_date = date(
        current_year,
        start_month,
        start_day,
    )

    end_date = date(
        current_year,
        end_month,
        end_day,
    )

    if end_date < start_date:
        raise ValueError(
            "종료 날짜가 시작 날짜보다 빠릅니다."
        )

    return (
        start_date,
        end_date,
    )


def ask_date_range():
    """
    올바른 날짜 범위가 입력될 때까지
    사용자에게 반복해서 요청한다.
    """

    while True:

        print()

        user_input = input(
            "수집 날짜를 입력하세요 "
            "(예: 09.19~21): "
        )

        try:
            return parse_date_range(
                user_input
            )

        except (
            ValueError,
            TypeError,
        ) as e:

            print()
            print(
                f"입력 오류: {e}"
            )

            print(
                "예시: "
                "09.19 / "
                "09.19~21 / "
                "09.30~10.02"
            )


def remove_unwanted_articles(
    articles
):
    """
    최종적으로 제외할 기사 규칙.
    """

    excluded_terms = (
        "한줄뉴스",
        "한줄 뉴스",
    )

    result = []

    for article in articles:

        if any(
            term in article.title
            for term in excluded_terms
        ):
            continue

        result.append(
            article
        )

    return result


def main():

    start_date, end_date = (
        ask_date_range()
    )

    print()
    print(
        f"수집 기간: "
        f"{start_date} ~ {end_date}"
    )

    initialize_database()

    collectors = [
        DNewsCollector(),
        MTNCollector(),
        ConTimesCollector(),
        CENewsCollector(),
        CENNewsCollector(),
        SafetyNewsCollector(),
    ]

    all_articles = []

    for collector in collectors:

        print()
        print(
            f"[{collector.source_name}] "
            "기사 수집 시작"
        )

        try:

            articles = (
                collector.collect_articles(
                    start_date=start_date,
                    end_date=end_date,
                )
            )

            all_articles.extend(
                articles
            )

            print(
                f"[{collector.source_name}] "
                f"{len(articles)}개 수집"
            )

        except Exception as e:

            print(
                f"[WARNING] "
                f"{collector.source_name} "
                "수집 실패"
            )

            print(e)

    all_articles = (
        remove_unwanted_articles(
            all_articles
        )
    )

    all_articles.sort(
        key=lambda article:
            article.published_at or "",
        reverse=True,
    )

    print()
    print(
        f"전체 수집 기사: "
        f"{len(all_articles)}개"
    )

    new_articles = save_articles(
        all_articles
    )

    print(
        f"신규 기사: "
        f"{len(new_articles)}개"
    )

    if not all_articles:

        print()
        print(
            "출력할 기사가 없습니다."
        )

        return

    formatted = format_articles(
        all_articles
    )

    output_path = save_text_report(
        content=formatted,
        start_date=start_date,
        end_date=end_date,
    )

    print()
    print(
        "TXT 파일 저장 완료:"
    )

    print(
        output_path
    )


if __name__ == "__main__":
    main()
    