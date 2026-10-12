import argparse
import csv
import json
import os
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from openai import OpenAI


INPUT_PATH = Path(
    "data/kpsea_embedding_input.csv"
)

CHECKPOINT_PATH = Path(
    "data/kpsea_embeddings_checkpoint.jsonl"
)

EMBEDDINGS_PATH = Path(
    "data/kpsea_embeddings.npy"
)

METADATA_PATH = Path(
    "data/kpsea_embedding_metadata.csv"
)


MODEL = "text-embedding-3-small"

BATCH_SIZE = 50

MAX_RETRIES = 5

RETRY_BASE_SECONDS = 2


def load_api_key():
    """
    repo 최상위의 .env 파일을 읽어
    OPENAI_API_KEY를 가져온다.
    """

    load_dotenv()

    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY를 찾을 수 없습니다. "
            ".env 파일을 확인하세요."
        )

    return api_key


def load_input_rows():
    """
    embedding 입력 CSV를 읽는다.
    """

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"입력 파일이 없습니다: "
            f"{INPUT_PATH}"
        )

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

            article_id = (
                row.get(
                    "article_id",
                    ""
                )
                .strip()
            )

            embedding_text = (
                row.get(
                    "embedding_text",
                    ""
                )
                .strip()
            )

            if not article_id:
                continue

            if not embedding_text:
                continue

            rows.append(
                row
            )

    return rows


def load_checkpoint():
    """
    이미 embedding이 생성된 article_id를 읽는다.

    중간에 프로그램이 종료돼도
    다시 실행하면 기존 결과를 재사용한다.
    """

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

            record = json.loads(
                line
            )

            article_id = str(
                record[
                    "article_id"
                ]
            )

            completed[
                article_id
            ] = record

    return completed


def save_checkpoint_records(
    records
):
    """
    새 embedding 결과를 JSONL checkpoint에 추가한다.
    """

    CHECKPOINT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with CHECKPOINT_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:

        for record in records:

            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
            )

            file.write(
                "\n"
            )


def create_batch_embeddings(
    client,
    batch_rows,
):
    """
    여러 기사를 한 번의 API 요청으로 embedding한다.
    """

    texts = [
        row[
            "embedding_text"
        ]
        for row in batch_rows
    ]

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = (
                client.embeddings.create(
                    model=MODEL,
                    input=texts,
                    encoding_format="float",
                )
            )

            embeddings = [
                item.embedding
                for item in response.data
            ]

            if (
                len(embeddings)
                != len(batch_rows)
            ):
                raise RuntimeError(
                    "입력 기사 수와 "
                    "embedding 결과 수가 다릅니다."
                )

            usage_tokens = (
                response.usage.total_tokens
                if response.usage
                else 0
            )

            return (
                embeddings,
                usage_tokens,
            )

        except Exception as error:

            last_error = error

            if attempt == MAX_RETRIES:
                break

            wait_seconds = (
                RETRY_BASE_SECONDS
                * attempt
            )

            print(
                f"  API 오류. "
                f"{wait_seconds}초 후 재시도 "
                f"({attempt}/{MAX_RETRIES})"
            )

            print(
                f"  {error}"
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        f"Embedding API 호출 실패: "
        f"{last_error}"
    )


def export_final_files(
    input_rows,
    completed,
):
    """
    checkpoint를 article_id 순서로 정렬해

    1. kpsea_embeddings.npy
    2. kpsea_embedding_metadata.csv

    를 생성한다.
    """

    final_rows = []

    vectors = []

    for row in input_rows:

        article_id = str(
            row[
                "article_id"
            ]
        )

        record = completed.get(
            article_id
        )

        if record is None:
            continue

        vectors.append(
            record[
                "embedding"
            ]
        )

        final_rows.append(
            row
        )

    if not vectors:

        print(
            "저장할 embedding이 없습니다."
        )

        return

    matrix = np.asarray(
        vectors,
        dtype=np.float32,
    )

    np.save(
        EMBEDDINGS_PATH,
        matrix,
    )

    metadata_fields = [
        "article_id",
        "kpsea_url",
        "title",
        "published_at",
        "source",
        "original_url",
        "body_length",
    ]

    with METADATA_PATH.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=metadata_fields,
        )

        writer.writeheader()

        for row in final_rows:

            writer.writerow(
                {
                    field:
                        row.get(
                            field,
                            ""
                        )
                    for field
                    in metadata_fields
                }
            )

    print()
    print(
        "[최종 파일 생성]"
    )

    print(
        f"Embedding matrix: "
        f"{EMBEDDINGS_PATH}"
    )

    print(
        f"Metadata: "
        f"{METADATA_PATH}"
    )

    print(
        f"Matrix shape: "
        f"{matrix.shape}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "KPSEA positive articles를 "
            "OpenAI embedding으로 변환"
        )
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "테스트용 최대 기사 수. "
            "예: --limit 10"
        ),
    )

    args = parser.parse_args()

    print()
    print(
        "KPSEA embedding 생성 시작"
    )

    print(
        f"모델: {MODEL}"
    )

    api_key = load_api_key()

    client = OpenAI(
        api_key=api_key
    )

    input_rows = (
        load_input_rows()
    )

    if args.limit is not None:

        if args.limit < 1:
            raise ValueError(
                "--limit은 1 이상이어야 합니다."
            )

        input_rows = input_rows[
            :args.limit
        ]

    completed = (
        load_checkpoint()
    )

    pending_rows = [
        row
        for row in input_rows
        if str(
            row[
                "article_id"
            ]
        )
        not in completed
    ]

    print()
    print(
        f"이번 대상 기사: "
        f"{len(input_rows):,}개"
    )

    print(
        f"이미 완료: "
        f"{len(input_rows) - len(pending_rows):,}개"
    )

    print(
        f"새로 처리: "
        f"{len(pending_rows):,}개"
    )

    print(
        f"Batch size: "
        f"{BATCH_SIZE}"
    )

    total_tokens = 0

    total_new = 0

    for start in range(
        0,
        len(pending_rows),
        BATCH_SIZE,
    ):

        batch_rows = (
            pending_rows[
                start:
                start + BATCH_SIZE
            ]
        )

        batch_number = (
            start
            // BATCH_SIZE
            + 1
        )

        total_batches = (
            (
                len(pending_rows)
                + BATCH_SIZE
                - 1
            )
            // BATCH_SIZE
        )

        print()
        print(
            f"[Batch "
            f"{batch_number}/"
            f"{total_batches}]"
        )

        embeddings, usage_tokens = (
            create_batch_embeddings(
                client,
                batch_rows,
            )
        )

        records = []

        for row, embedding in zip(
            batch_rows,
            embeddings,
        ):

            records.append(
                {
                    "article_id":
                        row[
                            "article_id"
                        ],

                    "embedding":
                        embedding,
                }
            )

        save_checkpoint_records(
            records
        )

        for record in records:

            completed[
                str(
                    record[
                        "article_id"
                    ]
                )
            ] = record

        total_new += len(
            records
        )

        total_tokens += (
            usage_tokens
        )

        print(
            f"  완료: "
            f"{len(records)}개"
        )

        print(
            f"  이번 batch tokens: "
            f"{usage_tokens:,}"
        )

        print(
            f"  누적 신규: "
            f"{total_new:,}개"
        )

    export_final_files(
        input_rows,
        completed,
    )

    print()
    print(
        "Embedding 생성 완료"
    )

    print(
        f"이번 실행 API 처리 기사: "
        f"{total_new:,}개"
    )

    print(
        f"이번 실행 사용 tokens: "
        f"{total_tokens:,}"
    )


if __name__ == "__main__":
    main()
