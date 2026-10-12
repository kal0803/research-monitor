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

# Git으로 freeze한 Rubric을 우선 사용한다.
# 아직 rubrics/에 없을 경우 outputs/를 fallback으로 사용한다.
RUBRIC_PATHS = [
    Path("rubrics/kpsea_rubric_v2.txt"),
    Path("outputs/kpsea_rubric_v2.txt"),
]

CHECKPOINT_PATH = Path(
    "outputs/rubric_v2_pipeline_checkpoint.jsonl"
)


# ============================================================
# MODEL / CLASSIFIER SETTINGS
# ============================================================

MODEL = "gpt-6-sol"

REASONING_EFFORT = "low"

MAX_RETRIES = 4

BODY_CHAR_LIMIT = 7000

# 기존 URL-only checkpoint와 명확하게 구분하기 위한 버전.
CHECKPOINT_VERSION = 2


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
# CLASSIFIER PROMPTS
# ============================================================

SYSTEM_PROMPT = (
    "당신은 KPSEA 토목·구조 엔지니어링 "
    "뉴스 선별 classifier다. "
    "제공된 Rubric v2를 그대로 적용한다. "
    "Rubric에 없는 기준을 임의로 추가하지 않는다. "
    "제목의 키워드보다 기사의 중심 사건·과업과 "
    "토목·구조 엔지니어링 직접성을 판단한다. "
    "REVIEW는 Rubric에 정의된 경우에만 사용한다."
)

