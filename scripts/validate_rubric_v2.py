import csv
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


RUBRIC_PATH = Path(
    "outputs/kpsea_rubric_v2.txt"
)

TEST_PATH = Path(
    "data/fresh_test_v2_labeling.csv"
)

DB_PATH = Path(
    "data/articles.db"
)

CHECKPOINT_PATH = Path(
    "outputs/rubric_v2_fresh_test_checkpoint.jsonl"
)

PREDICTIONS_PATH = Path(
    "outputs/rubric_v2_fresh_test_predictions.csv"
)

ERRORS_PATH = Path(
    "outputs/rubric_v2_fresh_test_errors.csv"
)

METRICS_PATH = Path(
    "outputs/rubric_v2_fresh_test_metrics.json"
)

REPORT_PATH = Path(
    "outputs/rubric_v2_fresh_test_report.txt"
)


MODEL = "gpt-6-sol"
REASONING_EFFORT = "low"
MAX_RETRIES = 4
BODY_CHAR_LIMIT = 7000


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
            f"Rubric 파일이 없습니다: {RUBRIC_PATH}"
        )

    return RUBRIC_PATH.read_text(
        encoding="utf-8"
    )


def make_rubric_hash(
    rubric_text,
):
    return hashlib.sha256(
        rubric_text.encode("utf-8")
    ).hexdigest()


def normalize_article_id(value):
    value = str(value or "").strip()

    if value.endswith(".0"):
        value = value[:-2]

    return value


def load_test_rows():
    if not TEST_PATH.exists():
        raise FileNotFoundError(
            f"Fresh test 파일이 없습니다: {TEST_PATH}"
        )

    with TEST_PATH.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        rows = list(
            csv.DictReader(file)
        )

    if not rows:
        raise RuntimeError(
            "Fresh test 파일이 비어 있습니다."
        )

    invalid = []

    for index, row in enumerate(
        rows,
        start=2,
    ):
        label = (
            row.get(
                "human_label",
                ""
            )
            .strip()
            .upper()
        )

        if label not in {
            "KEEP",
            "DROP",
        }:
            invalid.append(
                (
                    index,
                    row.get("title", ""),
                    label,
                )
            )

    if invalid:
        preview = "\n".join(
            f"- CSV row {row_num}: "
            f"label='{label}' | {title}"
            for row_num, title, label
            in invalid[:10]
        )

        raise RuntimeError(
            "모든 fresh test 기사에 human_label을 "
            "KEEP 또는 DROP으로 명시해야 합니다.\n"
            f"미완료/잘못된 라벨: {len(invalid)}개\n"
            f"{preview}"
        )

    return rows


def load_db_bodies():
    by_id = {}

    if not DB_PATH.exists():
        return by_id

    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = sqlite3.Row

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            paragraphs
        FROM articles
        """
    )

    for row in cursor.fetchall():
        raw = row["paragraphs"] or "[]"

        try:
            parsed = json.loads(raw)

        except json.JSONDecodeError:
            parsed = []

        if isinstance(parsed, list):
            body = "\n\n".join(
                str(item).strip()
                for item in parsed
                if str(item).strip()
            )
        else:
            body = ""

        by_id[
            normalize_article_id(
                row["id"]
            )
        ] = body

    connection.close()

    return by_id


def get_body(
    row,
    db_bodies,
):
    body = (
        row.get(
            "body",
            ""
        )
        .strip()
    )

    if body:
        return body

    article_id = normalize_article_id(
        row.get(
            "article_id",
            ""
        )
    )

    return (
        db_bodies.get(
            article_id,
            ""
        )
        .strip()
    )


def build_article_prompt(
    row,
    body,
):
    """
    절대로 human_label / human_note를
    모델에게 전달하지 않는다.
    """

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

    body = body[:BODY_CHAR_LIMIT]

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

    return "\n".join(parts)


def load_checkpoint(
    rubric_hash,
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
                record = json.loads(line)

            except json.JSONDecodeError:
                continue

            if (
                record.get("rubric_hash")
                != rubric_hash
            ):
                continue

            if record.get("model") != MODEL:
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


def append_checkpoint(record):
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
        file.write("\n")


def classify_article(
    client,
    rubric_text,
    article_text,
):
    system_prompt = (
        "당신은 KPSEA 토목·구조 엔지니어링 "
        "뉴스 선별 classifier다. "
        "제공된 Rubric v2를 그대로 적용한다. "
        "Rubric에 없는 기준을 새로 만들지 않는다. "
        "기사의 중심 사건·과업과 토목·구조 "
        "엔지니어링 직접성을 판단한다. "
        "REVIEW는 Rubric이 허용한 경우에만 사용한다."
    )

    user_prompt = f"""
