from pathlib import Path


OUTPUT_DIR = Path("outputs")


def save_text_report(
    content,
    start_date,
    end_date,
):
    """
    포맷된 뉴스 결과를
    TXT 파일로 저장한다.
    """

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = (
        f"news_{start_date}_to_{end_date}.txt"
    )

    path = OUTPUT_DIR / filename

    path.write_text(
        content,
        encoding="utf-8-sig",
    )

    return path
