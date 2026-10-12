import csv
from collections import Counter
from pathlib import Path


TRAIN_PATH = Path("data/human_labels_train_v1.csv")
VALIDATION_PATH = Path("data/human_labels_validation_v1.csv")

AUDITED_DEV_PATH = Path("data/human_labels_dev_v1_audited.csv")
COMBINED_V2_PATH = Path("data/human_labels_rubric_v2.csv")
AUDIT_REPORT_PATH = Path("outputs/label_audit_v1_to_v2.txt")


# article_id 기준으로 validation 27개를 감사한 결과.
# 기존 파일은 덮어쓰지 않고 새로운 audited development 파일을 만든다.
AUDIT_DECISIONS = {
    "46": {
        "final_label": "DROP",
        "status": "corrected",
        "note": (
            "[인사] 산림청. 사용자 명시 규칙상 인사 기사는 "
            "국토교통부·행정안전부만 KEEP이므로 DROP으로 수정."
        ),
    },
    "165": {
        "final_label": "DROP",
        "status": "corrected",
        "note": (
            "아파트 리모델링 수주 기사. 주택 자체가 중심이고 "
            "구조공학 과업이 중심이 아니므로 DROP으로 수정."
        ),
    },
    "21": {
        "final_label": "DROP",
        "status": "corrected",
        "note": (
            "축제 현장의 산업안전 수칙 홍보 캠페인. 구체적인 "
            "토목·구조 사고 분석·점검·현장 과업이 아니므로 DROP으로 수정."
        ),
    },
    "123": {
        "final_label": "DROP",
        "status": "corrected",
        "note": (
            "개발제한구역 불법행위 현장점검. 토목·구조 엔지니어링이 아니라 "
            "행정 단속·위법 여부 확인이 중심이므로 DROP으로 수정."
        ),
    },

    # 아래 4개는 인간 라벨을 유지하되 Rubric v2의 경계 수정에 사용한다.
    "86": {
        "final_label": "DROP",
        "status": "reviewed_no_change",
        "note": (
            "GTX-C 관련 사실이 포함돼도 기사 중심 사건이 특정 정치인의 "
            "환영·지원 발언이면 DROP. 정치인 중심성 규칙 강화 필요."
        ),
    },
    "81": {
        "final_label": "DROP",
        "status": "reviewed_no_change",
        "note": (
            "LNG 플랜트 EPC·모듈 패키지는 플랜트라는 이유만으로 KEEP하지 않음. "
            "직접적인 토목·구조 component가 있어야 KEEP."
        ),
    },
    "98": {
        "final_label": "KEEP",
        "status": "reviewed_no_change",
        "note": (
            "국가철도공단 같은 핵심 인프라 발주기관의 사업 수행·안전·엔지니어링 "
            "운영방침은 실질적 정보가 있으면 KEEP 가능."
        ),
    },
    "54": {
        "final_label": "KEEP",
        "status": "reviewed_no_change",
        "note": (
            "건축 맥락이라도 구조기사·구조설계·구조용역·관련 입찰제도처럼 "
            "구조공학 자체가 중심이면 KEEP."
        ),
    },
}


def require_file(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"필요한 파일이 없습니다: {path}")


def load_csv(path: Path):
    require_file(path)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def normalize_article_id(value):
    value = str(value or "").strip()
    if value.endswith(".0"):
        value = value[:-2]
    return value


def union_fieldnames(*row_groups):
    fieldnames = []
    seen = set()

    for rows in row_groups:
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)

    for extra in (
        "data_role",
        "original_human_label",
        "label_audit_status",
        "label_audit_note",
    ):
        if extra not in seen:
            fieldnames.append(extra)
            seen.add(extra)

    return fieldnames


