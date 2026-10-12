import csv
import hashlib
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# PATHS
# ============================================================

RUBRIC_PATH = Path(
    "outputs/kpsea_rubric_v2.txt"
)

INPUT_PATH = Path(
    "data/fresh_test_v2_labeling.csv"
)

CHECKPOINT_PATH = Path(
    "outputs/rubric_v2_prediction_checkpoint.jsonl"
)

OUTPUT_CSV_PATH = Path(
    "outputs/rubric_v2_predictions_for_review.csv"
)

OUTPUT_TXT_PATH = Path(
    "outputs/rubric_v2_predictions_for_review.txt"
)


# ============================================================
# MODEL SETTINGS
# ============================================================

MODEL = "gpt-6-sol"
REASONING_EFFORT = "low"

MAX_RETRIES = 4
BODY_CHAR_LIMIT = 7000


# ============================================================
# STRUCTURED OUTPUT
# ============================================================

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "KEEP",
                "DROP",
                "REVIEW",
            ],
        },
        "matched_rules": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "reason": {
            "type": "string",
        },
        "needs_more_information": {
            "type": "boolean",
        },
    },
    "required": [
        "decision",
        "matched_rules",
        "reason",
        "needs_more_information",
    ],
    "additionalProperties": False,
}


# ============================================================
# LOAD
# ============================================================

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


def load_rubric():
    if not RUBRIC_PATH.exists():
        raise FileNotFoundError(
            f"Rubric v2 파일이 없습니다: {RUBRIC_PATH}"
        )

    return RUBRIC_PATH.read_text(
        encoding="utf-8"
    )


def load_articles():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"입력 기사 파일이 없습니다: {INPUT_PATH}"
        )

    with INPUT_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        rows = list(
            csv.DictReader(file)
        )

    if not rows:
        raise RuntimeError(
            "입력 기사 파일이 비어 있습니다."
        )

    return rows


def make_rubric_hash(
    rubric_text
):
    return hashlib.sha256(
        rubric_text.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# PROMPT
# ============================================================

def build_article_prompt(
    row
):
    title = (
        row.get(
            "title",
            ""
        )
        .strip()
    )

    source = (
        row.get(
            "source",
            ""
        )
        .strip()
    )

    published_at = (
        row.get(
            "published_at",
            ""
        )
        .strip()
    )

    body = (
        row.get(
            "body",
            ""
        )
        .strip()
    )

    body = body[
        :BODY_CHAR_LIMIT
    ]

    parts = [
        f"제목: {title}",
    ]

    if source:
        parts.append(
            f"언론사: {source}"
        )

    if published_at:
        parts.append(
            f"날짜: {published_at}"
        )

    if body:
        parts.append(
            "\n본문:\n"
            + body
        )

    else:
        parts.append(
            "\n본문: 제공되지 않음"
        )

    return "\n".join(
        parts
    )


# ============================================================
# CHECKPOINT
# ============================================================

def normalize_article_id(
    value
):
    value = str(
        value or ""
    ).strip()

    if value.endswith(
        ".0"
    ):
        value = value[:-2]

    return value


def load_checkpoint(
    rubric_hash
):
    completed = {}

    if not CHECKPOINT_PATH.exists():
        return completed

    with CHECKPOINT_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(
                    line
                )

            except json.JSONDecodeError:
                continue

            if (
                record.get(
                    "rubric_hash"
                )
                != rubric_hash
            ):
                continue

            if (
                record.get(
                    "model"
                )
                != MODEL
            ):
                continue

            article_id = normalize_article_id(
                record.get(
                    "article_id",
                    ""
                )
            )

            if article_id:
                completed[
                    article_id
                ] = record

    return completed


def append_checkpoint(
    record
):
    CHECKPOINT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with CHECKPOINT_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
            )
        )

        file.write(
            "\n"
        )


# ============================================================
# CLASSIFIER
# ============================================================