[RUBRIC v2]

{rubric_text}

============================================================

[FRESH TEST ARTICLE]

{article_text}

============================================================

이 신규 기사에 Rubric v2를 적용하라.

주의:
- KEEP, DROP, REVIEW 중 하나를 선택한다.
- human label은 제공되지 않았다.
- 과거 similarity, embedding distance, CLOSE/FAR/BOUNDARY는 사용하지 않는다.
- 기사에 없는 사실을 추정하지 않는다.
- 제목의 키워드보다 본문의 중심 사건·과업을 우선한다.
- 명확한 KEEP/DROP 근거가 있으면 REVIEW를 남용하지 않는다.
- 반대로 필수 정보가 없어 직접성을 판정할 수 없으면 억지로 추정하지 않는다.
- matched_rules에는 실제 적용한 규칙 ID를 적는다.
- reason은 1~3문장으로 간결하게 작성한다.
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
                                "rubric_v2_fresh_test_decision",
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
                wait_seconds = attempt * 3

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


def safe_divide(
    numerator,
    denominator,
):
    if denominator == 0:
        return None

    return numerator / denominator


def f1_score(
    precision,
    recall,
):
    if (
        precision is None
        or recall is None
        or precision + recall == 0
    ):
        return None

    return (
        2
        * precision
        * recall
        / (precision + recall)
    )


def calculate_metrics(results):
    total = len(results)

    human_counts = Counter(
        row["human_label"]
        for row in results
    )

    predicted_counts = Counter(
        row["predicted_label"]
        for row in results
    )

    confusion = {
        human: {
            predicted: 0
            for predicted in (
                "KEEP",
                "DROP",
                "REVIEW",
            )
        }
        for human in (
            "KEEP",
            "DROP",
        )
    }

    for row in results:
        confusion[
            row["human_label"]
        ][
            row["predicted_label"]
        ] += 1

    exact_correct = sum(
        1
        for row in results
        if (
            row["human_label"]
            == row["predicted_label"]
        )
    )

    keep_tp = (
        confusion["KEEP"]["KEEP"]
    )
    keep_fp = (
        confusion["DROP"]["KEEP"]
    )
    keep_fn = (
        confusion["KEEP"]["DROP"]
        + confusion["KEEP"]["REVIEW"]
    )

    drop_tp = (
        confusion["DROP"]["DROP"]
    )
    drop_fp = (
        confusion["KEEP"]["DROP"]
    )
    drop_fn = (
        confusion["DROP"]["KEEP"]
        + confusion["DROP"]["REVIEW"]
    )

    keep_precision = safe_divide(
        keep_tp,
        keep_tp + keep_fp,
    )
    keep_recall = safe_divide(
        keep_tp,
        keep_tp + keep_fn,
    )
    keep_f1 = f1_score(
        keep_precision,
        keep_recall,
    )

    drop_precision = safe_divide(
        drop_tp,
        drop_tp + drop_fp,
    )
    drop_recall = safe_divide(
        drop_tp,
        drop_tp + drop_fn,
    )
    drop_f1 = f1_score(
        drop_precision,
        drop_recall,
    )

    review_count = predicted_counts[
        "REVIEW"
    ]

    non_review = [
        row
        for row in results
        if row["predicted_label"]
        != "REVIEW"
    ]

    non_review_correct = sum(
        1
        for row in non_review
        if row["human_label"]
        == row["predicted_label"]
    )

    recalls = [
        value
        for value in (
            keep_recall,
            drop_recall,
        )
        if value is not None
    ]

    f1s = [
        value
        for value in (
            keep_f1,
            drop_f1,
        )
        if value is not None
    ]

    return {
        "total":
            total,
        "exact_correct":
            exact_correct,
        "exact_accuracy":
            safe_divide(
                exact_correct,
                total,
            ),
        "keep_precision":
            keep_precision,
        "keep_recall":
            keep_recall,
        "keep_f1":
            keep_f1,
        "drop_precision":
            drop_precision,
        "drop_recall":
            drop_recall,
        "drop_f1":
            drop_f1,
        "balanced_accuracy":
            (
                sum(recalls) / len(recalls)
                if recalls
                else None
            ),
        "macro_f1":
            (
                sum(f1s) / len(f1s)
                if f1s
                else None
            ),
        "review_count":
            review_count,
        "review_rate":
            safe_divide(
                review_count,
                total,
            ),
        "auto_decision_count":
            len(non_review),
        "auto_decision_accuracy":
            safe_divide(
                non_review_correct,
                len(non_review),
            ),
        "human_counts":
            dict(human_counts),
        "predicted_counts":
            dict(predicted_counts),
        "confusion_matrix":
            confusion,
    }


