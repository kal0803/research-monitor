import csv
from pathlib import Path

import numpy as np


EMBEDDINGS_PATH = Path(
    "data/kpsea_embeddings.npy"
)

METADATA_PATH = Path(
    "data/kpsea_embedding_metadata.csv"
)


def cosine_similarity_matrix(
    query_vector,
    matrix,
):
    query_norm = np.linalg.norm(
        query_vector
    )

    matrix_norms = np.linalg.norm(
        matrix,
        axis=1,
    )

    similarities = (
        matrix @ query_vector
    ) / (
        matrix_norms
        * query_norm
        + 1e-12
    )

    return similarities


def load_metadata():
    rows = []

    with METADATA_PATH.open(
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


def main():

    if not EMBEDDINGS_PATH.exists():
        print(
            "Embedding 파일이 없습니다:"
        )
        print(
            EMBEDDINGS_PATH
        )
        return

    if not METADATA_PATH.exists():
        print(
            "Metadata 파일이 없습니다:"
        )
        print(
            METADATA_PATH
        )
        return

    embeddings = np.load(
        EMBEDDINGS_PATH
    )

    metadata = load_metadata()

    print()
    print(
        "[Embedding 상태]"
    )

    print(
        f"벡터 shape: "
        f"{embeddings.shape}"
    )

    print(
        f"Metadata 기사 수: "
        f"{len(metadata):,}"
    )

    if len(metadata) != len(
        embeddings
    ):
        print()
        print(
            "WARNING:"
        )

        print(
            "Embedding 수와 metadata 수가 "
            "일치하지 않습니다."
        )

        return

    print()
    print(
        "Embedding과 metadata 수가 "
        "정상적으로 일치합니다."
    )

    query_index = 0

    query_vector = embeddings[
        query_index
    ]

    similarities = (
        cosine_similarity_matrix(
            query_vector,
            embeddings,
        )
    )

    similarities[
        query_index
    ] = -1

    nearest_indices = (
        np.argsort(
            similarities
        )[::-1][:10]
    )

    print()
    print(
        "[기준 기사]"
    )

    print(
        metadata[
            query_index
        ]["title"]
    )

    print()
    print(
        "[가장 비슷한 기사 10개]"
    )

    for rank, index in enumerate(
        nearest_indices,
        start=1,
    ):

        print()
        print(
            f"{rank}. "
            f"similarity="
            f"{similarities[index]:.4f}"
        )

        print(
            metadata[index][
                "title"
            ]
        )


if __name__ == "__main__":
    main()