def write_csv(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    train_rows = load_csv(TRAIN_PATH)
    validation_rows = load_csv(VALIDATION_PATH)

    if len(train_rows) != 63:
        raise RuntimeError(
            f"예상한 training 63개가 아닙니다: {len(train_rows)}개"
        )

    if len(validation_rows) != 27:
        raise RuntimeError(
            f"예상한 validation 27개가 아닙니다: {len(validation_rows)}개"
        )

    audited_dev_rows = []
    seen_audit_ids = set()

    for original_row in validation_rows:
        row = dict(original_row)

        article_id = normalize_article_id(
            row.get("article_id", "")
        )

        original_label = (
            row.get("human_label", "")
            .strip()
            .upper()
        )

        row["article_id"] = article_id
        row["data_role"] = "development_audited"
        row["original_human_label"] = original_label

        decision = AUDIT_DECISIONS.get(article_id)

        if decision:
            seen_audit_ids.add(article_id)

            final_label = decision["final_label"]

            if final_label not in {"KEEP", "DROP"}:
                raise RuntimeError(
                    f"잘못된 final_label: {article_id} -> {final_label}"
                )

            row["human_label"] = final_label
            row["label_audit_status"] = decision["status"]
            row["label_audit_note"] = decision["note"]

        else:
            row["human_label"] = original_label
            row["label_audit_status"] = "unchanged"
            row["label_audit_note"] = ""

        audited_dev_rows.append(row)

    missing = set(AUDIT_DECISIONS) - seen_audit_ids

    if missing:
        raise RuntimeError(
            "감사 대상 article_id를 validation 파일에서 찾지 못했습니다: "
            + ", ".join(sorted(missing))
        )

    prepared_train_rows = []

    for original_row in train_rows:
        row = dict(original_row)

        row["article_id"] = normalize_article_id(
            row.get("article_id", "")
        )

        row["data_role"] = "training_v1"
        row["original_human_label"] = (
            row.get("human_label", "")
            .strip()
            .upper()
        )
        row["human_label"] = row["original_human_label"]
        row["label_audit_status"] = "not_audited"
        row["label_audit_note"] = ""

        prepared_train_rows.append(row)

    combined_rows = (
        prepared_train_rows
        + audited_dev_rows
    )

    fieldnames = union_fieldnames(
        prepared_train_rows,
        audited_dev_rows,
    )

    write_csv(
        AUDITED_DEV_PATH,
        audited_dev_rows,
        fieldnames,
    )

    write_csv(
        COMBINED_V2_PATH,
        combined_rows,
        fieldnames,
    )

    original_dev_counts = Counter(
        row.get("original_human_label", "")
        for row in audited_dev_rows
    )

    audited_dev_counts = Counter(
        row.get("human_label", "")
        for row in audited_dev_rows
    )

    combined_counts = Counter(
        row.get("human_label", "")
        for row in combined_rows
    )

    corrected_rows = [
        row
        for row in audited_dev_rows
        if row["label_audit_status"] == "corrected"
    ]

    reviewed_rows = [
        row
        for row in audited_dev_rows
        if row["label_audit_status"] == "reviewed_no_change"
    ]

    report_lines = [
        "=" * 80,
        "KPSEA Label Audit: Rubric v1 -> Rubric v2",
        "=" * 80,
        "",
        "기존 27개 validation은 Rubric v1 결과를 본 뒤 라벨 감사와 규칙 수정에 사용되었으므로",
        "이제부터 순수 validation/test set이 아니라 development set으로 취급한다.",
        "",
        "[COUNTS]",
        (
            f"Original development labels: "
            f"KEEP {original_dev_counts.get('KEEP', 0)}, "
            f"DROP {original_dev_counts.get('DROP', 0)}"
        ),
        (
            f"Audited development labels: "
            f"KEEP {audited_dev_counts.get('KEEP', 0)}, "
            f"DROP {audited_dev_counts.get('DROP', 0)}"
        ),
        (
            f"Rubric v2 combined labels (63 train + 27 audited dev): "
            f"KEEP {combined_counts.get('KEEP', 0)}, "
            f"DROP {combined_counts.get('DROP', 0)}, "
            f"TOTAL {len(combined_rows)}"
        ),
        "",
        "[CORRECTED LABELS]",
    ]

    for row in corrected_rows:
        report_lines.extend(
            [
                (
                    f"- article_id={row['article_id']} | "
                    f"{row.get('title', '')}"
                ),
                (
                    f"  {row['original_human_label']} -> "
                    f"{row['human_label']}"
                ),
                f"  이유: {row['label_audit_note']}",
            ]
        )

    report_lines.extend(
        [
            "",
            "[REVIEWED, LABEL UNCHANGED]",
        ]
    )

    for row in reviewed_rows:
        report_lines.extend(
            [
                (
                    f"- article_id={row['article_id']} | "
                    f"{row.get('title', '')}"
                ),
                f"  최종 라벨: {row['human_label']}",
                f"  Rubric v2 반영사항: {row['label_audit_note']}",
            ]
        )

    report_lines.extend(
        [
            "",
            "[METHODOLOGY NOTE]",
            (
                "Rubric v2를 이 90개에 맞춘 뒤에는 이 27개로 최종 성능을 "
                "다시 주장하지 않는다."
            ),
            (
                "Rubric v2의 최종 평가는 아직 사용하지 않은 새로운 기사 "
                "30~50개를 별도로 라벨링해 수행한다."
            ),
        ]
    )

    AUDIT_REPORT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    AUDIT_REPORT_PATH.write_text(
        "\n".join(report_lines),
        encoding="utf-8",
    )

    print()
    print("Label audit 완료")
    print(
        f"Audited development: {AUDITED_DEV_PATH}"
    )
    print(
        f"Rubric v2 combined labels: {COMBINED_V2_PATH}"
    )
    print(
        f"Audit report: {AUDIT_REPORT_PATH}"
    )
    print()
    print(
        "Audited development counts: "
        f"KEEP {audited_dev_counts.get('KEEP', 0)}, "
        f"DROP {audited_dev_counts.get('DROP', 0)}"
    )
    print(
        "Combined v2 counts: "
        f"KEEP {combined_counts.get('KEEP', 0)}, "
        f"DROP {combined_counts.get('DROP', 0)}"
    )


if __name__ == "__main__":
    main()
