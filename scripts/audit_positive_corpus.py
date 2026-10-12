import csv
import statistics
from pathlib import Path
from urllib.parse import urlparse


INPUT_PATH = Path(
    "data/kpsea_positive_articles.csv"
)


def clean(value):
    if value is None:
        return ""

    return value.strip()


def main():
    print()
    print("KPSEA positive corpus 품질 검사")
    print()

    if not INPUT_PATH.exists():
        print(
            f"파일을 찾을 수 없습니다: "
            f"{INPUT_PATH}"
        )
        return

    rows = []

    with INPUT_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        print(
            "CSV 열:",
            ", ".join(
                reader.fieldnames or []
            )
        )

        for row in reader:
            rows.append(row)

    total = len(rows)

    if total == 0:
        print("저장된 기사가 없습니다.")
        return

    print()
    print(f"총 기사 수: {total:,}")

    fields = [
        "kpsea_url",
        "title",
        "published_at",
        "source",
        "original_url",
        "body",
    ]

    print()
    print("[필드 누락]")

    for field in fields:

        missing = sum(
            1
            for row in rows
            if not clean(
                row.get(field)
            )
        )

        ratio = (
            missing / total * 100
        )

        print(
            f"{field}: "
            f"{missing:,}개 "
            f"({ratio:.1f}%)"
        )

    kpsea_urls = [
        clean(
            row.get("kpsea_url")
        )
        for row in rows
        if clean(
            row.get("kpsea_url")
        )
    ]

    original_urls = [
        clean(
            row.get("original_url")
        )
        for row in rows
        if clean(
            row.get("original_url")
        )
    ]

    duplicate_kpsea = (
        len(kpsea_urls)
        - len(set(kpsea_urls))
    )

    duplicate_original = (
        len(original_urls)
        - len(set(original_urls))
    )

    print()
    print("[중복]")

    print(
        f"KPSEA URL 중복: "
        f"{duplicate_kpsea:,}"
    )

    print(
        f"원문 URL 중복: "
        f"{duplicate_original:,}"
    )

    body_lengths = [
        len(
            clean(
                row.get("body")
            )
        )
        for row in rows
    ]

    nonempty_lengths = [
        length
        for length in body_lengths
        if length > 0
    ]

    short_50 = sum(
        length < 50
        for length in body_lengths
    )

    short_100 = sum(
        length < 100
        for length in body_lengths
    )

    short_300 = sum(
        length < 300
        for length in body_lengths
    )

    print()
    print("[본문 길이]")

    print(
        f"50자 미만: "
        f"{short_50:,}개"
    )

    print(
        f"100자 미만: "
        f"{short_100:,}개"
    )

    print(
        f"300자 미만: "
        f"{short_300:,}개"
    )

    if nonempty_lengths:

        print(
            f"평균 길이: "
            f"{statistics.mean(nonempty_lengths):,.0f}자"
        )

        print(
            f"중앙값 길이: "
            f"{statistics.median(nonempty_lengths):,.0f}자"
        )

        print(
            f"최소 길이: "
            f"{min(nonempty_lengths):,}자"
        )

        print(
            f"최대 길이: "
            f"{max(nonempty_lengths):,}자"
        )

    sources = {}

    for row in rows:

        source = clean(
            row.get("source")
        )

        if not source:
            source = "(누락)"

        sources[source] = (
            sources.get(
                source,
                0
            )
            + 1
        )

    print()
    print("[언론사 상위 15개]")

    sorted_sources = sorted(
        sources.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    for source, count in sorted_sources[:15]:

        print(
            f"{source}: "
            f"{count:,}개"
        )

    domains = {}

    for url in original_urls:

        try:

            domain = (
                urlparse(url)
                .netloc
                .lower()
            )

        except Exception:
            continue

        if not domain:
            continue

        domains[domain] = (
            domains.get(
                domain,
                0
            )
            + 1
        )

    print()
    print("[원문 도메인 상위 15개]")

    sorted_domains = sorted(
        domains.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    for domain, count in sorted_domains[:15]:

        print(
            f"{domain}: "
            f"{count:,}개"
        )

    print()
    print("[샘플 기사 3개]")

    for index, row in enumerate(
        rows[:3],
        start=1,
    ):

        print()
        print(
            f"--- SAMPLE {index} ---"
        )

        print(
            "제목:",
            clean(
                row.get("title")
            )
        )

        print(
            "날짜:",
            clean(
                row.get(
                    "published_at"
                )
            )
        )

        print(
            "언론사:",
            clean(
                row.get("source")
            )
        )

        print(
            "원문:",
            clean(
                row.get(
                    "original_url"
                )
            )
        )

        body = clean(
            row.get("body")
        )

        print(
            "본문 길이:",
            len(body)
        )

        print(
            "본문 앞 300자:"
        )

        print(
            body[:300]
        )

    print()
    print(
        "품질 검사 완료"
    )


if __name__ == "__main__":
    main()
