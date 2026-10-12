import csv
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


LABELS_PATH = Path("data/human_labels_rubric_v2.csv")
FALSE_POSITIVES_PATH = Path("data/historical_false_positives_v1.csv")
HUMAN_RULES_PATH = Path("data/human_rules_v1.txt")

POSITIVE_PROFILE_PATH = Path(
    "outputs/kpsea_positive_profile_v1.json"
)

RUBRIC_V1_PATH = Path(
    "outputs/kpsea_rubric_v1.txt"
)

AUDIT_REPORT_PATH = Path(
    "outputs/label_audit_v1_to_v2.txt"
)

CURRENT_DB_PATH = Path(
    "data/articles.db"
)

OUTPUT_JSON_PATH = Path(
    "outputs/kpsea_rubric_v2.json"
)

OUTPUT_TXT_PATH = Path(
    "outputs/kpsea_rubric_v2.txt"
)


MODEL = "gpt-6-sol"

# 기존 63개는 이미 v1 형성에 사용되었으므로 간결하게,
# 새롭게 감사한 27개와 핵심 오류 사례는 더 자세히 제공한다.
TRAIN_BODY_EXCERPT_CHARS = 650
DEV_BODY_EXCERPT_CHARS = 1500
AUDIT_BODY_EXCERPT_CHARS = 2500

TOP_NEIGHBORS_PER_EXAMPLE = 2


RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "rubric_name": {
            "type": "string"
        },
        "version": {
            "type": "string"
        },
        "scope": {
            "type": "string"
        },
        "core_principle": {
            "type": "string"
        },
        "changes_from_v1": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "change_id": {
                        "type": "string"
                    },
                    "problem_in_v1": {
                        "type": "string"
                    },
                    "change": {
                        "type": "string"
                    },
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                },
                "required": [
                    "change_id",
                    "problem_in_v1",
                    "change",
                    "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "precedence_rules": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
        "keep_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string"
                    },
                    "name": {
                        "type": "string"
                    },
                    "definition": {
                        "type": "string"
                    },
                    "strong_signals": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                    "required_context": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                    "counterexamples": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                },
                "required": [
                    "rule_id",
                    "name",
                    "definition",
                    "strong_signals",
                    "required_context",
                    "counterexamples",
                ],
                "additionalProperties": False,
            },
        },
        "drop_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string"
                    },
                    "name": {
                        "type": "string"
                    },
                    "definition": {
                        "type": "string"
                    },
                    "strong_signals": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                    "exceptions": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                },
                "required": [
                    "rule_id",
                    "name",
                    "definition",
                    "strong_signals",
                    "exceptions",
                ],
                "additionalProperties": False,
            },
        },
        "conditional_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {
                        "type": "string"
                    },
                    "topic": {
                        "type": "string"
                    },
                    "keep_when": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                    "drop_when": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                    "review_when": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                    },
                },
                "required": [
                    "rule_id",
                    "topic",
                    "keep_when",
                    "drop_when",
                    "review_when",
                ],
                "additionalProperties": False,
            },
        },
        "review_rules": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
        "decision_procedure": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
        "training_evidence_summary": {
            "type": "object",
            "properties": {
                "total_human_examples_used": {
                    "type": "integer"
                },
                "keep_examples_used": {
                    "type": "integer"
                },
                "drop_examples_used": {
                    "type": "integer"
                },
                "audited_development_examples_used": {
                    "type": "integer"
                },
                "historical_false_positive_marks_used": {
                    "type": "integer"
                },
                "notes": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                },
            },
            "required": [
                "total_human_examples_used",
                "keep_examples_used",
                "drop_examples_used",
                "audited_development_examples_used",
                "historical_false_positive_marks_used",
                "notes",
            ],
            "additionalProperties": False,
        },
        "limitations": {
            "type": "array",
            "items": {
                "type": "string"
            },
        },
    },
    "required": [
        "rubric_name",
        "version",
        "scope",
        "core_principle",
        "changes_from_v1",
        "precedence_rules",
        "keep_rules",
        "drop_rules",
        "conditional_rules",
        "review_rules",
        "decision_procedure",
        "training_evidence_summary",
        "limitations",
    ],
    "additionalProperties": False,
}