ARTICLE_INSTRUCTIONS = """
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


# ============================================================
# CLIENT / RUBRIC
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


def find_rubric_path():
    for path in RUBRIC_PATHS:
        if path.exists():
            return path

    searched = ", ".join(
        str(path)
        for path in RUBRIC_PATHS
    )

    raise FileNotFoundError(
        "Rubric v2 파일을 찾을 수 없습니다. "
        f"확인한 경로: {searched}"
    )


def load_rubric():
    path = find_rubric_path()

    return path.read_text(
        encoding="utf-8"
    )


def make_rubric_hash(
    rubric_text,
):
    return hashlib.sha256(
        rubric_text.encode(
            "utf-8"
        )
    ).hexdigest()


def make_classifier_hash():
    """
    모델뿐 아니라 reasoning 설정, body limit,
    system prompt, 판정 지침, output schema가 바뀌어도
    기존 checkpoint가 재사용되지 않도록 한다.
    """

    payload = {
        "checkpoint_version":
            CHECKPOINT_VERSION,

        "model":
            MODEL,

        "reasoning_effort":
            REASONING_EFFORT,

        "body_char_limit":
            BODY_CHAR_LIMIT,

        "system_prompt":
            SYSTEM_PROMPT,

        "article_instructions":
            ARTICLE_INSTRUCTIONS,

        "output_schema":
            OUTPUT_SCHEMA,
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


# ============================================================
# ARTICLE HELPERS
# ============================================================

def normalize_text(
    value,
):
    if value is None:
        return ""

    return str(
        value
    ).strip()


def article_body(
    article,
):
    paragraphs = (
        getattr(
            article,
            "paragraphs",
            None,
        )
        or []
    )

    cleaned = []

    for paragraph in paragraphs:
        paragraph = normalize_text(
            paragraph
        )

        if paragraph:
            cleaned.append(
                paragraph
            )

    return "\n\n".join(
        cleaned
    )


def make_article_id(
    article,
):
    """
    사람이 audit에서 식별하기 위한 안정적인 article ID.

    URL이 있으면 URL 기반으로 유지한다.
    URL이 없으면 source + title + published_at 기반이다.

    주의:
    이 값은 이제 checkpoint cache key로 사용하지 않는다.
    """

    url = normalize_text(
        getattr(
            article,
            "url",
            "",
        )
    )

    if url:
        raw_key = url

    else:
        raw_key = "|".join(
            [
                normalize_text(
                    getattr(
                        article,
                        "source",
                        "",
                    )
                ),
                normalize_text(
                    getattr(
                        article,
                        "title",
                        "",
                    )
                ),
                normalize_text(
                    getattr(
                        article,
                        "published_at",
                        "",
                    )
                ),
            ]
        )

    return hashlib.sha256(
        raw_key.encode(
            "utf-8"
        )
    ).hexdigest()[:16]


def make_article_hash(
    article,
):
    """
    실제 기사 내용에 대한 hash.

    같은 URL이라도 parser 수정 등으로 제목/날짜/본문이
    변경되면 다른 hash가 생성된다.

    body는 classifier에 전달되는 7000자만이 아니라
    전체 정제 body를 사용한다.
    """

    payload = {
        "source":
            normalize_text(
                getattr(
                    article,
                    "source",
                    "",
                )
            ),

        "title":
            normalize_text(
                getattr(
                    article,
                    "title",
                    "",
                )
            ),

        "published_at":
            normalize_text(
                getattr(
                    article,
                    "published_at",
                    "",
                )
            ),

        "url":
            normalize_text(
                getattr(
                    article,
                    "url",
                    "",
                )
            ),

        "body":
            article_body(
                article
            ),
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


def make_cache_key(
    article_hash,
    rubric_hash,
    classifier_hash,
):
    """
    checkpoint 재사용의 최종 기준.

    다음 중 하나라도 바뀌면 새 cache key가 생성된다.

    - 기사 실제 내용
    - Rubric
    - 모델 / reasoning / prompt / schema / body limit
    """

    payload = {
        "article_hash":
            article_hash,

        "rubric_hash":
            rubric_hash,

        "classifier_hash":
            classifier_hash,
    }

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


def build_article_prompt(
    article,
):
    title = normalize_text(
        getattr(
            article,
            "title",
            "",
        )
    )

    source = normalize_text(
        getattr(
            article,
            "source",
            "",
        )
    )

    published_at = normalize_text(
        getattr(
            article,
            "published_at",
            "",
        )
    )

    body = article_body(
        article
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

def load_checkpoint(
    rubric_hash,
    classifier_hash,
):
    """
    현재 Rubric + classifier 설정과 정확히 일치하는
    v2 checkpoint만 읽는다.

    기존 URL-only checkpoint는 checkpoint_version이 없으므로
    자동으로 무시된다.
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

            try:
                record = json.loads(
                    line
                )

            except json.JSONDecodeError:
                continue

            if (
                record.get(
                    "checkpoint_version"
                )
                != CHECKPOINT_VERSION
            ):
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
                    "classifier_hash"
                )
                != classifier_hash
            ):
                continue

            if (
                record.get(
                    "model"
                )
                != MODEL
            ):
                continue

            if (
                record.get(
                    "reasoning_effort"
                )
                != REASONING_EFFORT
            ):
                continue

            if (
                record.get(
                    "body_char_limit"
                )
                != BODY_CHAR_LIMIT
            ):
                continue

            article_hash = normalize_text(
                record.get(
                    "article_hash",
                    "",
                )
            )

            cache_key = normalize_text(
                record.get(
                    "cache_key",
                    "",
                )
            )

            decision = normalize_text(
                record.get(
                    "decision",
                    "",
                )
            ).upper()

            if not article_hash:
                continue

            if not cache_key:
                continue

            if decision not in {
                "KEEP",
                "DROP",
                "REVIEW",
            }:
                continue

            # 같은 cache key가 여러 번 존재한다면
            # 파일에서 뒤에 있는 최신 기록을 사용한다.
            completed[
                cache_key
            ] = record

    return completed


