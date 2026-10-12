import csv
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from collectors.kpsea_history import (
    collect_page_articles,
)


OUTPUT_PATH = Path(
    "data/kpsea_positive_articles.csv"
)

FAILED_PAGES_PATH = Path(
    "data/kpsea_failed_pages.txt"
)

ARTICLES_PER_PAGE = 15

MAX_RETRIES = 3

RETRY_DELAY_SECONDS = 3

MAX_CONSECUTIVE_EMPTY_PAGES = 5


def load_existing_data():
    """
    기존 CSV에서:
    - 이미 저장된 KPSEA URL
    - 저장된 행 수
    - 가장 큰 page 번호

    를 확인한다.
    """

    if not OUTPUT_PATH.exists():
        return set(), 0, 0

    urls = set()

    row_count = 0

    detected_pages = []

    with OUTPUT_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            row_count += 1

            url = (
                row.get("kpsea_url")
                or ""
            ).strip()

            if not url:
                continue

            urls.add(url)

            try:
                parsed = urlparse(url)

                query = parse_qs(
                    parsed.query
                )

                page_values = query.get(
                    "page"
                )

                if page_values:

                    page_number = int(
                        page_values[0]
                    )

                    detected_pages.append(
                        page_number
                    )

            except (
                ValueError,
                TypeError,
            ):
                pass

    # URL의 page 파라미터로
    # 마지막 페이지를 찾는 것이 1순위
    if detected_pages:

        last_page = max(
            detected_pages
        )

    else:

        # 과거 CSV에 page 파라미터가 없다면
        # 저장 기사 수를 이용한 fallback
        last_page = (
            row_count
            // ARTICLES_PER_PAGE
        )

    return (
        urls,
        row_count,
        last_page,
    )


def append_articles(
    articles,
    existing_urls,
):
    """
    새 기사만 CSV에 추가한다.
    """

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_exists = (
        OUTPUT_PATH.exists()
        and OUTPUT_PATH.stat().st_size > 0
    )

    fieldnames = [
        "kpsea_url",
        "title",
        "published_at",
        "source",
        "original_url",
        "body",
    ]

    new_count = 0

    with OUTPUT_PATH.open(
        "a",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        if not file_exists:
            writer.writeheader()

        for article in articles:

            url = (
                article.get(
                    "kpsea_url"
                )
                or ""
            ).strip()

            if not url:
                continue

            if url in existing_urls:
                continue

            writer.writerow(
                article
            )

            existing_urls.add(
                url
            )

            new_count += 1

    return new_count


def save_failed_page(
    page_number,
    reason,
):
    """
    수집에 실패하거나 비어 있던 페이지를
    별도 파일에 기록한다.
    """

    FAILED_PAGES_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with FAILED_PAGES_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            f"{page_number}\t{reason}\n"
        )


def collect_page_with_retry(
    page_number,
):
    """
    한 페이지 수집을 최대 MAX_RETRIES번 재시도한다.

    Playwright가 일시적으로 빈 페이지를 받는 경우를
    마지막 페이지로 오판하지 않도록 한다.
    """

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            print(
                f"  시도 "
                f"{attempt}/{MAX_RETRIES}"
            )

            articles = (
                collect_page_articles(
                    page_number
                )
            )

            if articles:

                return (
                    articles,
                    None,
                )

            last_error = (
                "게시글 0개"
            )

        except Exception as e:

            last_error = str(e)

        if attempt < MAX_RETRIES:

            print(
                f"  재시도 대기 "
                f"{RETRY_DELAY_SECONDS}초..."
            )

            time.sleep(
                RETRY_DELAY_SECONDS
            )

    return (
        [],
        last_error,
    )


def ask_collection_limit(
    start_page,
):
    """
    이어서 몇 페이지를 수집할지 결정한다.

    Enter:
        마지막 페이지까지 자동 진행

    숫자:
        해당 개수만큼만 테스트

    예:
        시작 페이지 244에서
        3 입력 → 244~246페이지
    """

    print()
    print(
        f"이번 시작 페이지: "
        f"{start_page}"
    )

    print()
    print(
        "테스트하려면 "
        "추가로 수집할 페이지 수를 입력하세요."
    )

    print(
        "전체 남은 데이터를 수집하려면 "
        "Enter를 누르세요."
    )

    value = input(
        "추가 수집 페이지 수: "
    ).strip()

    if not value:
        return None

    try:

        page_count = int(
            value
        )

    except ValueError:

        raise ValueError(
            "페이지 수는 숫자로 "
            "입력해야 합니다."
        )

    if page_count < 1:

        raise ValueError(
            "페이지 수는 "
            "1 이상이어야 합니다."
        )

    return page_count


