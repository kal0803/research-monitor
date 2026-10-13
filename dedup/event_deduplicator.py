import csv
import hashlib
import json
import os
import re
import time

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from sklearn.feature_extraction.text import (
    TfidfVectorizer,
)
from sklearn.metrics.pairwise import (
    cosine_similarity,
)


# ============================================================
# SETTINGS
# ============================================================

load_dotenv()
MODEL = os.getenv("RESEARCH_MONITOR_DEDUP_MODEL", "gpt-6-luna")

REASONING_EFFORT = "low"

MAX_RETRIES = 4

BODY_CHAR_LIMIT = 5000

# 후보쌍 생성용 기준.
# 최종 중복 판정 기준이 아니라
# LLM에게 보낼 가치가 있는 pair만 추리는 단계다.
CANDIDATE_SIMILARITY_THRESHOLD = 0.25

# 같은 사건이 며칠 뒤 다른 언론사에
# 다시 보도되는 경우까지 허용.
MAX_DATE_DISTANCE_DAYS = 3


CHECKPOINT_PATH = Path(
    "outputs/event_dedup_checkpoint.jsonl"
)


# ============================================================
# STRUCTURED OUTPUT
# ============================================================

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "same_event": {
            "type": "boolean",
        },
        "confidence": {
            "type": "string",
            "enum": [
                "HIGH",
                "MEDIUM",
                "LOW",
            ],
        },
        "reason": {
            "type": "string",
        },
    },
    "required": [
        "same_event",
        "confidence",
        "reason",
    ],
    "additionalProperties": False,
}


# ============================================================
# BASIC TEXT
# ============================================================

def clean_text(
    value,
):

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
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

        text = clean_text(
            paragraph
        )

        if text:
            cleaned.append(
                text
            )

    return "\n".join(
        cleaned
    )


def build_similarity_text(
    article,
):
    """
    후보쌍 탐색에 사용하는 텍스트.

    제목에 더 높은 중요도를 주기 위해
    제목을 두 번 포함한다.
    """

    title = clean_text(
        getattr(
            article,
            "title",
            "",
        )
    )

    body = article_body(
        article
    )

    body = body[
        :BODY_CHAR_LIMIT
    ]

    return (
        f"{title} {title} {body}"
    ).strip()


# ============================================================
# HASH
# ============================================================

def make_article_hash(
    article,
):
    """
    URL만 사용하지 않고
    현재 파싱된 기사 내용 전체를 기준으로 hash한다.

    crawler 수정 후 같은 URL의 body가 바뀌면
    기존 dedup checkpoint를 잘못 재사용하지 않는다.
    """

    raw = "|".join(
        [
            clean_text(
                getattr(
                    article,
                    "source",
                    "",
                )
            ),
            clean_text(
                getattr(
                    article,
                    "title",
                    "",
                )
            ),
            clean_text(
                getattr(
                    article,
                    "published_at",
                    "",
                )
            ),
            clean_text(
                getattr(
                    article,
                    "url",
                    "",
                )
            ),
            article_body(
                article
            ),
        ]
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()


def make_pair_id(
    article_a,
    article_b,
):

    hashes = sorted(
        [
            make_article_hash(
                article_a
            ),
            make_article_hash(
                article_b
            ),
        ]
    )

    raw = "||".join(
        hashes
    )

    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()[:24]


# ============================================================
# DATE
# ============================================================

def parse_article_date(
    article,
):

    value = clean_text(
        getattr(
            article,
            "published_at",
            "",
        )
    )

    if not value:
        return None

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]

    for format_string in formats:

        try:

            return datetime.strptime(
                value,
                format_string,
            ).date()

        except ValueError:
            continue

    return None


def within_date_distance(
    article_a,
    article_b,
):

    date_a = parse_article_date(
        article_a
    )

    date_b = parse_article_date(
        article_b
    )

    if (
        date_a is None
        or date_b is None
    ):

        # 날짜가 없다는 이유만으로
        # duplicate 후보를 놓치지는 않는다.
        return True

    distance = abs(
        (
            date_a
            - date_b
        ).days
    )

    return (
        distance
        <= MAX_DATE_DISTANCE_DAYS
    )