def require_file(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"필요한 파일이 없습니다: {path}"
        )


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


def load_csv(path: Path):
    require_file(path)

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        return list(
            csv.DictReader(file)
        )


def load_text(path: Path):
    require_file(path)

    return path.read_text(
        encoding="utf-8"
    ).strip()


def load_json(path: Path):
    require_file(path)

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def load_article_database():
    """
    article_id, URL, title 세 방식으로 본문을 찾을 수 있게 한다.
    """
    by_id = {}
    by_url = {}
    by_title = {}

    if not CURRENT_DB_PATH.exists():
        return (
            by_id,
            by_url,
            by_title,
        )

    connection = sqlite3.connect(
        CURRENT_DB_PATH
    )

    connection.row_factory = sqlite3.Row

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            title,
            url,
            paragraphs
        FROM articles
        """
    )

    for row in cursor.fetchall():
        raw = row["paragraphs"] or "[]"

        try:
            paragraphs = json.loads(raw)
        except json.JSONDecodeError:
            paragraphs = []

        if not isinstance(
            paragraphs,
            list,
        ):
            paragraphs = []

        body = "\n\n".join(
            str(item).strip()
            for item in paragraphs
            if str(item).strip()
        )

        article = {
            "id": str(row["id"]),
            "title": (
                row["title"]
                or ""
            ).strip(),
            "url": (
                row["url"]
                or ""
            ).strip(),
            "body": body,
        }

        by_id[
            article["id"]
        ] = article

        if article["url"]:
            by_url[
                article["url"]
            ] = article

        if article["title"]:
            by_title[
                article["title"]
            ] = article

    connection.close()

    return (
        by_id,
        by_url,
        by_title,
    )


def normalize_article_id(value):
    value = str(
        value or ""
    ).strip()

    if value.endswith(".0"):
        value = value[:-2]

    return value


def find_body(
    row,
    by_id,
    by_url,
    by_title,
):
    article_id = normalize_article_id(
        row.get(
            "article_id",
            ""
        )
    )

    url = (
        row.get(
            "url",
            ""
        )
        .strip()
    )

    title = (
        row.get(
            "title",
            ""
        )
        .strip()
    )

    article = None

    if (
        article_id
        and article_id in by_id
    ):
        article = by_id[article_id]

    elif (
        url
        and url in by_url
    ):
        article = by_url[url]

    elif (
        title
        and title in by_title
    ):
        article = by_title[title]

    if not article:
        return ""

    return (
        article.get(
            "body",
            ""
        )
        .strip()
    )


def choose_body_limit(row):
    audit_status = (
        row.get(
            "label_audit_status",
            ""
        )
        .strip()
    )

    data_role = (
        row.get(
            "data_role",
            ""
        )
        .strip()
    )

    if audit_status in {
        "corrected",
        "reviewed_no_change",
    }:
        return AUDIT_BODY_EXCERPT_CHARS

    if data_role == "development_audited":
        return DEV_BODY_EXCERPT_CHARS

    return TRAIN_BODY_EXCERPT_CHARS


def format_examples(
    rows,
    by_id,
    by_url,
    by_title,
):
    blocks = []

    for index, row in enumerate(
        rows,
        start=1,
    ):
        article_id = normalize_article_id(
            row.get(
                "article_id",
                ""
            )
        )

        label = (
            row.get(
                "human_label",
                ""
            )
            .strip()
            .upper()
        )

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

        data_role = (
            row.get(
                "data_role",
                ""
            )
            .strip()
        )

        audit_status = (
            row.get(
                "label_audit_status",
                ""
            )
            .strip()
        )

        audit_note = (
            row.get(
                "label_audit_note",
                ""
            )
            .strip()
        )

        original_label = (
            row.get(
                "original_human_label",
                ""
            )
            .strip()
            .upper()
        )

        body = find_body(
            row,
            by_id,
            by_url,
            by_title,
        )

        body = body[
            :choose_body_limit(row)
        ]

        block = [
            f"[HUMAN EXAMPLE {index}]",
            f"ARTICLE_ID: {article_id}",
            f"ROLE: {data_role}",
            f"FINAL LABEL: {label}",
            f"SOURCE: {source}",
            f"TITLE: {title}",
        ]

        if (
            original_label
            and original_label != label
        ):
            block.append(
                f"ORIGINAL LABEL BEFORE AUDIT: "
                f"{original_label}"
            )

        if audit_status:
            block.append(
                f"AUDIT STATUS: {audit_status}"
            )

        if audit_note:
            block.append(
                f"AUDIT NOTE: {audit_note}"
            )

        if body:
            block.append(
                "BODY EXCERPT:\n"
                + body
            )

        # 과거 embedding neighbor는 판정 기준이 아니지만,
        # train 파일에 존재하면 최대 2개까지만 참고용으로 남긴다.
        neighbors = []

        for rank in range(
            1,
            TOP_NEIGHBORS_PER_EXAMPLE + 1,
        ):
            neighbor_title = (
                row.get(
                    f"positive_{rank}_title",
                    ""
                )
                .strip()
            )

            if neighbor_title:
                neighbors.append(
                    f"{rank}. {neighbor_title}"
                )

        if neighbors:
            block.append(
                "NEAREST HISTORICAL POSITIVES "
                "(reference only; not labels):\n"
                + "\n".join(neighbors)
            )

        blocks.append(
            "\n".join(block)
        )

    return (
        "\n\n"
        + "-" * 72
        + "\n\n"
    ).join(blocks)


def format_false_positives(rows):
    grouped = {}

    for row in rows:
        title = (
            row.get(
                "positive_title",
                ""
            )
            .strip()
        )

        if not title:
            continue

        item = grouped.setdefault(
            title,
            {
                "count": 0,
                "contexts": [],
            },
        )

        item["count"] += 1

        context = (
            row.get(
                "review_title",
                ""
            )
            .strip()
        )

        if (
            context
            and len(
                item["contexts"]
            ) < 2
        ):
            item[
                "contexts"
            ].append(
                context
            )

    blocks = []

    for index, (
        title,
        info,
    ) in enumerate(
        grouped.items(),
        start=1,
    ):
        contexts = " | ".join(
            info["contexts"]
        )

        blocks.append(
            f"{index}. {title}\n"
            f"   marked_count="
            f"{info['count']}\n"
            f"   appeared_near="
            f"{contexts}"
        )

    return "\n".join(
        blocks
    )


def build_prompt(
    labels,
    false_positive_rows,
    human_rules,
    positive_profile,
    rubric_v1,
    audit_report,
    by_id,
    by_url,
    by_title,
):
    counts = Counter(
        (
            row.get(
                "human_label",
                ""
            )
            .strip()
            .upper()
        )
        for row in labels
    )

    dev_count = sum(
        1
        for row in labels
        if (
            row.get(
                "data_role",
                ""
            )
            == "development_audited"
        )
    )

    examples_text = format_examples(
        labels,
        by_id,
        by_url,
        by_title,
    )

    false_positive_text = (
        format_false_positives(
            false_positive_rows
        )
    )

    return f"""
