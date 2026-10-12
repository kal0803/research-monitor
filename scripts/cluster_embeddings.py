import csv
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans


EMBEDDINGS_PATH = Path(
    "data/kpsea_embeddings.npy"
)

METADATA_PATH = Path(
    "data/kpsea_embedding_metadata.csv"
)

OUTPUT_DIR = Path(
    "outputs"
)

ALL_CLUSTERS_PATH = OUTPUT_DIR / (
    "kpsea_clusters_k40.csv"
)

REPRESENTATIVES_PATH = OUTPUT_DIR / (
    "kpsea_cluster_representatives_k40.txt"
)


N_CLUSTERS = 40

REPRESENTATIVES_PER_CLUSTER = 8

RANDOM_STATE = 42


def load_metadata():
    rows = []

    with METADATA_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:
            rows.append(row)

    return rows


def normalize_embeddings(
    embeddings
):
    """
    벡터 길이를 1로 정규화한다.

    뉴스 embedding에서는
    방향 기반 의미 유사성이 중요하기 때문에
    정규화 후 KMeans를 사용한다.
    """

    norms = np.linalg.norm(
        embeddings,
        axis=1,
        keepdims=True,
    )

    norms[
        norms == 0
    ] = 1

    return (
        embeddings / norms
    )


def calculate_distances(
    embeddings,
    labels,
    centers,
):
    """
    각 기사가 자기 cluster 중심에서
    얼마나 떨어져 있는지 계산한다.

    값이 작을수록
    cluster를 잘 대표한다.
    """

    distances = np.zeros(
        len(embeddings),
        dtype=np.float32,
    )

    for index in range(
        len(embeddings)
    ):

        cluster_id = labels[
            index
        ]

        center = centers[
            cluster_id
        ]

        distances[
            index
        ] = np.linalg.norm(
            embeddings[index]
            - center
        )

    return distances


def save_all_clusters(
    metadata,
    labels,
    distances,
):
    """
    모든 기사에 cluster_id를 붙여 CSV 저장.
    """

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "article_id",
        "cluster_id",
        "distance_to_center",
        "title",
        "published_at",
        "source",
        "kpsea_url",
        "original_url",
    ]

    with ALL_CLUSTERS_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for index, row in enumerate(
            metadata
        ):

            writer.writerow(
                {
                    "article_id":
                        row.get(
                            "article_id",
                            ""
                        ),

                    "cluster_id":
                        int(
                            labels[index]
                        ),

                    "distance_to_center":
                        f"{distances[index]:.6f}",

                    "title":
                        row.get(
                            "title",
                            ""
                        ),

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

                    "kpsea_url":
                        row.get(
                            "kpsea_url",
                            ""
                        ),

                    "original_url":
                        row.get(
                            "original_url",
                            ""
                        ),
                }
            )


def get_cluster_indices(
    labels,
    cluster_id,
):
    return np.where(
        labels == cluster_id
    )[0]


def get_representative_indices(
    cluster_indices,
    distances,
):
    """
    cluster 중심에 가장 가까운 기사
    REPRESENTATIVES_PER_CLUSTER개 선정.
    """

    sorted_indices = sorted(
        cluster_indices,
        key=lambda index:
            distances[index],
    )

    return sorted_indices[
        :REPRESENTATIVES_PER_CLUSTER
    ]


def save_representatives(
    metadata,
    labels,
    distances,
):
    """
    사람이 읽기 쉬운 형태로
    각 cluster 대표기사 저장.
    """

    with REPRESENTATIVES_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:

        for cluster_id in range(
            N_CLUSTERS
        ):

            cluster_indices = (
                get_cluster_indices(
                    labels,
                    cluster_id,
                )
            )

            representatives = (
                get_representative_indices(
                    cluster_indices,
                    distances,
                )
            )

            file.write(
                "=" * 80
            )

            file.write(
                "\n"
            )

            file.write(
                f"CLUSTER {cluster_id}\n"
            )

            file.write(
                f"전체 기사 수: "
                f"{len(cluster_indices)}개\n"
            )

            file.write(
                "=" * 80
            )

            file.write(
                "\n\n"
            )

            for rank, index in enumerate(
                representatives,
                start=1,
            ):

                row = metadata[
                    index
                ]

                file.write(
                    f"{rank}. "
                    f"{row.get('title', '')}\n"
                )

                file.write(
                    f"   날짜: "
                    f"{row.get('published_at', '')}\n"
                )

                file.write(
                    f"   언론사: "
                    f"{row.get('source', '')}\n"
                )

                file.write(
                    f"   중심거리: "
                    f"{distances[index]:.6f}\n"
                )

                file.write(
                    f"   KPSEA: "
                    f"{row.get('kpsea_url', '')}\n"
                )

                file.write(
                    "\n"
                )

            file.write(
                "\n"
            )


def print_cluster_summary(
    labels
):
    """
    cluster별 기사 수를 터미널에 출력.
    """

    print()
    print(
        "[Cluster 크기]"
    )

    cluster_sizes = []

    for cluster_id in range(
        N_CLUSTERS
    ):

        size = int(
            np.sum(
                labels == cluster_id
            )
        )

        cluster_sizes.append(
            (
                cluster_id,
                size,
            )
        )

    cluster_sizes.sort(
        key=lambda item:
            item[1],
        reverse=True,
    )

    for cluster_id, size in (
        cluster_sizes
    ):

        print(
            f"Cluster "
            f"{cluster_id:02d}: "
            f"{size:,}개"
        )


def main():

    print()
    print(
        "KPSEA embedding clustering 시작"
    )

    print(
        f"K = {N_CLUSTERS}"
    )

    print()

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

    print(
        f"Embedding shape: "
        f"{embeddings.shape}"
    )

    print(
        f"Metadata 기사 수: "
        f"{len(metadata):,}"
    )

    if len(
        embeddings
    ) != len(
        metadata
    ):

        print()
        print(
            "ERROR:"
        )

        print(
            "Embedding 수와 "
            "metadata 수가 다릅니다."
        )

        return

    if len(
        embeddings
    ) < N_CLUSTERS:

        print()
        print(
            "ERROR:"
        )

        print(
            "기사 수보다 cluster 수가 "
            "많습니다."
        )

        return

    print()
    print(
        "Embedding 정규화 중..."
    )

    normalized_embeddings = (
        normalize_embeddings(
            embeddings
        )
    )

    print(
        "KMeans clustering 중..."
    )

    model = KMeans(
        n_clusters=N_CLUSTERS,
        random_state=RANDOM_STATE,
        n_init=20,
    )

    labels = model.fit_predict(
        normalized_embeddings
    )

    centers = model.cluster_centers_

    distances = calculate_distances(
        normalized_embeddings,
        labels,
        centers,
    )

    save_all_clusters(
        metadata,
        labels,
        distances,
    )

    save_representatives(
        metadata,
        labels,
        distances,
    )

    print_cluster_summary(
        labels
    )

    print()
    print(
        "[결과 파일]"
    )

    print(
        f"전체 clustering 결과:"
    )

    print(
        ALL_CLUSTERS_PATH
    )

    print()

    print(
        "Cluster별 대표기사:"
    )

    print(
        REPRESENTATIVES_PATH
    )

    print()
    print(
        "Clustering 완료"
    )


if __name__ == "__main__":
    main()
