import csv
import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


TRAIN_LABELS_PATH = Path("data/human_labels_train_v1.csv")
VALIDATION_LABELS_PATH = Path("data/human_labels_validation_v1.csv")
FALSE_POSITIVES_PATH = Path("data/historical_false_positives_v1.csv")
HUMAN_RULES_PATH = Path("data/human_rules_v1.txt")
POSITIVE_PROFILE_PATH = Path("outputs/kpsea_positive_profile_v1.json")
CURRENT_DB_PATH = Path("data/articles.db")

OUTPUT_JSON_PATH = Path("outputs/kpsea_rubric_v1.json")
OUTPUT_TXT_PATH = Path("outputs/kpsea_rubric_v1.txt")

MODEL = "gpt-6-sol"
BODY_EXCERPT_CHARS = 1200
TOP_NEIGHBORS_PER_EXAMPLE = 3


RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "rubric_name": {"type": "string"},
        "scope": {"type": "string"},
        "core_principle": {"type": "string"},
        "precedence_rules": {
            "type": "array",
            "items": {"type": "string"},
        },
        "keep_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                    "strong_signals": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "required_context": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "counterexamples": {
                        "type": "array",
                        "items": {"type": "string"},
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
                    "rule_id": {"type": "string"},
                    "name": {"type": "string"},
                    "definition": {"type": "string"},
                    "strong_signals": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "exceptions": {
                        "type": "array",
                        "items": {"type": "string"},
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
                    "rule_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "keep_when": {"type": "array", "items": {"type": "string"}},
                    "drop_when": {"type": "array", "items": {"type": "string"}},
                    "review_when": {"type": "array", "items": {"type": "string"}},
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
            "items": {"type": "string"},
        },
        "decision_procedure": {
            "type": "array",
            "items": {"type": "string"},
        },
        "training_evidence_summary": {
            "type": "object",
            "properties": {
                "keep_examples_used": {"type": "integer"},
                "drop_examples_used": {"type": "integer"},
                "historical_false_positive_marks_used": {"type": "integer"},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "keep_examples_used",
                "drop_examples_used",
                "historical_false_positive_marks_used",
                "notes",
            ],
            "additionalProperties": False,
        },
        "limitations": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "rubric_name",
        "scope",
        "core_principle",
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


def load_client():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY를 찾을 수 없습니다.")
    return OpenAI(api_key=api_key)


def require_file(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"필요한 파일이 없습니다: {path}")


