import csv
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


CLUSTERS_PATH = Path(
    "outputs/kpsea_clusters_k40.csv"
)

EMBEDDING_INPUT_PATH = Path(
    "data/kpsea_embedding_input.csv"
)

OUTPUT_JSONL_PATH = Path(
    "outputs/kpsea_cluster_analysis_k40.jsonl"
)

OUTPUT_TXT_PATH = Path(
    "outputs/kpsea_cluster_analysis_k40.txt"
)


MODEL = "gpt-5.6-terra"

N_CLUSTERS = 40

REPRESENTATIVES_PER_CLUSTER = 8

MAX_RETRIES = 4


CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "cluster_id": {
            "type": "integer"
        },

        "cluster_name": {
            "type": "string"
        },

        "main_topic": {
            "type": "string"
        },

        "subtopics": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "recurring_positive_characteristics": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "typical_entities_or_contexts": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "cluster_coherence": {
            "type": "string",
            "enum": [
                "high",
                "medium",
                "low"
            ]
        },

        "uncertainty_note": {
            "type": "string"
        }
    },

    "required": [
        "cluster_id",
        "cluster_name",
        "main_topic",
        "subtopics",
        "recurring_positive_characteristics",
        "typical_entities_or_contexts",
        "cluster_coherence",
        "uncertainty_note"
    ],

    "additionalProperties": False
}


def load_client():
    """
    .env에서 OpenAI API key를 읽는다.
    """

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


def load_embedding_articles():
    """
    article_id -> 원문 데이터 mapping.
    """

    if not EMBEDDING_INPUT_PATH.exists():
        raise FileNotFoundError(
            f"파일이 없습니다: "
            f"{EMBEDDING_INPUT_PATH}"
        )

    articles = {}

    with EMBEDDING_INPUT_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            article_id = str(
                row.get(
                    "article_id",
                    ""
                )
            ).strip()

            if article_id:
                articles[
                    article_id
                ] = row

    return articles


def load_clusters():
    """
    clustering 결과를 cluster별로 묶는다.
    """

    if not CLUSTERS_PATH.exists():
        raise FileNotFoundError(
            f"파일이 없습니다: "
            f"{CLUSTERS_PATH}"
        )

    clusters = {}

    with CLUSTERS_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            cluster_id = int(
                row[
                    "cluster_id"
                ]
            )

            distance = float(
                row[
                    "distance_to_center"
                ]
            )

            row[
                "_distance"
            ] = distance

            clusters.setdefault(
                cluster_id,
                []
            ).append(
                row
            )

    return clusters


def choose_representatives(
    cluster_rows
):
    """
    cluster 중심에 가장 가까운 기사 8개.
    """

    sorted_rows = sorted(
        cluster_rows,
        key=lambda row:
            row["_distance"]
    )

    return sorted_rows[
        :REPRESENTATIVES_PER_CLUSTER
    ]


def build_cluster_prompt(
    cluster_id,
    cluster_rows,
    representatives,
    article_map,
):
    """
    대표기사 제목 + 본문을 LLM에게 전달한다.
    """

    sections = []

    for rank, row in enumerate(
        representatives,
        start=1,
    ):

        article_id = str(
            row[
                "article_id"
            ]
        )

        article = article_map.get(
            article_id,
            {}
        )

        title = (
            article.get(
                "title",
                ""
            )
            or row.get(
                "title",
                ""
            )
        ).strip()

        embedding_text = (
            article.get(
                "embedding_text",
                ""
            )
        ).strip()

        sections.append(
            "\n".join(
                [
                    f"[ARTICLE {rank}]",
                    f"TITLE: {title}",
                    embedding_text,
                ]
            )
        )

    articles_text = (
        "\n\n"
        "------------------------------"
        "\n\n"
    ).join(
        sections
    )

    prompt = f"""
다음은 KPSEA의 과거 채택 기사들을 embedding한 뒤
K-means로 만든 cluster {cluster_id}의 대표 기사들이다.

이 cluster 전체에는 {len(cluster_rows)}개의 기사가 있으며,
아래에는 cluster 중심에 가장 가까운 대표 기사
{len(representatives)}개를 제공한다.

목표는 아직 KEEP/DROP 규칙을 만드는 것이 아니다.

현재 데이터는 모두 과거에 실제로 채택된 positive 사례이므로,
negative 사례가 없는 상태에서
"이 특성 때문에 채택되었다"는 인과적 결론을 내리지 마라.

대신 다음을 분석하라.

1. 이 cluster의 공통된 뉴스 주제
2. 주요 하위 주제
3. positive 기사들에서 반복적으로 관찰되는 특징
4. 자주 등장하는 기관·사업·기술·제도적 맥락
5. 대표기사들이 하나의 의미 있는 cluster로 잘 묶여 있는지
6. 대표기사만으로 판단하기 어려운 부분

cluster_name은 사람이 한눈에 이해할 수 있도록
짧고 구체적인 한국어 명칭으로 작성하라.

recurring_positive_characteristics는
단순 키워드가 아니라 기사들의 공통적인 정보적 특성을 작성하라.

예:
- 설계 또는 감리 실무에 직접 영향을 주는 제도 변화
- 대규모 SOC 사업의 발주·입찰 진행 상황
- 구조적 안전성 또는 신기술 검증과 관련된 내용

아직 다음과 같은 표현은 사용하지 마라.
- "반드시 KEEP해야 한다"
- "이런 기사는 DROP한다"
- "KPSEA는 이 이유 때문에 선택했다"

[REPRESENTATIVE ARTICLES]

{articles_text}
"""

    return prompt.strip()