def main():

    print()
    print(
        "KPSEA positive corpus 이어서 구축"
    )

    (
        existing_urls,
        existing_row_count,
        last_saved_page,
    ) = load_existing_data()

    if last_saved_page > 0:

        start_page = (
            last_saved_page + 1
        )

    else:

        start_page = 1

    print()
    print(
        f"기존 저장 기사: "
        f"{existing_row_count:,}개"
    )

    print(
        f"기존 저장 URL: "
        f"{len(existing_urls):,}개"
    )

    print(
        f"마지막 확인 페이지: "
        f"{last_saved_page}"
    )

    print(
        f"이어갈 페이지: "
        f"{start_page}"
    )

    try:

        page_count = (
            ask_collection_limit(
                start_page
            )
        )

    except ValueError as e:

        print(
            f"입력 오류: {e}"
        )

        return

    if page_count is None:

        end_page = None

        print()
        print(
            "수집 모드: "
            "남은 전체 페이지"
        )

    else:

        end_page = (
            start_page
            + page_count
            - 1
        )

        print()
        print(
            f"수집 모드: "
            f"{start_page} ~ "
            f"{end_page}페이지"
        )

    print()

    total_new = 0

    page_number = start_page

    consecutive_empty_pages = 0

    empty_pages = []

    while True:

        if (
            end_page is not None
            and page_number > end_page
        ):
            break

        print(
            f"[페이지 {page_number}] "
            "수집 중..."
        )

        (
            articles,
            error,
        ) = collect_page_with_retry(
            page_number
        )

        if not articles:

            consecutive_empty_pages += 1

            empty_pages.append(
                page_number
            )

            print(
                f"  게시글 없음 또는 실패"
            )

            print(
                f"  연속 빈 페이지: "
                f"{consecutive_empty_pages}/"
                f"{MAX_CONSECUTIVE_EMPTY_PAGES}"
            )

            if error:

                print(
                    f"  원인: {error}"
                )

            save_failed_page(
                page_number,
                error or "empty",
            )

            # 한 페이지가 비어 있다고
            # 전체 수집을 종료하지 않는다.
            if (
                consecutive_empty_pages
                >=
                MAX_CONSECUTIVE_EMPTY_PAGES
            ):

                print()
                print(
                    f"연속 "
                    f"{MAX_CONSECUTIVE_EMPTY_PAGES}"
                    "페이지에서 "
                    "게시글을 찾지 못했습니다."
                )

                print(
                    "마지막 페이지에 "
                    "도달한 것으로 판단합니다."
                )

                break

            page_number += 1

            continue

        # 정상적인 페이지가 다시 나타났으면
        # empty count 초기화
        consecutive_empty_pages = 0

        new_count = append_articles(
            articles,
            existing_urls,
        )

        total_new += new_count

        print(
            f"  상세 수집: "
            f"{len(articles)}개"
        )

        print(
            f"  신규 저장: "
            f"{new_count}개"
        )

        print(
            f"  현재 누적: "
            f"{len(existing_urls):,}개"
        )

        print()

        page_number += 1

    print()
    print(
        "KPSEA positive corpus "
        "수집 종료"
    )

    print(
        f"이번 실행 신규 기사: "
        f"{total_new:,}개"
    )

    print(
        f"누적 기사: "
        f"{len(existing_urls):,}개"
    )

    if empty_pages:

        print()
        print(
            "수집 중 비었거나 "
            "실패한 페이지:"
        )

        print(
            ", ".join(
                str(page)
                for page in empty_pages
            )
        )

        print(
            f"기록 파일: "
            f"{FAILED_PAGES_PATH}"
        )

    print()
    print(
        f"데이터 파일: "
        f"{OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