def append_checkpoint(
    record,
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
# API CLASSIFIER
# ============================================================

def classify_article_with_api(
    client,
    rubric_text,
    article_text,
):
    user_prompt = f"""
[RUBRIC v2]

{rubric_text}

============================================================

[NEW ARTICLE]

{article_text}

============================================================

{ARTICLE_INSTRUCTIONS}
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
                                SYSTEM_PROMPT,
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
# BATCH CLASSIFICATION
# ============================================================

def classify_articles(
    articles,
    verbose=True,
):
    rubric_text = load_rubric()

    rubric_hash = make_rubric_hash(
        rubric_text
    )

    classifier_hash = (
        make_classifier_hash()
    )

    completed = load_checkpoint(
        rubric_hash=rubric_hash,
        classifier_hash=classifier_hash,
    )

    if verbose:
        print()
        print(
            "=" * 60
        )

        print(
            "RUBRIC v2 CLASSIFICATION"
        )

        print(
            "=" * 60
        )

        print(
            f"분류 대상: "
            f"{len(articles)}개"
        )

        print(
            f"모델: {MODEL}"
        )

        print(
            f"reasoning: "
            f"{REASONING_EFFORT}"
        )

        print(
            f"checkpoint version: "
            f"{CHECKPOINT_VERSION}"
        )

        print(
            f"checkpoint 사용 가능: "
            f"{len(completed)}개"
        )

    client = None

    results = []

    total = len(
        articles
    )

    for index, article in enumerate(
        articles,
        start=1,
    ):
        article_id = make_article_id(
            article
        )

        article_hash = make_article_hash(
            article
        )

        cache_key = make_cache_key(
            article_hash=article_hash,
            rubric_hash=rubric_hash,
            classifier_hash=classifier_hash,
        )

        cached = False

        if cache_key in completed:
            prediction = completed[
                cache_key
            ]

            cached = True

            if verbose:
                print(
                    f"[{index}/{total}] "
                    f"CACHE | "
                    f"{article.title}"
                )

        else:
            if client is None:
                client = load_client()

            if verbose:
                print()
                print(
                    f"[{index}/{total}] "
                    f"{article.title}"
                )

            article_text = (
                build_article_prompt(
                    article
                )
            )

            prediction_data = (
                classify_article_with_api(
                    client=client,
                    rubric_text=rubric_text,
                    article_text=article_text,
                )
            )

            prediction = {
                "checkpoint_version":
                    CHECKPOINT_VERSION,

                "article_id":
                    article_id,

                "article_hash":
                    article_hash,

                "cache_key":
                    cache_key,

                "model":
                    MODEL,

                "reasoning_effort":
                    REASONING_EFFORT,

                "body_char_limit":
                    BODY_CHAR_LIMIT,

                "rubric_hash":
                    rubric_hash,

                "classifier_hash":
                    classifier_hash,

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

                "url":
                    normalize_text(
                        getattr(
                            article,
                            "url",
                            "",
                        )
                    ),

                "title":
                    normalize_text(
                        getattr(
                            article,
                            "title",
                            "",
                        )
                    ),
            }

            append_checkpoint(
                prediction
            )

            completed[
                cache_key
            ] = prediction

            if verbose:
                print(
                    "  → "
                    f"{prediction['decision']}"
                )

        results.append(
            {
                "article":
                    article,

                "article_id":
                    article_id,

                "article_hash":
                    article_hash,

                "cache_key":
                    cache_key,

                "decision":
                    prediction[
                        "decision"
                    ],

                "matched_rules":
                    prediction.get(
                        "matched_rules",
                        [],
                    ),

                "reason":
                    prediction.get(
                        "reason",
                        "",
                    ),

                "needs_more_information":
                    prediction.get(
                        "needs_more_information",
                        False,
                    ),

                "cached":
                    cached,
            }
        )

    return results


# ============================================================
# AUDIT CSV
# ============================================================

def save_classification_audit(
    results,
    start_date,
    end_date,
):
    output_path = Path(
        "outputs"
    ) / (
        "rubric_v2_pipeline_"
        f"{start_date.strftime('%Y%m%d')}_"
        f"{end_date.strftime('%Y%m%d')}.csv"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "article_id",
        "article_hash",
        "source",
        "title",
        "published_at",
        "url",
        "decision",
        "matched_rules",
        "reason",
        "needs_more_information",
        "cached",
    ]

    with output_path.open(
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
            article = result[
                "article"
            ]

            writer.writerow(
                {
                    "article_id":
                        result[
                            "article_id"
                        ],

                    "article_hash":
                        result.get(
                            "article_hash",
                            "",
                        ),

                    "source":
                        normalize_text(
                            getattr(
                                article,
                                "source",
                                "",
                            )
                        ),

                    "title":
                        normalize_text(
                            getattr(
                                article,
                                "title",
                                "",
                            )
                        ),

                    "published_at":
                        normalize_text(
                            getattr(
                                article,
                                "published_at",
                                "",
                            )
                        ),

                    "url":
                        normalize_text(
                            getattr(
                                article,
                                "url",
                                "",
                            )
                        ),

                    "decision":
                        result[
                            "decision"
                        ],

                    "matched_rules":
                        ", ".join(
                            result.get(
                                "matched_rules",
                                [],
                            )
                        ),

                    "reason":
                        result.get(
                            "reason",
                            ""
                        ),

                    "needs_more_information":
                        result.get(
                            "needs_more_information",
                            False,
                        ),

                    "cached":
                        result.get(
                            "cached",
                            False,
                        ),
                }
            )

    return output_path
