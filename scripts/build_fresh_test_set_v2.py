import argparse
import csv
import json
import random
import sqlite3
from pathlib import Path


DB_PATH = Path("data/articles.db")

# 이미 Rubric 개발에 사용된 기사 ID/URL/제목을 제외하기 위해 확인할 파일들.
USED_LABEL_FILES = [
    Path("data/human_labels_v1.csv"),
    Path("data/human_labels_train_v1.csv"),
    Path("data/human_labels_validation_v1.csv"),
    Path("data/human_labels_dev_v1_audited.csv"),
    Path("data/human_labels_rubric_v2.csv"),
]

DEFAULT_OUTPUT_PATH = Path(
    "data/fresh_test_v2_labeling.csv"
)

DEFAULT_SAMPLE_SIZE = 50
DEFAULT_RANDOM_SEED = 20261005


def normalize_text(value):
    return str(value or "").strip()


def normalize_article_id(value):
    value = normalize_text(value)

    if value.endswith(".0"):
        value = value[:-2]

    return value


def parse_paragraphs(raw_value):
    if not raw_value:
        return ""

    try:
        parsed = json.loads(raw_value)

    except json.JSONDecodeError:
        return normalize_text(raw_value)

    if isinstance(parsed, list):
        return "\n\n".join(
            normalize_text(item)
            for item in parsed
            if normalize_text(item)
        )

    return normalize_text(parsed)


def load_used_keys():
    used_ids = set()
    used_urls = set()
    used_titles = set()

    for path in USED_LABEL_FILES:
        if not path.exists():
            continue

        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            reader = csv.DictReader(file)

            for row in reader:
                article_id = normalize_article_id(
                    row.get("article_id", "")
                )
                url = normalize_text(
                    row.get("url", "")
                )
                title = normalize_text(
                    row.get("title", "")
                )

                if article_id:
                    used_ids.add(article_id)

                if url:
                    used_urls.add(url)

                if title:
                    used_titles.add(title)

    return used_ids, used_urls, used_titles


def load_db_articles():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DB 파일이 없습니다: {DB_PATH}"
        )

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            source,
            title,
            reporter,
            published_at,
            image_caption,
            paragraphs,
            url
        FROM articles
        ORDER BY id ASC
        """
    )

    rows = []

    for row in cursor.fetchall():
        rows.append(
            {
                "article_id":
                    normalize_article_id(row["id"]),
                "source":
                    normalize_text(row["source"]),
                "title":
                    normalize_text(row["title"]),
                "reporter":
                    normalize_text(row["reporter"]),
                "published_at":
                    normalize_text(row["published_at"]),
                "image_caption":
                    normalize_text(row["image_caption"]),
                "body":
                    parse_paragraphs(row["paragraphs"]),
                "url":
                    normalize_text(row["url"]),
            }
        )

    connection.close()

    return rows


def is_used(
    article,
    used_ids,
    used_urls,
    used_titles,
):
    article_id = article["article_id"]
    url = article["url"]
    title = article["title"]

    if article_id and article_id in used_ids:
        return True

    if url and url in used_urls:
        return True

    if title and title in used_titles:
        return True

    return False


def write_labeling_csv(
    path,
    articles,
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "test_order",
        "article_id",
        "source",
        "title",
        "reporter",
        "published_at",
        "url",
        "image_caption",
        "body",
        "human_label",
        "human_note",
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for index, article in enumerate(
            articles,
            start=1,
        ):
            writer.writerow(
                {
                    "test_order": index,
                    **article,
                    "human_label": "",
                    "human_note": "",
                }
            )


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Rubric v2용 fresh holdout test set을 "
            "기존 DB의 미사용 기사에서 생성합니다."
        )
    )

    parser.add_argument(
        "--n",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="추출할 기사 수 (기본 50)",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help="재현 가능한 random seed",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="출력 CSV 경로",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.n <= 0:
        raise ValueError("--n은 1 이상이어야 합니다.")

    used_ids, used_urls, used_titles = (
        load_used_keys()
    )

    all_articles = load_db_articles()

    unseen_articles = [
        article
        for article in all_articles
        if not is_used(
            article,
            used_ids,
            used_urls,
            used_titles,
        )
    ]

    print()
    print("Fresh test set 생성 시작")
    print(f"DB 전체 기사: {len(all_articles)}개")
    print(
        "개발/라벨링에 사용된 article_id: "
        f"{len(used_ids)}개"
    )
    print(
        "현재 DB에서 완전히 미사용 기사: "
        f"{len(unseen_articles)}개"
    )

    if len(unseen_articles) < args.n:
        raise RuntimeError(
            f"미사용 기사가 {len(unseen_articles)}개라 "
            f"요청한 {args.n}개를 만들 수 없습니다.\n"
            "새 날짜 범위를 crawler로 추가 수집한 뒤 "
            "이 스크립트를 다시 실행하세요."
        )

    rng = random.Random(args.seed)

    selected = rng.sample(
        unseen_articles,
        args.n,
    )

    # 샘플링 후에는 사용자가 보기 편하게 날짜/ID 순으로 정렬.
    # 표본 선택 자체는 위의 random sample에서 이미 완료됨.
    selected.sort(
        key=lambda item: (
            item["published_at"],
            int(item["article_id"])
            if item["article_id"].isdigit()
            else item["article_id"],
        )
    )

    write_labeling_csv(
        args.output,
        selected,
    )

    selected_ids = {
        row["article_id"]
        for row in selected
    }

    overlap = selected_ids & used_ids

    if overlap:
        raise RuntimeError(
            "데이터 누수 감지: 기존 사용 article_id와 "
            f"겹칩니다: {sorted(overlap)}"
        )

    remaining_reserve = (
        len(unseen_articles)
        - len(selected)
    )

    print()
    print("Fresh test set 생성 완료")
    print(f"Test articles: {len(selected)}개")
    print(
        f"남겨둔 unseen reserve: "
        f"{remaining_reserve}개"
    )
    print(f"Random seed: {args.seed}")
    print(f"출력: {args.output}")
    print()
    print(
        "중요: human_label 열에 모든 기사에 대해 "
        "KEEP 또는 DROP을 명시적으로 입력하세요."
    )
    print(
        "빈칸을 KEEP으로 간주하지 않습니다. "
        "라벨링 완료 전에는 Rubric v2 결과를 보지 마세요."
    )


if __name__ == "__main__":
    main()
