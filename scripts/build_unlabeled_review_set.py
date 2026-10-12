import csv
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI


DB_PATH = Path(
    "data/articles.db"
)

POSITIVE_EMBEDDINGS_PATH = Path(
    "data/kpsea_embeddings.npy"
)

POSITIVE_METADATA_PATH = Path(
    "data/kpsea_embedding_metadata.csv"
)

OUTPUT_PATH = Path(
    "outputs/unlabeled_review_set.csv"
)


MODEL = "text-embedding-3-small"

TOP_K_POSITIVES = 5

REVIEW_SAMPLE_PER_GROUP = 30


def load_client():
    load_dotenv()

    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY를 찾을 수 없습니다."
        )

    return OpenAI(
        api_key=api_key
    )


def load_unlabeled_articles():
    """
    현재 crawler가 SQLite에 저장한 기사들을 읽는다.
    """

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DB가 없습니다: {DB_PATH}"
        )

    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            source,
            title,
            published_at,
            paragraphs,
            url
        FROM articles
        ORDER BY id
        """
    )

    rows = cursor.fetchall()

    connection.close()

    articles = []

    for row in rows:

        paragraphs_raw = (
            row["paragraphs"]
            or "[]"
        )

        try:
            paragraphs = json.loads(
                paragraphs_raw
            )
        except json.JSONDecodeError:
            paragraphs = []

        if not isinstance(
            paragraphs,
            list
        ):
            paragraphs = []

        body = "\n\n".join(
            str(paragraph).strip()
            for paragraph in paragraphs
            if str(paragraph).strip()
        )

        title = (
            row["title"]
            or ""
        ).strip()

        if not title:
            continue

        embedding_text = (
            f"TITLE: {title}"
        )

        if body:
            embedding_text += (
                f"\n\nBODY:\n{body}"
            )

        articles.append(
            {
                "id": row["id"],
                "source": (
                    row["source"]
                    or ""
                ),
                "title": title,
                "published_at": (
                    row["published_at"]
                    or ""
                ),
                "url": (
                    row["url"]
                    or ""
                ),
                "embedding_text":
                    embedding_text,
            }
        )

    return articles


def load_positive_metadata():
    rows = []

    with POSITIVE_METADATA_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:
            rows.append(row)

    return rows


def normalize_matrix(matrix):
    norms = np.linalg.norm(
        matrix,
        axis=1,
        keepdims=True,
    )

    norms[
        norms == 0
    ] = 1

    return (
        matrix / norms
    )


def create_embeddings(
    client,
    articles,
):
    """
    신규 기사들을 batch embedding한다.
    """

    batch_size = 50

    vectors = []

    for start in range(
        0,
        len(articles),
        batch_size,
    ):

        batch = articles[
            start:
            start + batch_size
        ]

        print(
            f"Embedding "
            f"{start + 1} ~ "
            f"{start + len(batch)}"
        )

        response = (
            client.embeddings.create(
                model=MODEL,
                input=[
                    article[
                        "embedding_text"
                    ]
                    for article
                    in batch
                ],
                encoding_format="float",
            )
        )

        vectors.extend(
            item.embedding
            for item in response.data
        )

    return np.asarray(
        vectors,
        dtype=np.float32,
    )


def calculate_positive_similarity(
    query_embeddings,
    positive_embeddings,
):
    """
    각 신규 기사에 대해
    가장 유사한 positive 사례들을 찾는다.

    대표 score는 top-5 similarity 평균을 사용한다.
    """

    query_embeddings = (
        normalize_matrix(
            query_embeddings
        )
    )

    positive_embeddings = (
        normalize_matrix(
            positive_embeddings
        )
    )

    similarities = (
        query_embeddings
        @ positive_embeddings.T
    )

    results = []

    for row in similarities:

        top_indices = (
            np.argsort(
                row
            )[::-1][
                :TOP_K_POSITIVES
            ]
        )

        top_scores = row[
            top_indices
        ]

        results.append(
            {
                "top_indices":
                    top_indices,

                "top_scores":
                    top_scores,

                "mean_top5_similarity":
                    float(
                        np.mean(
                            top_scores
                        )
                    ),

                "max_similarity":
                    float(
                        top_scores[0]
                    ),
            }
        )

    return results


def assign_distance_group(
    scores
):
    """
    아직 KEEP/DROP을 만들지 않는다.

    신규 기사 전체의 similarity 분포를 기준으로
    가까움 / 경계 / 멂 세 그룹으로만 나눈다.
    """

    scores_array = np.asarray(
        scores
    )

    lower = float(
        np.quantile(
            scores_array,
            0.33
        )
    )

    upper = float(
        np.quantile(
            scores_array,
            0.67
        )
    )

    groups = []

    for score in scores:

        if score >= upper:
            groups.append(
                "CLOSE"
            )

        elif score <= lower:
            groups.append(
                "FAR"
            )

        else:
            groups.append(
                "BOUNDARY"
            )

    return (
        groups,
        lower,
        upper,
    )


def build_output_rows(
    articles,
    similarity_results,
    groups,
    positive_metadata,
):
    rows = []

    for (
        article,
        similarity,
        group,
    ) in zip(
        articles,
        similarity_results,
        groups,
    ):

        top_indices = (
            similarity[
                "top_indices"
            ]
        )

        top_scores = (
            similarity[
                "top_scores"
            ]
        )

        output = {
            "article_id":
                article["id"],

            "group":
                group,

            "mean_top5_similarity":
                f"{similarity['mean_top5_similarity']:.4f}",

            "max_similarity":
                f"{similarity['max_similarity']:.4f}",

            "source":
                article["source"],

            "title":
                article["title"],

            "published_at":
                article[
                    "published_at"
                ],

            "url":
                article["url"],

            "human_label":
                "",

            "human_note":
                "",
        }

        for rank in range(
            TOP_K_POSITIVES
        ):

            positive_index = int(
                top_indices[rank]
            )

            positive = (
                positive_metadata[
                    positive_index
                ]
            )

            output[
                f"positive_{rank + 1}_title"
            ] = positive.get(
                "title",
                ""
            )

            output[
                f"positive_{rank + 1}_similarity"
            ] = (
                f"{top_scores[rank]:.4f}"
            )

        rows.append(
            output
        )

    return rows


def choose_review_sample(
    rows
):
    """
    CLOSE / BOUNDARY / FAR에서
    최대 30개씩 추출한다.

    특히 BOUNDARY를 사람이 보는 것이 중요하다.
    """

    selected = []

    for group in (
        "CLOSE",
        "BOUNDARY",
        "FAR",
    ):

        group_rows = [
            row
            for row in rows
            if row["group"]
            == group
        ]

        group_rows.sort(
            key=lambda row:
                float(
                    row[
                        "mean_top5_similarity"
                    ]
                ),
            reverse=True,
        )

        if group == "BOUNDARY":

            middle = len(
                group_rows
            ) // 2

            half = (
                REVIEW_SAMPLE_PER_GROUP
                // 2
            )

            start = max(
                0,
                middle - half
            )

            chosen = (
                group_rows[
                    start:
                    start
                    + REVIEW_SAMPLE_PER_GROUP
                ]
            )

        else:

            chosen = (
                group_rows[
                    :REVIEW_SAMPLE_PER_GROUP
                ]
            )

        selected.extend(
            chosen
        )

    return selected


def save_csv(
    rows,
    path,
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "article_id",
        "group",
        "mean_top5_similarity",
        "max_similarity",
        "source",
        "title",
        "published_at",
        "url",
    ]

    for rank in range(
        1,
        TOP_K_POSITIVES + 1
    ):
        fieldnames.extend(
            [
                f"positive_{rank}_title",
                f"positive_{rank}_similarity",
            ]
        )

    fieldnames.extend(
        [
            "human_label",
            "human_note",
        ]
    )

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

        writer.writerows(
            rows
        )


def main():

    print()
    print(
        "UNLABELED 기사 "
        "Positive 공간 비교 시작"
    )

    articles = (
        load_unlabeled_articles()
    )

    print()
    print(
        f"신규 후보 기사: "
        f"{len(articles):,}개"
    )

    if not articles:
        print(
            "비교할 기사가 없습니다."
        )
        return

    positive_embeddings = (
        np.load(
            POSITIVE_EMBEDDINGS_PATH
        )
    )

    positive_metadata = (
        load_positive_metadata()
    )

    print(
        f"Historical Positive: "
        f"{len(positive_metadata):,}개"
    )

    if (
        len(positive_embeddings)
        != len(positive_metadata)
    ):

        raise RuntimeError(
            "Positive embedding과 "
            "metadata 수가 다릅니다."
        )

    client = load_client()

    query_embeddings = (
        create_embeddings(
            client,
            articles,
        )
    )

    similarity_results = (
        calculate_positive_similarity(
            query_embeddings,
            positive_embeddings,
        )
    )

    scores = [
        result[
            "mean_top5_similarity"
        ]
        for result
        in similarity_results
    ]

    (
        groups,
        lower_threshold,
        upper_threshold,
    ) = assign_distance_group(
        scores
    )

    rows = build_output_rows(
        articles,
        similarity_results,
        groups,
        positive_metadata,
    )

    review_rows = (
        choose_review_sample(
            rows
        )
    )

    save_csv(
        review_rows,
        OUTPUT_PATH,
    )

    print()
    print(
        "[Similarity 분포]"
    )

    print(
        f"FAR / BOUNDARY 경계: "
        f"{lower_threshold:.4f}"
    )

    print(
        f"BOUNDARY / CLOSE 경계: "
        f"{upper_threshold:.4f}"
    )

    print()

    for group in (
        "CLOSE",
        "BOUNDARY",
        "FAR",
    ):

        count = sum(
            1
            for row in rows
            if row["group"]
            == group
        )

        print(
            f"{group}: "
            f"{count:,}개"
        )

    print()
    print(
        f"사람 검토용 샘플: "
        f"{len(review_rows):,}개"
    )

    print(
        f"저장 위치: "
        f"{OUTPUT_PATH}"
    )

    print()
    print(
        "중요:"
    )

    print(
        "CLOSE/FAR는 KEEP/DROP 라벨이 아닙니다."
    )

    print(
        "현재는 Positive corpus와의 "
        "의미적 거리만 표현합니다."
    )


if __name__ == "__main__":
    main()