def percent_text(value):
    if value is None:
        return "N/A"

    return f"{value * 100:.1f}%"


def save_predictions(results):
    PREDICTIONS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "test_order",
        "article_id",
        "title",
        "source",
        "published_at",
        "human_label",
        "predicted_label",
        "correct",
        "matched_rules",
        "reason",
        "needs_more_information",
        "body_available",
        "human_note",
    ]

    with PREDICTIONS_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()

        for row in results:
            writer.writerow(
                {
                    field:
                        row.get(
                            field,
                            ""
                        )
                    for field in fieldnames
                }
            )


def save_errors(results):
    errors = [
        row
        for row in results
        if not row["correct"]
    ]

    fieldnames = [
        "test_order",
        "article_id",
        "title",
        "source",
        "human_label",
        "predicted_label",
        "matched_rules",
        "reason",
        "needs_more_information",
        "body_available",
        "human_note",
    ]

    with ERRORS_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()

        for row in errors:
            writer.writerow(
                {
                    field:
                        row.get(
                            field,
                            ""
                        )
                    for field in fieldnames
                }
            )


def save_metrics(metrics):
    with METRICS_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            metrics,
            file,
            ensure_ascii=False,
            indent=2,
        )


def save_report(
    metrics,
    results,
):
    errors = [
        row
        for row in results
        if not row["correct"]
    ]

    with REPORT_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:
        file.write(
            "=" * 80
            + "\n"
        )
        file.write(
            "KPSEA Rubric v2 Fresh Holdout Test Report\n"
        )
        file.write(
            "=" * 80
            + "\n\n"
        )

        file.write(
            f"Total fresh test articles: "
            f"{metrics['total']}\n"
        )
        file.write(
            f"Human KEEP: "
            f"{metrics['human_counts'].get('KEEP', 0)}\n"
        )
        file.write(
            f"Human DROP: "
            f"{metrics['human_counts'].get('DROP', 0)}\n\n"
        )

        file.write(
            f"Exact accuracy: "
            f"{percent_text(metrics['exact_accuracy'])}\n"
        )
        file.write(
            f"Balanced accuracy: "
            f"{percent_text(metrics['balanced_accuracy'])}\n"
        )
        file.write(
            f"Macro F1: "
            f"{percent_text(metrics['macro_f1'])}\n\n"
        )

        file.write("[KEEP]\n")
        file.write(
            f"Precision: "
            f"{percent_text(metrics['keep_precision'])}\n"
        )
        file.write(
            f"Recall: "
            f"{percent_text(metrics['keep_recall'])}\n"
        )
        file.write(
            f"F1: "
            f"{percent_text(metrics['keep_f1'])}\n\n"
        )

        file.write("[DROP]\n")
        file.write(
            f"Precision: "
            f"{percent_text(metrics['drop_precision'])}\n"
        )
        file.write(
            f"Recall: "
            f"{percent_text(metrics['drop_recall'])}\n"
        )
        file.write(
            f"F1: "
            f"{percent_text(metrics['drop_f1'])}\n\n"
        )

        file.write(
            f"REVIEW: {metrics['review_count']} "
            f"({percent_text(metrics['review_rate'])})\n"
        )
        file.write(
            "Auto-decision accuracy "
            "(REVIEW 제외): "
            f"{percent_text(metrics['auto_decision_accuracy'])}\n\n"
        )

        file.write("[CONFUSION MATRIX]\n")
        file.write(
            "Human \\ Predicted | KEEP | DROP | REVIEW\n"
        )
        file.write(
            "-" * 50
            + "\n"
        )

        for human in (
            "KEEP",
            "DROP",
        ):
            row = (
                metrics[
                    "confusion_matrix"
                ][human]
            )

            file.write(
                f"{human:17} | "
                f"{row['KEEP']:4} | "
                f"{row['DROP']:4} | "
                f"{row['REVIEW']:6}\n"
            )

        file.write("\n")
        file.write(
            "=" * 80
            + "\n"
        )
        file.write(
            f"Errors / REVIEW ({len(errors)} cases)\n"
        )
        file.write(
            "=" * 80
            + "\n\n"
        )

        for index, row in enumerate(
            errors,
            start=1,
        ):
            file.write(
                f"{index}. {row['title']}\n"
            )
            file.write(
                f"Human: {row['human_label']}\n"
            )
            file.write(
                f"Predicted: {row['predicted_label']}\n"
            )
            file.write(
                f"Rules: {row['matched_rules']}\n"
            )
            file.write(
                f"Reason: {row['reason']}\n"
            )
            file.write(
                f"Body available: "
                f"{row['body_available']}\n"
            )

            if row.get("human_note"):
                file.write(
                    f"Human note: "
                    f"{row['human_note']}\n"
                )

            file.write("\n")