def classify_article(
    client,
    rubric_text,
    article_text,
):
    system_prompt = (
        "당신은 KPSEA 토목·구조 엔지니어링 "
        "뉴스 선별 classifier다. "
        "제공된 Rubric v2를 그대로 적용한다. "
        "Rubric에 없는 기준을 임의로 추가하지 않는다. "
        "제목의 키워드보다 기사의 중심 사건·과업과 "
        "토목·구조 엔지니어링 직접성을 판단한다. "
        "REVIEW는 Rubric에 정의된 경우에만 사용한다."
    )

    user_prompt = f"""
[RUBRIC v2]

{rubric_text}

============================================================

[NEW ARTICLE]

{article_text}

============================================================

위 기사에 Rubric v2를 적용하라.

판정 규칙:

1. KEEP / DROP / REVIEW 중 하나를 선택한다.
2. 기사의 중심 사건과 중심 과업을 기준으로 판단한다.
3. 기사에 없는 사실은 추정하지 않는다.
4. 명확한 KEEP 또는 DROP 근거가 있으면 REVIEW를 남용하지 않는다.
5. 필요한 정보가 없어 직접성을 판단할 수 없으면 REVIEW할 수 있다.
6. matched_rules에는 실제 적용한 규칙 ID를 적는다.
   예: K1, D3, C4
7. reason은 판정 이유를 1~3문장으로 설명한다.
""".strip()

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = (
                client.responses.create(
                    model=MODEL,

                    reasoning={
                        "effort":
                            REASONING_EFFORT
                    },

                    input=[
                        {
                            "role":
                                "system",

                            "content":
                                system_prompt,
                        },
                        {
                            "role":
                                "user",

                            "content":
                                user_prompt,
                        },
                    ],

                    text={
                        "format": {
                            "type":
                                "json_schema",

                            "name":
                                "rubric_v2_prediction",

                            "strict":
                                True,

                            "schema":
                                OUTPUT_SCHEMA,
                        }
                    },
                )
            )

            return json.loads(
                response.output_text
            )

        except Exception as error:

            last_error = error

            if attempt < MAX_RETRIES:

                wait_seconds = (
                    attempt * 3
                )

                print(
                    f"  API 오류: {error}"
                )

                print(
                    f"  {wait_seconds}초 후 재시도..."
                )

                time.sleep(
                    wait_seconds
                )

    raise RuntimeError(
        "Classifier 호출 실패: "
        f"{last_error}"
    )


# ============================================================
# OUTPUT
# ============================================================

def save_csv(
    results
):
    OUTPUT_CSV_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "test_order",
        "article_id",
        "source",
        "title",
        "published_at",
        "predicted_label",
        "matched_rules",
        "reason",
        "needs_more_information",
        "human_confirm",
        "final_label",
        "human_note",
    ]

    with OUTPUT_CSV_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for result in results:

            writer.writerow(
                {
                    field:
                        result.get(
                            field,
                            ""
                        )
                    for field
                    in fieldnames
                }
            )