def load_csv(path: Path):
    require_file(path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def load_json(path: Path):
    require_file(path)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_text(path: Path):
    require_file(path)
    return path.read_text(encoding="utf-8").strip()


def load_article_bodies():
    """현재 crawler DB의 article_id -> body mapping."""
    if not CURRENT_DB_PATH.exists():
        return {}

    connection = sqlite3.connect(CURRENT_DB_PATH)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()
    cursor.execute("SELECT id, paragraphs FROM articles")

    result = {}
    for row in cursor.fetchall():
        raw = row["paragraphs"] or "[]"
        try:
            paragraphs = json.loads(raw)
        except json.JSONDecodeError:
            paragraphs = []

        if not isinstance(paragraphs, list):
            paragraphs = []

        body = "\n\n".join(
            str(item).strip()
            for item in paragraphs
            if str(item).strip()
        )
        result[str(row["id"])] = body

    connection.close()
    return result


def format_training_examples(rows, body_map):
    blocks = []

    for index, row in enumerate(rows, start=1):
        article_id = str(row.get("article_id", "")).strip()
        label = row.get("human_label", "").strip()
        title = row.get("title", "").strip()
        source = row.get("source", "").strip()
        group = row.get("group", "").strip()
        similarity = row.get("mean_top5_similarity", "").strip()

        body = body_map.get(article_id, "")[:BODY_EXCERPT_CHARS]

        neighbors = []
        for rank in range(1, TOP_NEIGHBORS_PER_EXAMPLE + 1):
            neighbor_title = row.get(f"positive_{rank}_title", "").strip()
            neighbor_similarity = row.get(f"positive_{rank}_similarity", "").strip()
            if neighbor_title:
                neighbors.append(
                    f"{rank}. {neighbor_title} (similarity={neighbor_similarity})"
                )

        block = [
            f"[TRAINING EXAMPLE {index}]",
            f"LABEL: {label}",
            f"GROUP: {group}",
            f"MEAN_TOP5_SIMILARITY: {similarity}",
            f"SOURCE: {source}",
            f"TITLE: {title}",
        ]

        if body:
            block.append(f"BODY EXCERPT:\n{body}")

        if neighbors:
            block.append("NEAREST HISTORICAL POSITIVES:\n" + "\n".join(neighbors))

        blocks.append("\n".join(block))

    return "\n\n----------------------------------------\n\n".join(blocks)


def format_false_positives(rows):
    # 제목 기준으로 중복을 묶되, 몇 번 표시됐는지도 남긴다.
    grouped = {}
    for row in rows:
        title = row.get("positive_title", "").strip()
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
        if len(item["contexts"]) < 3:
            item["contexts"].append(
                row.get("review_title", "").strip()
            )

    blocks = []
    for index, (title, info) in enumerate(grouped.items(), start=1):
        contexts = " | ".join(x for x in info["contexts"] if x)
        blocks.append(
            f"{index}. {title}\n"
            f"   marked_count={info['count']}\n"
            f"   appeared_near_review_articles={contexts}"
        )

    return "\n".join(blocks)


def build_prompt(profile, human_rules, train_rows, false_positive_rows, body_map):
    keep_count = sum(row.get("human_label") == "KEEP" for row in train_rows)
    drop_count = sum(row.get("human_label") == "DROP" for row in train_rows)

    examples_text = format_training_examples(train_rows, body_map)
    false_positive_text = format_false_positives(false_positive_rows)

    return f"""
당신의 임무는 토목·구조 엔지니어링 뉴스 선별을 위한 'Rubric v1'을 만드는 것이다.

이 rubric은 일반적인 건설뉴스 상식을 만드는 작업이 아니다.
아래에 제공되는 특정 사용자의 실제 인간 라벨과 명시적 규칙을 재현하는 것이 목적이다.

[증거의 우선순위]
1. HUMAN-AUTHORED RULES: 사용자가 직접 적은 규칙. 가장 높은 우선순위.
2. HUMAN-LABELED TRAINING EXAMPLES: 사용자가 직접 KEEP/DROP한 실제 기사.
3. HISTORICAL FALSE POSITIVES: 과거 KPSEA positive 기사 중 사용자가 노란색으로 제외 표시한 사례.
4. POSITIVE PROFILE: 과거 KPSEA 채택 데이터의 일반적 분포를 보여주는 보조 근거.

충돌할 경우 위 순위를 따른다.
Positive Profile에 많이 나타난 주제라도 HUMAN RULE 또는 DROP 사례가 배제하면 제외 규칙을 우선한다.

[중요한 해석 원칙]
- 단순 키워드 매칭 규칙을 만들지 마라.
- '철도', '도로', 'AI', 'BIM', '안전' 같은 단어 자체는 KEEP 조건이 아니다.
- 해당 내용이 토목·구조 엔지니어링, 구조물, 설계/해석/시공/CM, 기술형입찰, 수주/계약/심의 등과 어떤 관계인지 조건부로 판단해야 한다.
- CLOSE/FAR/BOUNDARY는 정답 라벨이 아니라 embedding 거리 정보일 뿐이다. 인간 KEEP/DROP이 정답이다.
- CLOSE인데 DROP인 사례와 FAR인데 KEEP인 사례를 특히 중요하게 사용하라. 이는 단순 유사도로 설명되지 않는 decision boundary를 보여준다.
- Historical false positive는 '과거에 KPSEA에 실렸으므로 KEEP'이라는 가정을 깨는 반례로 사용한다.
- REVIEW는 억지로 많이 만들지 말고, KEEP과 DROP 규칙이 충돌하거나 기사 정보만으로 핵심 관련성을 결정하기 어려운 경우에만 사용한다.

[HUMAN-AUTHORED RULES]
{human_rules}

[POSITIVE PROFILE - 보조 근거]
{json.dumps(profile, ensure_ascii=False, indent=2)}

[HUMAN-LABELED TRAINING EXAMPLES]
총 {len(train_rows)}개 / KEEP {keep_count}개 / DROP {drop_count}개

{examples_text}

[HISTORICAL POSITIVES MARKED AS EXCLUDE]
표시 인스턴스 {len(false_positive_rows)}개. 같은 제목이 여러 번 나타날 수 있다.

{false_positive_text}

[출력 지침]
1. 실제 신규 기사에 바로 적용할 수 있을 만큼 구체적인 규칙을 만든다.
2. KEEP 규칙과 DROP 규칙 모두 예외/조건을 명확히 한다.
3. 주제 자체보다 '토목·구조 엔지니어링에 대한 직접성'과 '정보의 실질적 가치'를 중심으로 한다.
4. conditional_rules에서 철도/도로/수자원/AI/BIM/안전/기업뉴스처럼 문맥에 따라 KEEP과 DROP이 갈리는 영역을 적극적으로 명시한다.
5. decision_procedure는 실제 classifier가 순서대로 실행할 수 있는 판정 절차로 작성한다.
6. validation 데이터는 제공되지 않았다. 현재 training evidence에만 맞추되 과적합을 피한다.
""".strip()


def generate_rubric(client, prompt):
    response = client.responses.create(
        model=MODEL,
        reasoning={"effort": "medium"},
        input=[
            {
                "role": "system",
                "content": (
                    "당신은 토목·구조 엔지니어링 뉴스 분류 시스템의 "
                    "의사결정 규칙을 설계하는 데이터 분석가다. "
                    "사용자의 명시적 판단을 최우선으로 재현하고, "
                    "관찰된 증거와 추론을 구분한다."
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
                "name": "kpsea_rubric_v1",
                "strict": True,
                "schema": RUBRIC_SCHEMA,
            }
        },
    )

    return json.loads(response.output_text)


def save_json(rubric):
    OUTPUT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_JSON_PATH.open("w", encoding="utf-8") as file:
        json.dump(rubric, file, ensure_ascii=False, indent=2)


def save_txt(rubric):
    lines = []
    lines.append("=" * 80)
    lines.append(rubric["rubric_name"])
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"[SCOPE]\n{rubric['scope']}")
    lines.append("")
    lines.append(f"[CORE PRINCIPLE]\n{rubric['core_principle']}")
    lines.append("")

    lines.append("[PRECEDENCE RULES]")
    for item in rubric["precedence_rules"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("[KEEP RULES]")
    for rule in rubric["keep_rules"]:
        lines.append(f"\n{rule['rule_id']} | {rule['name']}")
        lines.append(rule["definition"])
        lines.append("  Strong signals:")
        for item in rule["strong_signals"]:
            lines.append(f"  - {item}")
        lines.append("  Required context:")
        for item in rule["required_context"]:
            lines.append(f"  - {item}")
        lines.append("  Counterexamples:")
        for item in rule["counterexamples"]:
            lines.append(f"  - {item}")
    lines.append("")

    lines.append("[DROP RULES]")
    for rule in rubric["drop_rules"]:
        lines.append(f"\n{rule['rule_id']} | {rule['name']}")
        lines.append(rule["definition"])
        lines.append("  Strong signals:")
        for item in rule["strong_signals"]:
            lines.append(f"  - {item}")
        lines.append("  Exceptions:")
        for item in rule["exceptions"]:
            lines.append(f"  - {item}")
    lines.append("")

    lines.append("[CONDITIONAL RULES]")
    for rule in rubric["conditional_rules"]:
        lines.append(f"\n{rule['rule_id']} | {rule['topic']}")
        lines.append("  KEEP when:")
        for item in rule["keep_when"]:
            lines.append(f"  - {item}")
        lines.append("  DROP when:")
        for item in rule["drop_when"]:
            lines.append(f"  - {item}")
        lines.append("  REVIEW when:")
        for item in rule["review_when"]:
            lines.append(f"  - {item}")
    lines.append("")

    lines.append("[REVIEW RULES]")
    for item in rubric["review_rules"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("[DECISION PROCEDURE]")
    for index, item in enumerate(rubric["decision_procedure"], start=1):
        lines.append(f"{index}. {item}")
    lines.append("")

    lines.append("[TRAINING EVIDENCE SUMMARY]")
    evidence = rubric["training_evidence_summary"]
    lines.append(f"KEEP examples: {evidence['keep_examples_used']}")
    lines.append(f"DROP examples: {evidence['drop_examples_used']}")
    lines.append(
        "Historical false-positive marks: "
        f"{evidence['historical_false_positive_marks_used']}"
    )
    for item in evidence["notes"]:
        lines.append(f"- {item}")
    lines.append("")

    lines.append("[LIMITATIONS]")
    for item in rubric["limitations"]:
        lines.append(f"- {item}")

    OUTPUT_TXT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main():
    print("\nKPSEA Rubric v1 생성 시작")
    print(f"모델: {MODEL}")

    train_rows = load_csv(TRAIN_LABELS_PATH)
    # 파일 존재만 확인해 둔다. 이 파일은 rubric 생성에는 절대 사용하지 않는다.
    require_file(VALIDATION_LABELS_PATH)
    false_positive_rows = load_csv(FALSE_POSITIVES_PATH)
    human_rules = load_text(HUMAN_RULES_PATH)
    positive_profile = load_json(POSITIVE_PROFILE_PATH)
    body_map = load_article_bodies()

    keep_count = sum(row.get("human_label") == "KEEP" for row in train_rows)
    drop_count = sum(row.get("human_label") == "DROP" for row in train_rows)

    print(f"Training labels: {len(train_rows)}개 (KEEP {keep_count}, DROP {drop_count})")
    print(f"Historical false-positive marks: {len(false_positive_rows)}개")
    print(f"DB body 연결 가능 기사: {len(body_map)}개")
    print("Validation labels는 rubric 생성에 사용하지 않습니다.")

    prompt = build_prompt(
        positive_profile,
        human_rules,
        train_rows,
        false_positive_rows,
        body_map,
    )

    client = load_client()
    rubric = generate_rubric(client, prompt)

    save_json(rubric)
    save_txt(rubric)

    print("\nRubric v1 생성 완료")
    print(f"JSON: {OUTPUT_JSON_PATH}")
    print(f"TXT : {OUTPUT_TXT_PATH}")
    print("\n다음 단계는 validation 27개에 이 rubric을 적용해 성능을 측정하는 것입니다.")


if __name__ == "__main__":
    main()