def main():
    print()
    print(
        "KPSEA Rubric v2 Fresh Holdout Test 시작"
    )

    rubric_text = load_rubric()
    rubric_hash = make_rubric_hash(
        rubric_text
    )

    test_rows = load_test_rows()
    db_bodies = load_db_bodies()

    print()
    print(
        f"Fresh test articles: "
        f"{len(test_rows)}개"
    )
    print(f"모델: {MODEL}")
    print(
        "human_label/human_note는 "
        "모델 입력에 포함하지 않습니다."
    )

    completed = load_checkpoint(
        rubric_hash
    )

    print(
        f"기존 checkpoint: "
        f"{len(completed)}개"
    )

    client = load_client()

    results = []
    total = len(test_rows)

    for index, row in enumerate(
        test_rows,
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
                row.get("title", "")
                + "|"
                + row.get("url", "")
            )
            article_id = hashlib.sha256(
                raw_key.encode("utf-8")
            ).hexdigest()[:16]

        body = get_body(
            row,
            db_bodies,
        )

        if article_id in completed:
            prediction = completed[
                article_id
            ]

            print(
                f"[{index}/{total}] "
                f"checkpoint 사용: "
                f"{row.get('title', '')}"
            )

        else:
            article_text = build_article_prompt(
                row,
                body,
            )

            print()
            print(
                f"[{index}/{total}] "
                f"{row.get('title', '')}"
            )

            prediction_data = classify_article(
                client,
                rubric_text,
                article_text,
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

        human_label = (
            row.get(
                "human_label",
                ""
            )
            .strip()
            .upper()
        )

        predicted_label = (
            prediction[
                "decision"
            ]
            .strip()
            .upper()
        )

        result = {
            "test_order":
                row.get(
                    "test_order",
                    ""
                ),
            "article_id":
                article_id,
            "title":
                row.get(
                    "title",
                    ""
                ),
            "source":
                row.get(
                    "source",
                    ""
                ),
            "published_at":
                row.get(
                    "published_at",
                    ""
                ),
            "human_label":
                human_label,
            "predicted_label":
                predicted_label,
            "correct":
                (
                    human_label
                    == predicted_label
                ),
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
            "body_available":
                bool(body),
            "human_note":
                row.get(
                    "human_note",
                    ""
                ),
        }

        results.append(result)

    metrics = calculate_metrics(
        results
    )

    save_predictions(results)
    save_errors(results)
    save_metrics(metrics)
    save_report(
        metrics,
        results,
    )

    print()
    print("=" * 60)
    print("FRESH TEST RESULT")
    print("=" * 60)
    print(
        "Exact accuracy: "
        f"{percent_text(metrics['exact_accuracy'])}"
    )
    print(
        "Balanced accuracy: "
        f"{percent_text(metrics['balanced_accuracy'])}"
    )
    print(
        "Macro F1: "
        f"{percent_text(metrics['macro_f1'])}"
    )
    print(
        "KEEP precision: "
        f"{percent_text(metrics['keep_precision'])}"
    )
    print(
        "KEEP recall: "
        f"{percent_text(metrics['keep_recall'])}"
    )
    print(
        "KEEP F1: "
        f"{percent_text(metrics['keep_f1'])}"
    )
    print(
        "DROP precision: "
        f"{percent_text(metrics['drop_precision'])}"
    )
    print(
        "DROP recall: "
        f"{percent_text(metrics['drop_recall'])}"
    )
    print(
        "DROP F1: "
        f"{percent_text(metrics['drop_f1'])}"
    )
    print(
        "REVIEW rate: "
        f"{percent_text(metrics['review_rate'])}"
    )
    print(
        "Auto-decision accuracy: "
        f"{percent_text(metrics['auto_decision_accuracy'])}"
    )

    print()
    print("[결과 파일]")
    print(PREDICTIONS_PATH)
    print(ERRORS_PATH)
    print(METRICS_PATH)
    print(REPORT_PATH)


if __name__ == "__main__":
    main()