def load_completed():
    """
    이미 분석된 cluster를 읽어 재호출을 방지한다.
    """

    completed = {}

    if not OUTPUT_JSONL_PATH.exists():
        return completed

    with OUTPUT_JSONL_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = line.strip()

            if not line:
                continue

            data = json.loads(
                line
            )

            completed[
                int(
                    data[
                        "cluster_id"
                    ]
                )
            ] = data

    return completed


def append_result(
    result
):
    OUTPUT_JSONL_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_JSONL_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            json.dumps(
                result,
                ensure_ascii=False,
            )
        )

        file.write(
            "\n"
        )


def analyze_cluster(
    client,
    cluster_id,
    prompt,
):
    """
    Structured Outputs를 사용해
    일정한 JSON 형식으로 분석한다.
    """

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = client.responses.create(
                model=MODEL,

                reasoning={
                    "effort": "low"
                },

                input=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 건설·토목·구조공학 및 "
                            "건설산업 뉴스 데이터 분석가다. "
                            "제공된 positive 사례에서 관찰 가능한 "
                            "공통 패턴만 신중하게 추출한다."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],

                text={
                    "format": {
                        "type": "json_schema",
                        "name": "cluster_analysis",
                        "strict": True,
                        "schema": CLUSTER_SCHEMA,
                    }
                },
            )

            result = json.loads(
                response.output_text
            )

            # 모델이 다른 id를 쓰는 것을 방지
            result[
                "cluster_id"
            ] = cluster_id

            return result

        except Exception as error:

            last_error = error

            print(
                f"  오류: {error}"
            )

            if attempt < MAX_RETRIES:

                wait_seconds = (
                    attempt * 3
                )

                print(
                    f"  {wait_seconds}초 후 재시도..."
                )

                time.sleep(
                    wait_seconds
                )

    raise RuntimeError(
        f"Cluster {cluster_id} 분석 실패: "
        f"{last_error}"
    )


def export_txt(
    completed,
    clusters,
):
    """
    사람이 읽기 쉬운 TXT 결과 생성.
    """

    with OUTPUT_TXT_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:

        for cluster_id in sorted(
            completed.keys()
        ):

            result = completed[
                cluster_id
            ]

            cluster_size = len(
                clusters.get(
                    cluster_id,
                    []
                )
            )

            file.write(
                "=" * 80
                + "\n"
            )

            file.write(
                f"CLUSTER {cluster_id} "
                f"({cluster_size} articles)\n"
            )

            file.write(
                "=" * 80
                + "\n\n"
            )

            file.write(
                "이름: "
                + result[
                    "cluster_name"
                ]
                + "\n"
            )

            file.write(
                "주제: "
                + result[
                    "main_topic"
                ]
                + "\n"
            )

            file.write(
                "응집도: "
                + result[
                    "cluster_coherence"
                ]
                + "\n\n"
            )

            file.write(
                "[하위 주제]\n"
            )

            for item in result[
                "subtopics"
            ]:

                file.write(
                    f"- {item}\n"
                )

            file.write(
                "\n[반복되는 positive 특성]\n"
            )

            for item in result[
                "recurring_positive_characteristics"
            ]:

                file.write(
                    f"- {item}\n"
                )

            file.write(
                "\n[주요 맥락]\n"
            )

            for item in result[
                "typical_entities_or_contexts"
            ]:

                file.write(
                    f"- {item}\n"
                )

            file.write(
                "\n[불확실성]\n"
            )

            file.write(
                result[
                    "uncertainty_note"
                ]
            )

            file.write(
                "\n\n"
            )


def main():

    print()
    print(
        "KPSEA positive cluster 분석 시작"
    )

    print(
        f"모델: {MODEL}"
    )

    client = load_client()

    article_map = (
        load_embedding_articles()
    )

    clusters = load_clusters()

    completed = load_completed()

    print()
    print(
        f"Cluster 수: "
        f"{len(clusters)}"
    )

    print(
        f"기존 분석 완료: "
        f"{len(completed)}"
    )

    print()

    for cluster_id in range(
        N_CLUSTERS
    ):

        if cluster_id in completed:

            print(
                f"[Cluster {cluster_id}] "
                "이미 완료 - skip"
            )

            continue

        cluster_rows = clusters.get(
            cluster_id,
            []
        )

        if not cluster_rows:

            print(
                f"[Cluster {cluster_id}] "
                "기사 없음 - skip"
            )

            continue

        representatives = (
            choose_representatives(
                cluster_rows
            )
        )

        prompt = build_cluster_prompt(
            cluster_id,
            cluster_rows,
            representatives,
            article_map,
        )

        print(
            f"[Cluster {cluster_id}] "
            f"{len(cluster_rows)}개 기사 분석 중..."
        )

        result = analyze_cluster(
            client,
            cluster_id,
            prompt,
        )

        append_result(
            result
        )

        completed[
            cluster_id
        ] = result

        print(
            "  → "
            + result[
                "cluster_name"
            ]
        )

    export_txt(
        completed,
        clusters,
    )

    print()
    print(
        "Positive cluster 분석 완료"
    )

    print(
        f"JSONL: "
        f"{OUTPUT_JSONL_PATH}"
    )

    print(
        f"TXT: "
        f"{OUTPUT_TXT_PATH}"
    )


if __name__ == "__main__":
    main()
