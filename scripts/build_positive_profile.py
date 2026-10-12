import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


INPUT_PATH = Path(
    "outputs/kpsea_cluster_analysis_k40.jsonl"
)

OUTPUT_JSON_PATH = Path(
    "outputs/kpsea_positive_profile_v1.json"
)

OUTPUT_TXT_PATH = Path(
    "outputs/kpsea_positive_profile_v1.txt"
)


MODEL = "gpt-5.6-terra"


PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "profile_name": {
            "type": "string"
        },

        "summary": {
            "type": "string"
        },

        "major_domains": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "domain_name": {
                        "type": "string"
                    },

                    "description": {
                        "type": "string"
                    },

                    "supporting_cluster_ids": {
                        "type": "array",
                        "items": {
                            "type": "integer"
                        }
                    },

                    "typical_subtopics": {
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

                    "typical_contexts": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },

                    "internal_diversity_note": {
                        "type": "string"
                    }
                },

                "required": [
                    "domain_name",
                    "description",
                    "supporting_cluster_ids",
                    "typical_subtopics",
                    "recurring_positive_characteristics",
                    "typical_contexts",
                    "internal_diversity_note"
                ],

                "additionalProperties": False
            }
        },

        "cross_domain_patterns": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "rare_but_valid_positive_patterns": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "uncertain_or_mixed_areas": {
            "type": "array",
            "items": {
                "type": "string"
            }
        },

        "limitations": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },

    "required": [
        "profile_name",
        "summary",
        "major_domains",
        "cross_domain_patterns",
        "rare_but_valid_positive_patterns",
        "uncertain_or_mixed_areas",
        "limitations"
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


def load_cluster_analyses():
    """
    40개 cluster 분석 JSONL을 읽는다.
    """

    if not INPUT_PATH.exists():

        raise FileNotFoundError(
            f"Cluster 분석 파일이 없습니다: "
            f"{INPUT_PATH}"
        )

    analyses = []

    with INPUT_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = line.strip()

            if not line:
                continue

            analyses.append(
                json.loads(
                    line
                )
            )

    analyses.sort(
        key=lambda item:
            item[
                "cluster_id"
            ]
    )

    return analyses


def build_prompt(
    analyses
):
    """
    40개 cluster 분석 결과를
    상위 positive 영역으로 통합하도록 한다.
    """

    cluster_texts = []

    for analysis in analyses:

        cluster_id = analysis[
            "cluster_id"
        ]

        cluster_name = analysis[
            "cluster_name"
        ]

        main_topic = analysis[
            "main_topic"
        ]

        subtopics = analysis[
            "subtopics"
        ]

        characteristics = analysis[
            "recurring_positive_characteristics"
        ]

        contexts = analysis[
            "typical_entities_or_contexts"
        ]

        coherence = analysis[
            "cluster_coherence"
        ]

        uncertainty = analysis[
            "uncertainty_note"
        ]

        block = (
            f"[CLUSTER {cluster_id}]\n"
            f"이름: {cluster_name}\n"
            f"주제: {main_topic}\n"
            f"응집도: {coherence}\n"
            f"하위주제: "
            f"{json.dumps(subtopics, ensure_ascii=False)}\n"
            f"반복 특성: "
            f"{json.dumps(characteristics, ensure_ascii=False)}\n"
            f"맥락: "
            f"{json.dumps(contexts, ensure_ascii=False)}\n"
            f"불확실성: {uncertainty}"
        )

        cluster_texts.append(
            block
        )

    all_clusters = (
        "\n\n"
        "----------------------------------------"
        "\n\n"
    ).join(
        cluster_texts
    )

    return f"""
다음은 KPSEA가 과거 실제로 채택한 positive 기사 약 4천 건을
embedding하고 K-means로 40개 cluster로 분류한 뒤,
각 cluster의 대표기사들을 분석한 결과다.

현재 모든 데이터는 positive 사례다.

목표는 아직 KEEP / DROP 판정규칙을 만드는 것이 아니다.

이번 작업의 목적은 40개의 세부 cluster를
더 큰 의미 단위로 통합하여
'KPSEA Positive Profile v1'을 만드는 것이다.

다음 원칙을 반드시 지켜라.

1. 의미적으로 겹치는 cluster들은 하나의 상위 domain으로 묶어라.

2. 억지로 모든 cluster를 비슷한 크기의 domain으로 만들지 마라.

3. 일반적으로 약 8~15개의 상위 domain이 적절하지만,
   데이터가 요구한다면 그보다 적거나 많아도 된다.

4. 각 domain에는 어떤 cluster들이 근거가 되었는지
   supporting_cluster_ids로 명시하라.

5. recurring_positive_characteristics에는
   단순한 키워드가 아니라
   positive 기사에서 반복적으로 관찰되는 정보적 성격을 작성하라.

예:
- 발주 또는 입찰 조건의 구체적인 변화를 전달함
- 대규모 SOC 사업의 진행 단계가 실제로 변화함
- 새로운 구조·시공기술의 성능 검증 결과를 포함함

6. positive 자료만 존재하므로 다음과 같은 결론은 내리지 마라.

- 이것이 KPSEA의 공식 선별기준이다.
- 이 특성이 있으면 반드시 KEEP이다.
- 이 특성이 없으면 DROP이다.
- KPSEA가 정확히 이 이유 때문에 기사를 선택했다.

7. 여러 domain에서 반복적으로 나타나는 더 일반적인 특성이 있다면
   cross_domain_patterns에 작성하라.

8. 빈도는 낮지만 유효한 positive 사례로 보이는 패턴이 있다면
   rare_but_valid_positive_patterns에 별도로 보존하라.
   큰 cluster가 아니라는 이유만으로 버리지 마라.

9. 응집도가 낮거나 여러 주제가 섞여 있어
   현재 단계에서 해석하기 어려운 영역은
   uncertain_or_mixed_areas에 명시하라.

10. 이 profile은 이후 새로운 unlabeled 기사와 비교하여
    positive 영역과 경계 사례를 찾는 데 사용할 것이다.

[40 CLUSTER ANALYSES]

{all_clusters}
""".strip()


def generate_profile(
    client,
    prompt,
):
    """
    LLM을 이용해 Positive Profile을 생성한다.
    """

    response = client.responses.create(
        model=MODEL,

        reasoning={
            "effort": "medium"
        },

        input=[
            {
                "role": "system",
                "content": (
                    "당신은 건설·토목·구조공학과 "
                    "뉴스 데이터 분석에 전문성을 가진 "
                    "연구 분석가다. "
                    "관찰 가능한 positive 패턴과 "
                    "해석적 추론을 명확히 구분한다."
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
                "name": "kpsea_positive_profile",
                "strict": True,
                "schema": PROFILE_SCHEMA,
            }
        },
    )

    return json.loads(
        response.output_text
    )


def save_json(
    profile
):
    OUTPUT_JSON_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_JSON_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            profile,
            file,
            ensure_ascii=False,
            indent=2,
        )


def save_txt(
    profile
):
    with OUTPUT_TXT_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:

        file.write(
            "=" * 80
            + "\n"
        )

        file.write(
            profile[
                "profile_name"
            ]
            + "\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        file.write(
            "[전체 요약]\n"
        )

        file.write(
            profile[
                "summary"
            ]
        )

        file.write(
            "\n\n"
        )

        for index, domain in enumerate(
            profile[
                "major_domains"
            ],
            start=1,
        ):

            file.write(
                "-" * 80
                + "\n"
            )

            file.write(
                f"{index}. "
                f"{domain['domain_name']}\n"
            )

            file.write(
                "-" * 80
                + "\n\n"
            )

            file.write(
                "[설명]\n"
            )

            file.write(
                domain[
                    "description"
                ]
            )

            file.write(
                "\n\n"
            )

            file.write(
                "[근거 Cluster]\n"
            )

            file.write(
                ", ".join(
                    str(
                        cluster_id
                    )
                    for cluster_id
                    in domain[
                        "supporting_cluster_ids"
                    ]
                )
            )

            file.write(
                "\n\n"
            )

            file.write(
                "[대표 하위주제]\n"
            )

            for item in domain[
                "typical_subtopics"
            ]:

                file.write(
                    f"- {item}\n"
                )

            file.write(
                "\n"
            )

            file.write(
                "[반복되는 Positive 특성]\n"
            )

            for item in domain[
                "recurring_positive_characteristics"
            ]:

                file.write(
                    f"- {item}\n"
                )

            file.write(
                "\n"
            )

            file.write(
                "[주요 맥락]\n"
            )

            for item in domain[
                "typical_contexts"
            ]:

                file.write(
                    f"- {item}\n"
                )

            file.write(
                "\n"
            )

            file.write(
                "[내부 다양성]\n"
            )

            file.write(
                domain[
                    "internal_diversity_note"
                ]
            )

            file.write(
                "\n\n"
            )

        file.write(
            "=" * 80
            + "\n"
        )

        file.write(
            "DOMAIN 공통 패턴\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        for item in profile[
            "cross_domain_patterns"
        ]:

            file.write(
                f"- {item}\n"
            )

        file.write(
            "\n"
        )

        file.write(
            "=" * 80
            + "\n"
        )

        file.write(
            "희귀하지만 유효한 Positive 패턴\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        for item in profile[
            "rare_but_valid_positive_patterns"
        ]:

            file.write(
                f"- {item}\n"
            )

        file.write(
            "\n"
        )

        file.write(
            "=" * 80
            + "\n"
        )

        file.write(
            "불확실하거나 혼합된 영역\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        for item in profile[
            "uncertain_or_mixed_areas"
        ]:

            file.write(
                f"- {item}\n"
            )

        file.write(
            "\n"
        )

        file.write(
            "=" * 80
            + "\n"
        )

        file.write(
            "현재 Profile의 한계\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        for item in profile[
            "limitations"
        ]:

            file.write(
                f"- {item}\n"
            )


def main():

    print()
    print(
        "KPSEA Positive Profile v1 생성"
    )

    analyses = (
        load_cluster_analyses()
    )

    print()
    print(
        f"불러온 cluster 분석: "
        f"{len(analyses)}개"
    )

    if len(
        analyses
    ) < 40:

        print()
        print(
            "WARNING:"
        )

        print(
            "40개 cluster 분석이 "
            "모두 존재하지 않습니다."
        )

        print(
            "현재 존재하는 결과만으로 "
            "profile을 생성합니다."
        )

    client = load_client()

    prompt = build_prompt(
        analyses
    )

    print()
    print(
        "상위 Positive 영역 통합 중..."
    )

    profile = generate_profile(
        client,
        prompt,
    )

    save_json(
        profile
    )

    save_txt(
        profile
    )

    print()
    print(
        "Positive Profile 생성 완료"
    )

    print()
    print(
        f"상위 domain 수: "
        f"{len(profile['major_domains'])}"
    )

    print()
    print(
        "[상위 Domain]"
    )

    for index, domain in enumerate(
        profile[
            "major_domains"
        ],
        start=1,
    ):

        print(
            f"{index}. "
            f"{domain['domain_name']}"
        )

    print()
    print(
        "[결과 파일]"
    )

    print(
        OUTPUT_TXT_PATH
    )

    print(
        OUTPUT_JSON_PATH
    )


if __name__ == "__main__":
    main()
