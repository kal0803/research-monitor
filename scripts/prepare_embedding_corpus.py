import csv
import hashlib
import re
from pathlib import Path


INPUT_PATH = Path(
    "data/kpsea_positive_articles.csv"
)

OUTPUT_PATH = Path(
    "data/kpsea_embedding_input.csv"
)


def normalize_whitespace(text):
    """
    불필요한 공백을 정리한다.
    문단 구분은 유지한다.
    """

    if not text:
        return ""

    text = text.replace(
        "\r\n",
        "\n"
    ).replace(
        "\r",
        "\n"
    )

    paragraphs = []

    for paragraph in text.split("\n"):

        paragraph = re.sub(
            r"[ \t]+",
            " ",
            paragraph
        ).strip()

        if paragraph:
            paragraphs.append(
                paragraph
            )

    return "\n\n".join(
        paragraphs
    )


def make_embedding_text(
    title,
    body,
):
    """
    실제 embedding 모델에 보낼 텍스트 생성.
    """

    title = normalize_whitespace(
        title
    )

    body = normalize_whitespace(
        body
    )

    parts = []

    if title:
        parts.append(
            f"TITLE: {title}"
        )

    if body:
        parts.append(
            f"BODY:\n{body}"
        )

    return "\n\n".join(
        parts
    )


def make_content_hash(
    title,
    body,
):
    """
    동일한 title + body 중복을 찾기 위한
    SHA-256 fingerprint 생성.
    """

    normalized_title = (
        normalize_whitespace(
            title
        ).lower()
    )

    normalized_body = (
        normalize_whitespace(
            body
        ).lower()
    )

    raw = (
        normalized_title
        + "\n"
        + normalized_body
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


def main():

    print()
    print(
        "KPSEA embedding corpus 준비 시작"
    )
    print()

    # -------------------------
    # 입력 파일 확인
    # -------------------------

    if not INPUT_PATH.exists():

        print(
            "입력 파일을 찾을 수 없습니다."
        )

        print(
            f"필요한 파일: {INPUT_PATH}"
        )

        return

    # -------------------------
    # 원본 CSV 읽기
    # -------------------------

    rows = []

    with INPUT_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:
            rows.append(
                row
            )

    print(
        f"원본 기사 수: "
        f"{len(rows):,}개"
    )

    # -------------------------
    # 전처리
    # -------------------------

    output_rows = []

    seen_hashes = set()

    missing_title = 0
    missing_body = 0
    short_body = 0
    exact_duplicates = 0
    unusable = 0

    for row in rows:

        title = normalize_whitespace(
            row.get(
                "title",
                ""
            )
        )

        body = normalize_whitespace(
            row.get(
                "body",
                ""
            )
        )

        if not title:
            missing_title += 1

        if not body:
            missing_body += 1

        if len(body) < 100:
            short_body += 1

        # 제목과 본문이 둘 다 없으면
        # embedding 불가능
        if (
            not title
            and not body
        ):

            unusable += 1

            continue

        content_hash = (
            make_content_hash(
                title,
                body,
            )
        )

        # 완전히 동일한 기사 제거
        if content_hash in seen_hashes:

            exact_duplicates += 1

            continue

        seen_hashes.add(
            content_hash
        )

        embedding_text = (
            make_embedding_text(
                title,
                body,
            )
        )

        output_rows.append(
            {
                "article_id":
                    len(output_rows) + 1,

                "kpsea_url":
                    row.get(
                        "kpsea_url",
                        ""
                    ),

                "title":
                    title,

                "published_at":
                    row.get(
                        "published_at",
                        ""
                    ),

                "source":
                    row.get(
                        "source",
                        ""
                    ),

                "original_url":
                    row.get(
                        "original_url",
                        ""
                    ),

                "body_length":
                    len(body),

                "embedding_text":
                    embedding_text,
            }
        )

    # -------------------------
    # 출력 폴더 생성
    # -------------------------

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -------------------------
    # CSV 저장
    # -------------------------

    fieldnames = [
        "article_id",
        "kpsea_url",
        "title",
        "published_at",
        "source",
        "original_url",
        "body_length",
        "embedding_text",
    ]

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        writer.writerows(
            output_rows
        )

    # -------------------------
    # 결과 출력
    # -------------------------

    print()
    print(
        "[전처리 결과]"
    )

    print(
        f"제목 누락: "
        f"{missing_title:,}개"
    )

    print(
        f"본문 누락: "
        f"{missing_body:,}개"
    )

    print(
        f"본문 100자 미만: "
        f"{short_body:,}개"
    )

    print(
        f"완전 동일 기사 제거: "
        f"{exact_duplicates:,}개"
    )

    print(
        f"사용 불가 기사 제거: "
        f"{unusable:,}개"
    )

    print()

    print(
        f"Embedding 대상 기사: "
        f"{len(output_rows):,}개"
    )

    print(
        f"저장 위치: "
        f"{OUTPUT_PATH}"
    )

    # -------------------------
    # 샘플 출력
    # -------------------------

    if output_rows:

        print()
        print(
            "[Embedding text 샘플]"
        )

        print()

        sample_text = (
            output_rows[0]
            ["embedding_text"]
        )

        print(
            sample_text[:1000]
        )

        if len(sample_text) > 1000:
            print(
                "\n..."
            )

    print()
    print(
        "Embedding corpus 준비 완료"
    )


if __name__ == "__main__":
    main()