당신의 임무는 'KPSEA 토목·구조 엔지니어링 뉴스 선별 Rubric v2'를 만드는 것이다.

Rubric v2는 v1을 단순히 길게 다시 쓰는 작업이 아니다.
Rubric v1의 실제 27개 development 평가에서 드러난 경계 오류를 수정하고,
감사된 인간 라벨과 사용자의 명시적 규칙을 가장 높은 우선순위로 반영해야 한다.

중요: 아래 27개 development 사례는 이미 v1 평가와 라벨 감사에 사용되었다.
따라서 이 데이터는 더 이상 독립 validation/test set이 아니다.
Rubric v2는 이 데이터를 학습·개발 증거로 사용하며,
최종 성능은 이후 별도의 새로운 test set으로 평가해야 한다.

[증거 우선순위]
1. HUMAN-AUTHORED RULES
2. 감사 후 확정된 HUMAN LABELS
3. LABEL AUDIT REPORT의 구체적 경계 교정
4. HISTORICAL FALSE POSITIVES
5. POSITIVE PROFILE
6. RUBRIC v1

충돌 시 위 순위를 따른다.

[Rubric v2에서 반드시 해결해야 할 v1 문제]

A. 일반 안전홍보와 행정 현장점검
- '안전', '현장', '점검'이라는 단어만으로 KEEP하지 않는다.
- 축제·캠페인에서의 일반 산업안전 수칙 홍보는 DROP.
- 개발제한구역 불법행위 확인처럼 행정 단속·위법 여부 확인이 중심이면 DROP.
- 반대로 토목 구조물 사고 원인, 구조안전 진단, 보수·보강,
  구체적인 건설현장 위험관리 절차와 직접 연결되면 KEEP할 수 있다.