PHOTO_PAIR_MIN_SIMILARITY = 0.10


def photo_same_day_candidate(article_a, article_b, score):
    """같은 날짜의 짧은 포토기사를 추가 중복 후보로 검토."""
    if score < PHOTO_PAIR_MIN_SIMILARITY:
        return False

    date_a = parse_article_date(article_a)
    date_b = parse_article_date(article_b)
    if date_a is None or date_a != date_b:
        return False

    articles = (article_a, article_b)
    photos = [
        article for article in articles
        if re.match(
            r"^\s*(?:\[포토\]|\[사진\]|\(포토\)|【포토】)",
            clean_text(getattr(article, "title", "")),
            re.IGNORECASE,
        )
    ]

    if not photos:
        return False

    if all(len(article_body(a)) > 900 for a in photos):
        return False

    def terms(article):
        text = (
            clean_text(getattr(article, "title", ""))
            + " " + article_body(article)
        )
        return set(
            re.findall(r"[가-힣]{4,}|[A-Za-z]{4,}", text)
        )

    shared = terms(article_a) & terms(article_b)
    return len(shared) >= 2


# ============================================================
# CANDIDATE GENERATION
# ============================================================

def generate_candidate_pairs(
    articles,
):
    """
    TF-IDF cosine similarity로
    LLM에게 보낼 후보쌍만 추린다.

    여기서 duplicate를 확정하지 않는다.
    """

    if len(articles) < 2:
        return []

    documents = [
        build_similarity_text(
            article
        )
        for article
        in articles
    ]

    vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(
            2,
            5,
        ),
        min_df=1,
        sublinear_tf=True,
    )

    matrix = vectorizer.fit_transform(
        documents
    )

    similarities = cosine_similarity(
        matrix
    )

    candidates = []

    for i in range(
        len(articles)
    ):

        for j in range(
            i + 1,
            len(articles),
        ):

            if not within_date_distance(
                articles[i],
                articles[j],
            ):
                continue

            score = float(
                similarities[
                    i,
                    j,
                ]
            )

            standard = score >= CANDIDATE_SIMILARITY_THRESHOLD
            photo_exception = (
                not standard
                and photo_same_day_candidate(
                    articles[i], articles[j], score
                )
            )

            if not (standard or photo_exception):
                continue

            candidates.append(
                {
                    "index_a":
                        i,

                    "index_b":
                        j,

                    "similarity":
                        score,
                    "candidate_rule":
                        "photo_same_day" if photo_exception else "standard",
                }
            )

    candidates.sort(
        key=lambda item:
            item[
                "similarity"
            ],
        reverse=True,
    )

    return candidates


# ============================================================
# OPENAI
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


# ============================================================
# CHECKPOINT
# ============================================================