def save_txt(
    results
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
            "KPSEA Rubric v2 Prediction Review\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        keep_count = sum(
            1
            for row in results
            if row[
                "predicted_label"
            ]
            == "KEEP"
        )

        drop_count = sum(
            1
            for row in results
            if row[
                "predicted_label"
            ]
            == "DROP"
        )

        review_count = sum(
            1
            for row in results
            if row[
                "predicted_label"
            ]
            == "REVIEW"
        )

        file.write(
            f"Total: {len(results)}\n"
        )

        file.write(
            f"KEEP: {keep_count}\n"
        )

        file.write(
            f"DROP: {drop_count}\n"
        )

        file.write(
            f"REVIEW: {review_count}\n\n"
        )

        for index, result in enumerate(
            results,
            start=1,
        ):

            file.write(
                "-" * 80
                + "\n"
            )

            file.write(
                f"{index}. "
                f"[{result['predicted_label']}] "
                f"{result['title']}\n"
            )

            file.write(
                f"언론사: "
                f"{result['source']}\n"
            )

            file.write(
                f"규칙: "
                f"{result['matched_rules']}\n"
            )

            file.write(
                f"이유: "
                f"{result['reason']}\n"
            )

            file.write(
                "\n"
            )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "Rubric v2 자동 판정 시작"
    )

    rubric_text = (
        load_rubric()
    )

    rubric_hash = (
        make_rubric_hash(
            rubric_text
        )
    )

    articles = load_articles()

    print(
        f"입력 기사: "
        f"{len(articles)}개"
    )

    print(
        f"모델: {MODEL}"
    )

    completed = (
        load_checkpoint(
            rubric_hash
        )
    )

    print(
        f"기존 checkpoint: "
        f"{len(completed)}개"
    )

    client = load_client()

    results = []

    total = len(
        articles
    )

    for index, row in enumerate(
        articles,
        start=1,
    ):

        article_id = normalize_article_id(
            row.get(
                "article_id",
                ""
            )
        )

        if not article_id:

            raw_key = (
                row.get(
                    "title",
                    ""
                )
                + "|"
                + row.get(
                    "url",
                    ""
                )
            )

            article_id = hashlib.sha256(
                raw_key.encode(
                    "utf-8"
                )
            ).hexdigest()[
                :16
            ]

        if article_id in completed:

            prediction = (
                completed[
                    article_id
                ]
            )

            print(
                f"[{index}/{total}] "
                "checkpoint 사용: "
                f"{row.get('title', '')}"
            )

        else:

            article_text = (
                build_article_prompt(
                    row
                )
            )

            print()
            print(
                f"[{index}/{total}] "
                f"{row.get('title', '')}"
            )

            prediction_data = (
                classify_article(
                    client,
                    rubric_text,
                    article_text,
                )
            )

            prediction = {
                "article_id":
                    article_id,

                "model":
                    MODEL,

                "rubric_hash":
                    rubric_hash,

                "decision":
                    prediction_data[
                        "decision"
                    ],

                "matched_rules":
                    prediction_data[
                        "matched_rules"
                    ],

                "reason":
                    prediction_data[
                        "reason"
                    ],

                "needs_more_information":
                    prediction_data[
                        "needs_more_information"
                    ],
            }

            append_checkpoint(
                prediction
            )

            completed[
                article_id
            ] = prediction

            print(
                "  → "
                f"{prediction['decision']}"
            )

        result = {
            "test_order":
                row.get(
                    "test_order",
                    index
                ),

            "article_id":
                article_id,

            "source":
                row.get(
                    "source",
                    ""
                ),

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

            "predicted_label":
                prediction[
                    "decision"
                ],

            "matched_rules":
                ", ".join(
                    prediction.get(
                        "matched_rules",
                        []
                    )
                ),

            "reason":
                prediction.get(
                    "reason",
                    ""
                ),

            "needs_more_information":
                prediction.get(
                    "needs_more_information",
                    False
                ),

            "human_confirm":
                "",

            "final_label":
                "",

            "human_note":
                "",
        }

        results.append(
            result
        )

    save_csv(
        results
    )

    save_txt(
        results
    )

    keep_count = sum(
        1
        for row in results
        if row[
            "predicted_label"
        ]
        == "KEEP"
    )

    drop_count = sum(
        1
        for row in results
        if row[
            "predicted_label"
        ]
        == "DROP"
    )

    review_count = sum(
        1
        for row in results
        if row[
            "predicted_label"
        ]
        == "REVIEW"
    )

    print()
    print(
        "=" * 60
    )

    print(
        "RUBRIC v2 PREDICTION RESULT"
    )

    print(
        "=" * 60
    )

    print(
        f"KEEP: {keep_count}"
    )

    print(
        f"DROP: {drop_count}"
    )

    print(
        f"REVIEW: {review_count}"
    )

    print()
    print(
        "[결과 파일]"
    )

    print(
        OUTPUT_CSV_PATH
    )

    print(
        OUTPUT_TXT_PATH
    )


if __name__ == "__main__":
    main()