B. 건축 제외와 구조공학 직접성의 충돌
- 건축 일반, 주택, 병원은 원칙적으로 DROP.
- 그러나 '건축'이라는 맥락에 있다는 이유만으로 구조공학 자체를 배제하지 않는다.
- 구조기사/구조기술자, 구조설계·해석, 구조용역,
  구조 관련 PQ·입찰·자격·제도처럼 구조공학 자체가 중심이면 KEEP.

C. 핵심 인프라 발주기관의 정책·운영방침
- 국가철도공단 등 핵심 인프라 발주기관의 기사라고 자동 KEEP하지 않는다.
- 단순 슬로건·CEO 홍보는 DROP.
- 그러나 사업 수행, 안전관리, 엔지니어링 조달·운영방향 등
  업계 의사결정에 실질적으로 관련된 내용이 중심이면 KEEP 가능.

D. 정치인 중심 기사
- 프로젝트 사실이 본문에 포함돼 있다는 이유만으로 KEEP하지 않는다.
- 기사 중심 사건이 특정 정치인의 환영, 주장, 지원 약속, 정쟁이면 DROP.
- 정치인은 부수적이고 독립적인 사업 단계·설계·입찰·계약·안전 정보가
  기사 중심일 때만 관련 KEEP 규칙을 적용한다.

E. 플랜트/EPC 범위
- '플랜트', 'EPC', '모듈', '패키지'라는 이유만으로 KEEP하지 않는다.
- 공정기술, 액화공정, 생산패키지, 일반 기계·공정 모듈화는
  토목·구조 직접성이 없으면 DROP.
- 기초, 토목공사, 구조물, 구조설계, 토목/구조 CM 등
  civil/structural component가 기사 중심에 직접 나타나야 KEEP.

F. 인사와 주택
- [인사]는 국토교통부·행정안전부만 KEEP.
- 산림청 등 그 외 기관 인사는 DROP.
- 아파트 리모델링 수주 같은 일반 주택사업은 DROP.
- 다만 주택이라는 프로젝트 맥락과 별개로 구조공학 자체가 기사 중심이면
  B의 구조공학 직접성 규칙을 적용한다.

[HUMAN-AUTHORED RULES]
{human_rules}

[LABEL AUDIT REPORT]
{audit_report}

[RUBRIC v1 - 수정 대상]
{rubric_v1}

[POSITIVE PROFILE - 보조 근거]
{json.dumps(
    positive_profile,
    ensure_ascii=False,
    indent=2,
)}

[FINAL HUMAN EXAMPLES FOR RUBRIC v2]
총 {len(labels)}개
KEEP {counts.get('KEEP', 0)}개
DROP {counts.get('DROP', 0)}개
Audited development examples {dev_count}개

{examples_text}

[HISTORICAL POSITIVES MARKED AS EXCLUDE]
표시 인스턴스 {len(false_positive_rows)}개.
과거 KPSEA 채택 이력은 현재 KEEP 보증이 아니다.

{false_positive_text}

[출력 지침]
1. Rubric v2는 신규 기사에 바로 적용할 수 있는 독립적인 완성본이어야 한다.
2. v1의 규칙 ID를 기계적으로 유지할 필요는 없지만, 규칙은 간결하고 상호 중복이 적어야 한다.
3. 키워드가 아니라 '기사의 중심 사건/과업'과 '토목·구조 직접성'을 판단한다.
4. 명시적 제외 규칙과 구조공학 직접성 예외의 우선순위를 분명하게 써라.
5. political mention과 politician-centered article을 구분하라.
6. general construction safety publicity와 engineering-relevant safety information을 구분하라.
7. plant/process engineering과 civil/structural engineering을 구분하라.
8. REVIEW는 정보 부족 또는 중심성이 진짜 양립할 때만 사용하되,
   억지로 KEEP/DROP하는 것보다 합리적인 REVIEW가 낫다.