def load_checkpoint():

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
                    "model"
                )
                != MODEL
            ):
                continue

            pair_id = clean_text(
                record.get(
                    "pair_id",
                    "",
                )
            )

            if pair_id:

                completed[
                    pair_id
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
# PAIR PROMPT
# ============================================================

def build_article_for_prompt(
    article,
):

    title = clean_text(
        getattr(
            article,
            "title",
            "",
        )
    )

    source = clean_text(
        getattr(
            article,
            "source",
            "",
        )
    )

    published_at = clean_text(
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

    return f"""
언론사: {source}
발행일: {published_at}
제목: {title}

본문:
{body}
""".strip()


def classify_pair_with_api(
    client,
    article_a,
    article_b,
):

    system_prompt = (
        "당신은 건설·토목 뉴스의 "
        "event-level duplicate 판정기다. "
        "두 기사가 같은 프로젝트를 언급하는지가 아니라 "
        "같은 구체적 사건을 보도하는지를 판단한다. "
        "동일 프로젝트에서 발생한 서로 다른 계약, 발주, "
        "수주, 착공, 심의, 사고, 조사, 기술개발 등은 "
        "서로 다른 사건으로 본다."
    )

    prompt_a = (
        build_article_for_prompt(
            article_a
        )
    )

    prompt_b = (
        build_article_for_prompt(
            article_b
        )
    )

    user_prompt = f"""
[ARTICLE A]

{prompt_a}

============================================================

[ARTICLE B]

{prompt_b}

============================================================

두 기사가 동일한 뉴스 사건을 보도하는지 판단하라.

판정 기준:

1. 단순히 같은 회사, 같은 사업, 같은 철도·도로·공구를
   언급한다고 동일 사건으로 판단하지 않는다.

2. 핵심 행위 또는 사건이 같아야 한다.
   예:
   - 같은 계약 체결
   - 같은 수주
   - 같은 사고
   - 같은 입찰 결과
   - 같은 기술 발표
   - 같은 정책 결정

3. 같은 프로젝트라도 시점과 행위가 다르면
   서로 다른 사건이다.
   예:
   - 입찰 공고 vs 수주 결과
   - 수주 vs 착공
   - 사고 발생 vs 사고 조사 결과
   - 기본설계 계약 vs 후속 실시설계 계약

4. 언론사마다 제목이나 문장 표현이 달라도
   핵심 주체, 대상, 행위, 시점이 사실상 같으면
   동일 사건으로 판단한다.

5. 확실하지 않으면 same_event=false로 보수적으로 판단한다.

reason은 1~3문장으로 작성한다.
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
                                "event_duplicate",

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
                    f"  API 오류: "
                    f"{error}"
                )

                print(
                    f"  {wait_seconds}초 후 재시도..."
                )

                time.sleep(
                    wait_seconds
                )

    raise RuntimeError(
        "Event dedup API 호출 실패: "
        f"{last_error}"
    )


# ============================================================
# PAIR CLASSIFICATION
# ============================================================

def classify_candidate_pairs(
    articles,
    candidates,
    verbose=True,
):

    checkpoint = (
        load_checkpoint()
    )

    client = None

    results = []

    total = len(
        candidates
    )

    for number, candidate in enumerate(
        candidates,
        start=1,
    ):

        index_a = candidate[
            "index_a"
        ]

        index_b = candidate[
            "index_b"
        ]

        article_a = articles[
            index_a
        ]

        article_b = articles[
            index_b
        ]

        pair_id = make_pair_id(
            article_a,
            article_b,
        )

        cached = False

        if pair_id in checkpoint:

            prediction = checkpoint[
                pair_id
            ]

            cached = True

        else:

            if client is None:

                client = load_client()

            prediction_data = (
                classify_pair_with_api(
                    client=client,
                    article_a=article_a,
                    article_b=article_b,
                )
            )

            prediction = {
                "pair_id":
                    pair_id,

                "model":
                    MODEL,

                "same_event":
                    prediction_data[
                        "same_event"
                    ],

                "confidence":
                    prediction_data[
                        "confidence"
                    ],

                "reason":
                    prediction_data[
                        "reason"
                    ],

                "article_a_hash":
                    make_article_hash(
                        article_a
                    ),

                "article_b_hash":
                    make_article_hash(
                        article_b
                    ),
            }

            append_checkpoint(
                prediction
            )

            checkpoint[
                pair_id
            ] = prediction

        result = {
            "pair_id":
                pair_id,

            "index_a":
                index_a,

            "index_b":
                index_b,

            "similarity":
                candidate[
                    "similarity"
                ],

            "same_event":
                bool(
                    prediction[
                        "same_event"
                    ]
                ),

            "confidence":
                prediction.get(
                    "confidence",
                    "",
                ),

            "reason":
                prediction.get(
                    "reason",
                    "",
                ),

            "cached":
                cached,
        }

        results.append(
            result
        )

        if verbose:

            title_a = clean_text(
                getattr(
                    article_a,
                    "title",
                    "",
                )
            )

            title_b = clean_text(
                getattr(
                    article_b,
                    "title",
                    "",
                )
            )

            decision = (
                "DUPLICATE"
                if result[
                    "same_event"
                ]
                else "DISTINCT"
            )

            cache_text = (
                " CACHE"
                if cached
                else ""
            )

            print()
            print(
                f"[DEDUP {number}/{total}] "
                f"{decision}{cache_text}"
            )

            print(
                f"  similarity: "
                f"{result['similarity']:.3f}"
            )

            print(
                f"  A: {title_a}"
            )

            print(
                f"  B: {title_b}"
            )

            print(
                f"  reason: "
                f"{result['reason']}"
            )

    return results


# ============================================================
# UNION FIND
# ============================================================

class UnionFind:

    def __init__(
        self,
        size,
    ):

        self.parent = list(
            range(
                size
            )
        )

    def find(
        self,
        value,
    ):

        while (
            self.parent[
                value
            ]
            != value
        ):

            self.parent[
                value
            ] = (
                self.parent[
                    self.parent[
                        value
                    ]
                ]
            )

            value = (
                self.parent[
                    value
                ]
            )

        return value

    def union(
        self,
        a,
        b,
    ):

        root_a = self.find(
            a
        )

        root_b = self.find(
            b
        )

        if root_a == root_b:
            return

        self.parent[
            root_b
        ] = root_a


# ============================================================
# GROUPING
# ============================================================

def build_event_groups(
    articles,
    pair_results,
):

    union_find = UnionFind(
        len(
            articles
        )
    )

    for result in pair_results:

        if not result[
            "same_event"
        ]:
            continue

        union_find.union(
            result[
                "index_a"
            ],
            result[
                "index_b"
            ],
        )

    grouped_indices = {}

    for index in range(
        len(articles)
    ):

        root = union_find.find(
            index
        )

        grouped_indices.setdefault(
            root,
            [],
        ).append(
            index
        )

    groups = list(
        grouped_indices.values()
    )

    groups.sort(
        key=lambda group:
            min(
                group
            )
    )

    return groups


# ============================================================
# REPRESENTATIVE
# ============================================================

def representative_score(
    article,
):
    """
    동일 사건 중 대표 기사 선택.

    현재는 LLM을 추가 호출하지 않고
    정보량이 많은 기사를 우선한다.
    """

    paragraphs = (
        getattr(
            article,
            "paragraphs",
            None,
        )
        or []
    )

    body_chars = sum(
        len(
            clean_text(
                paragraph
            )
        )
        for paragraph
        in paragraphs
    )

    paragraph_count = len(
        [
            paragraph
            for paragraph
            in paragraphs
            if clean_text(
                paragraph
            )
        ]
    )

    title_chars = len(
        clean_text(
            getattr(
                article,
                "title",
                "",
            )
        )
    )

    return (
        body_chars,
        paragraph_count,
        title_chars,
    )


def select_group_representative(
    articles,
    group,
):

    return max(
        group,
        key=lambda index:
            representative_score(
                articles[
                    index
                ]
            ),
    )


# ============================================================
# MAIN DEDUP
# ============================================================

def deduplicate_events(
    articles,
    verbose=True,
):
    """
    Returns
    -------
    representatives:
        event-level dedup 후 남길 Article list.

    audit:
        그룹 및 pair 판정 정보.
    """

    if verbose:

        print()
        print(
            "=" * 60
        )

        print(
            "EVENT-LEVEL DEDUP"
        )

        print(
            "=" * 60
        )

        print(
            f"입력 기사: "
            f"{len(articles)}개"
        )

    if len(articles) < 2:

        return (
            list(
                articles
            ),
            {
                "candidate_pairs": [],
                "pair_results": [],
                "groups": [
                    [0]
                ]
                if articles
                else [],
                "representative_indices": [
                    0
                ]
                if articles
                else [],
            },
        )

    candidates = (
        generate_candidate_pairs(
            articles
        )
    )

    if verbose:

        total_possible_pairs = (
            len(articles)
            * (
                len(articles)
                - 1
            )
            // 2
        )

        print(
            f"전체 가능한 pair: "
            f"{total_possible_pairs}"
        )

        print(
            f"유사도 후보 pair: "
            f"{len(candidates)}"
        )

    if candidates:

        pair_results = (
            classify_candidate_pairs(
                articles=articles,
                candidates=candidates,
                verbose=verbose,
            )
        )

    else:

        pair_results = []

    groups = build_event_groups(
        articles=articles,
        pair_results=pair_results,
    )

    representative_indices = []

    for group in groups:

        representative_index = (
            select_group_representative(
                articles=articles,
                group=group,
            )
        )

        representative_indices.append(
            representative_index
        )

    representatives = [
        articles[
            index
        ]
        for index
        in representative_indices
    ]

    duplicate_groups = [
        group
        for group
        in groups
        if len(group) > 1
    ]

    if verbose:

        print()
        print(
            "[EVENT DEDUP SUMMARY]"
        )

        print(
            f"동일 사건 그룹: "
            f"{len(duplicate_groups)}개"
        )

        print(
            f"제거된 중복 기사: "
            f"{len(articles) - len(representatives)}개"
        )

        print(
            f"최종 대표 기사: "
            f"{len(representatives)}개"
        )

        for number, group in enumerate(
            duplicate_groups,
            start=1,
        ):

            print()
            print(
                f"  GROUP {number}"
            )

            representative_index = (
                select_group_representative(
                    articles,
                    group,
                )
            )

            for index in group:

                marker = (
                    "KEEP"
                    if index
                    == representative_index
                    else "DROP"
                )

                print(
                    f"    {marker} | "
                    f"{getattr(articles[index], 'source', '')} | "
                    f"{getattr(articles[index], 'title', '')}"
                )

    audit = {
        "candidate_pairs":
            candidates,

        "pair_results":
            pair_results,

        "groups":
            groups,

        "representative_indices":
            representative_indices,
    }

    return (
        representatives,
        audit,
    )


# ============================================================
# AUDIT CSV
# ============================================================

def save_dedup_audit(
    articles,
    audit,
    start_date,
    end_date,
):

    output_path = Path(
        "outputs"
    ) / (
        "event_dedup_"
        f"{start_date.strftime('%Y%m%d')}_"
        f"{end_date.strftime('%Y%m%d')}.csv"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    groups = audit.get(
        "groups",
        [],
    )

    representative_indices = set(
        audit.get(
            "representative_indices",
            [],
        )
    )

    group_lookup = {}

    for group_number, group in enumerate(
        groups,
        start=1,
    ):

        for index in group:

            group_lookup[
                index
            ] = group_number

    fieldnames = [
        "group_id",
        "is_representative",
        "source",
        "title",
        "published_at",
        "url",
        "body_chars",
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

        for index, article in enumerate(
            articles
        ):

            body_chars = len(
                article_body(
                    article
                )
            )

            writer.writerow(
                {
                    "group_id":
                        group_lookup.get(
                            index,
                            "",
                        ),

                    "is_representative":
                        (
                            index
                            in representative_indices
                        ),

                    "source":
                        clean_text(
                            getattr(
                                article,
                                "source",
                                "",
                            )
                        ),

                    "title":
                        clean_text(
                            getattr(
                                article,
                                "title",
                                "",
                            )
                        ),

                    "published_at":
                        clean_text(
                            getattr(
                                article,
                                "published_at",
                                "",
                            )
                        ),

                    "url":
                        clean_text(
                            getattr(
                                article,
                                "url",
                                "",
                            )
                        ),

                    "body_chars":
                        body_chars,
                }
            )

    return output_path