9. changes_from_v1에는 실제 v1 오류를 어떤 규칙으로 고쳤는지 명시한다.
10. decision_procedure는 classifier가 순서대로 적용할 수 있게 작성한다.
11. 이 90개에 대한 성능을 '독립 validation 성능'이라고 주장하지 않는다.
""".strip()


def generate_rubric(
    client,
    prompt,
):
    response = client.responses.create(
        model=MODEL,
        reasoning={
            "effort": "medium"
        },
        input=[
            {
                "role": "system",
                "content": (
                    "당신은 토목·구조 엔지니어링 뉴스 분류 시스템의 "
                    "의사결정 규칙을 설계하는 데이터 분석가다. "
                    "사용자 명시 규칙과 감사된 인간 라벨을 우선하고, "
                    "v1의 실제 오류를 과도한 일반화 없이 수정한다."
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
                "name": "kpsea_rubric_v2",
                "strict": True,
                "schema": RUBRIC_SCHEMA,
            }
        },
    )

    return json.loads(
        response.output_text
    )


def save_json(rubric):
    OUTPUT_JSON_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_JSON_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            rubric,
            file,
            ensure_ascii=False,
            indent=2,
        )


def save_txt(rubric):
    lines = []

    lines.append("=" * 80)
    lines.append(
        rubric["rubric_name"]
    )
    lines.append("=" * 80)
    lines.append("")

    lines.append(
        f"[VERSION]\n"
        f"{rubric['version']}"
    )
    lines.append("")

    lines.append(
        f"[SCOPE]\n"
        f"{rubric['scope']}"
    )
    lines.append("")

    lines.append(
        f"[CORE PRINCIPLE]\n"
        f"{rubric['core_principle']}"
    )
    lines.append("")

    lines.append(
        "[CHANGES FROM v1]"
    )

    for change in (
        rubric[
            "changes_from_v1"
        ]
    ):
        lines.append(
            f"\n{change['change_id']}"
        )
        lines.append(
            "  Problem in v1: "
            + change[
                "problem_in_v1"
            ]
        )
        lines.append(
            "  Change: "
            + change[
                "change"
            ]
        )
        lines.append(
            "  Evidence:"
        )

        for item in (
            change[
                "evidence"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

    lines.append("")
    lines.append(
        "[PRECEDENCE RULES]"
    )

    for item in (
        rubric[
            "precedence_rules"
        ]
    ):
        lines.append(
            f"- {item}"
        )

    lines.append("")
    lines.append(
        "[KEEP RULES]"
    )

    for rule in (
        rubric[
            "keep_rules"
        ]
    ):
        lines.append(
            f"\n{rule['rule_id']} | "
            f"{rule['name']}"
        )
        lines.append(
            rule[
                "definition"
            ]
        )
        lines.append(
            "  Strong signals:"
        )

        for item in (
            rule[
                "strong_signals"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

        lines.append(
            "  Required context:"
        )

        for item in (
            rule[
                "required_context"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

        lines.append(
            "  Counterexamples:"
        )

        for item in (
            rule[
                "counterexamples"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

    lines.append("")
    lines.append(
        "[DROP RULES]"
    )

    for rule in (
        rubric[
            "drop_rules"
        ]
    ):
        lines.append(
            f"\n{rule['rule_id']} | "
            f"{rule['name']}"
        )
        lines.append(
            rule[
                "definition"
            ]
        )
        lines.append(
            "  Strong signals:"
        )

        for item in (
            rule[
                "strong_signals"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

        lines.append(
            "  Exceptions:"
        )

        for item in (
            rule[
                "exceptions"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

    lines.append("")
    lines.append(
        "[CONDITIONAL RULES]"
    )

    for rule in (
        rubric[
            "conditional_rules"
        ]
    ):
        lines.append(
            f"\n{rule['rule_id']} | "
            f"{rule['topic']}"
        )
        lines.append(
            "  KEEP when:"
        )

        for item in (
            rule[
                "keep_when"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

        lines.append(
            "  DROP when:"
        )

        for item in (
            rule[
                "drop_when"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

        lines.append(
            "  REVIEW when:"
        )

        for item in (
            rule[
                "review_when"
            ]
        ):
            lines.append(
                f"  - {item}"
            )

    lines.append("")
    lines.append(
        "[REVIEW RULES]"
    )

    for item in (
        rubric[
            "review_rules"
        ]
    ):
        lines.append(
            f"- {item}"
        )

    lines.append("")
    lines.append(
        "[DECISION PROCEDURE]"
    )

    for index, item in enumerate(
        rubric[
            "decision_procedure"
        ],
        start=1,
    ):
        lines.append(
            f"{index}. {item}"
        )

    evidence = (
        rubric[
            "training_evidence_summary"
        ]
    )

    lines.append("")
    lines.append(
        "[TRAINING / DEVELOPMENT EVIDENCE SUMMARY]"
    )
    lines.append(
        "Total human examples: "
        f"{evidence['total_human_examples_used']}"
    )
    lines.append(
        "KEEP examples: "
        f"{evidence['keep_examples_used']}"
    )
    lines.append(
        "DROP examples: "
        f"{evidence['drop_examples_used']}"
    )
    lines.append(
        "Audited development examples: "
        f"{evidence['audited_development_examples_used']}"
    )
    lines.append(
        "Historical false-positive marks: "
        f"{evidence['historical_false_positive_marks_used']}"
    )

    for item in (
        evidence[
            "notes"
        ]
    ):
        lines.append(
            f"- {item}"
        )

    lines.append("")
    lines.append(
        "[LIMITATIONS]"
    )

    for item in (
        rubric[
            "limitations"
        ]
    ):
        lines.append(
            f"- {item}"
        )

    OUTPUT_TXT_PATH.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def main():
    print()
    print(
        "KPSEA Rubric v2 생성 시작"
    )
    print(
        f"모델: {MODEL}"
    )

    labels = load_csv(
        LABELS_PATH
    )

    false_positive_rows = (
        load_csv(
            FALSE_POSITIVES_PATH
        )
    )

    human_rules = load_text(
        HUMAN_RULES_PATH
    )

    positive_profile = load_json(
        POSITIVE_PROFILE_PATH
    )

    rubric_v1 = load_text(
        RUBRIC_V1_PATH
    )

    audit_report = load_text(
        AUDIT_REPORT_PATH
    )

    (
        by_id,
        by_url,
        by_title,
    ) = load_article_database()

    counts = Counter(
        (
            row.get(
                "human_label",
                ""
            )
            .strip()
            .upper()
        )
        for row in labels
    )

    dev_count = sum(
        1
        for row in labels
        if (
            row.get(
                "data_role",
                ""
            )
            == "development_audited"
        )
    )

    print(
        f"Human examples: {len(labels)}개 "
        f"(KEEP {counts.get('KEEP', 0)}, "
        f"DROP {counts.get('DROP', 0)})"
    )

    print(
        f"Audited development: "
        f"{dev_count}개"
    )

    print(
        "Historical false-positive marks: "
        f"{len(false_positive_rows)}개"
    )

    print(
        "DB body 연결 가능 기사: "
        f"{len(by_id)}개"
    )

    prompt = build_prompt(
        labels,
        false_positive_rows,
        human_rules,
        positive_profile,
        rubric_v1,
        audit_report,
        by_id,
        by_url,
        by_title,
    )

    client = load_client()

    rubric = generate_rubric(
        client,
        prompt,
    )

    save_json(
        rubric
    )

    save_txt(
        rubric
    )

    print()
    print(
        "Rubric v2 생성 완료"
    )
    print(
        f"JSON: {OUTPUT_JSON_PATH}"
    )
    print(
        f"TXT : {OUTPUT_TXT_PATH}"
    )
    print()
    print(
        "주의: 기존 27개 development 사례는 "
        "Rubric v2의 독립 validation으로 다시 사용하지 않습니다."
    )
    print(
        "다음 단계는 아직 보지 않은 새로운 기사 "
        "30~50개로 fresh test set을 만드는 것입니다."
    )


if __name__ == "__main__":
    main()
