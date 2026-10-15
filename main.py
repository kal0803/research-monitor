"""Research Monitor: 기사 수집 → 품질검사 → Rubric → 중복 제거 → 보고서."""

from collections import Counter
from datetime import date, datetime

from collectors.dnews import DNewsCollector
from collectors.mtn import MTNCollector
from collectors.constimes import ConTimesCollector
from collectors.cenews import CENewsCollector as ConstructionEconomyCollector
from collectors.cennews import CENewsCollector as ConstructionIndustryCollector
from collectors.safetynews import SafetyNewsCollector
from collectors.quality import filter_articles_by_quality
from collectors.article_image import (
    fetch_representative_image, remove_repeated_site_images,
)

from storage.database import initialize_database, save_articles
from classification.rubric_classifier import classify_articles, save_classification_audit
from dedup.event_deduplicator import deduplicate_events, save_dedup_audit
from formatters.news_formatter import format_articles
from formatters.text_writer import save_text_report
from formatters.html_writer import save_html_report


def parse_date_range(text):
    """09.19 / 09.19~21 / 09.19~09.21 / 09.30~10.02 지원."""
    year = date.today().year
    text = text.strip().replace(" ", "")
    if "~" not in text:
        single = datetime.strptime(f"{year}.{text}", "%Y.%m.%d").date()
        return single, single

    start_text, end_text = text.split("~", 1)
    parts = start_text.split(".")
    if len(parts) != 2:
        raise ValueError("시작 날짜 형식이 올바르지 않습니다.")
    start_month, start_day = map(int, parts)

    if "." in end_text:
        end_parts = end_text.split(".")
        if len(end_parts) != 2:
            raise ValueError("종료 날짜 형식이 올바르지 않습니다.")
        end_month, end_day = map(int, end_parts)
    else:
        end_month, end_day = start_month, int(end_text)

    start_date = date(year, start_month, start_day)
    end_date = date(year, end_month, end_day)
    if end_date < start_date:
        raise ValueError("종료 날짜가 시작 날짜보다 빠릅니다.")
    return start_date, end_date


def ask_date_range():
    while True:
        print()
        user_input = input("수집 날짜를 입력하세요 (예: 09.19~21): ")
        try:
            return parse_date_range(user_input)
        except (ValueError, TypeError) as exc:
            print(f"\n입력 오류: {exc}")
            print("예시: 09.19 / 09.19~21 / 09.30~10.02")


def remove_unwanted_articles(articles):
    """한줄뉴스만 사전 제외. 주제 관련성은 Rubric에서 판단."""
    keep, removed = [], []
    for article in articles:
        title = article.title or ""
        if "한줄뉴스" in title or "한줄 뉴스" in title:
            removed.append(article)
        else:
            keep.append(article)
    return keep, removed


def print_collection_summary(source_counts):
    print("\n" + "=" * 60 + "\nSOURCE COLLECTION SUMMARY\n" + "=" * 60)
    for source_name, count in source_counts.items():
        print(f"{source_name}: {count}개")


def print_quality_summary(raw_count, unwanted_count, valid_articles, rejected):
    print("\n" + "=" * 60 + "\nDATA QUALITY SUMMARY\n" + "=" * 60)
    print(f"원본 수집 기사: {raw_count}개")
    print(f"명시적 제외: {unwanted_count}개")
    print(f"품질검사 제외: {len(rejected)}개")
    print(f"최종 유효 기사: {len(valid_articles)}개")
    if rejected:
        error_counter = Counter()
        source_counter = Counter()
        for item in rejected:
            source_counter[item.get("source") or "UNKNOWN"] += 1
            for error in item.get("errors", []):
                error_counter[error] += 1
        print("\n[품질 오류 유형]")
        for error, count in error_counter.most_common():
            print(f"  {error}: {count}")
        print("\n[품질 제외 소스]")
        for source, count in source_counter.most_common():
            print(f"  {source}: {count}")


def print_rubric_summary(classification_results):
    decisions = Counter(result["decision"] for result in classification_results)
    cached_count = sum(bool(result.get("cached")) for result in classification_results)
    print("\n" + "=" * 60 + "\nRUBRIC v2 SUMMARY\n" + "=" * 60)
    for name in ("KEEP", "DROP", "REVIEW"):
        print(f"{name}: {decisions[name]}")
    print(f"Checkpoint reused: {cached_count}")


def run_pipeline(start_date, end_date, progress_callback=None):
    """CLI와 웹 UI가 함께 호출한다. 콜백: (메시지, 진행률 0~1)."""

    def progress(message, portion):
        if progress_callback is not None:
            progress_callback(message, min(1.0, max(0.0, float(portion))))

    if end_date < start_date:
        raise ValueError("종료 날짜가 시작 날짜보다 빠릅니다.")
    print(f"\n수집 기간: {start_date} ~ {end_date}")
    progress("뉴스 수집 준비", 0.02)
    initialize_database()

    collectors = [
        DNewsCollector(), MTNCollector(), ConTimesCollector(),
        ConstructionEconomyCollector(), ConstructionIndustryCollector(),
        SafetyNewsCollector(),
    ]
    all_articles, source_counts = [], {}

    # 1. 수집
    for collector_index, collector in enumerate(collectors, 1):
        print(f"\n[{collector.source_name}] 기사 수집 시작")
        progress(f"{collector.source_name} 기사 수집 중", 0.04 + (collector_index - 1) * 0.075)
        try:
            articles = collector.collect_articles(
                start_date=start_date, end_date=end_date
            )
            all_articles.extend(articles)
            source_counts[collector.source_name] = len(articles)
            print(f"[{collector.source_name}] {len(articles)}개 수집")
        except Exception as exc:
            source_counts[collector.source_name] = 0
            print(f"[WARNING] {collector.source_name} 수집 실패\n{exc}")

    progress("기사 품질 검사", 0.50)
    raw_count = len(all_articles)
    print_collection_summary(source_counts)

    # 2~5. 사전 제외 / 품질 / 정렬
    all_articles, unwanted = remove_unwanted_articles(all_articles)
    for article in unwanted:
        print(f"  [명시적 제외] {article.source} | {article.title}")

    valid_articles, rejected = filter_articles_by_quality(all_articles, verbose=True)
    valid_articles.sort(key=lambda a: a.published_at or "", reverse=True)
    print_quality_summary(raw_count, len(unwanted), valid_articles, rejected)
    if not valid_articles:
        print("\n품질 검사를 통과한 기사가 없습니다.")
        return None

    # 6. 원본 DB 저장
    new_articles = save_articles(valid_articles)
    print(f"\nDB 저장 대상: {len(valid_articles)}개")
    print(f"신규 기사: {len(new_articles)}개")

    # 7~8. 전체 본문으로 분류 및 감사 기록
    progress("Rubric v2 기사 분류 중", 0.54)
    try:
        classification_results = classify_articles(valid_articles, verbose=True)
    except Exception as exc:
        print(f"\n[ERROR] Rubric v2 분류 실패\n{exc}")
        print("원문은 DB에 저장됐지만 미분류 기사는 최종 출력하지 않습니다.")
        return None

    classification_audit_path = save_classification_audit(
        results=classification_results,
        start_date=start_date,
        end_date=end_date,
    )
    print_rubric_summary(classification_results)
    print(f"\nRubric audit CSV:\n{classification_audit_path}")

    # 9. KEEP + REVIEW
    selected = [
        result["article"] for result in classification_results
        if result["decision"] in ("KEEP", "REVIEW")
    ]
    selected.sort(key=lambda a: a.published_at or "", reverse=True)
    print("\n" + "=" * 60 + "\nRUBRIC SELECTED ARTICLES\n" + "=" * 60)
    print(f"KEEP + REVIEW: {len(selected)}개")

    # 10~11. 동일 사건 중복 제거
    progress("동일 사건 중복 제거 중", 0.70)
    try:
        deduplicated, dedup_audit = deduplicate_events(selected, verbose=True)
    except Exception as exc:
        print(f"\n[ERROR] Event-level dedup 실패\n{exc}")
        print("원문과 Rubric 결과는 보존됐지만 최종 보고서는 만들지 않습니다.")
        return None

    deduplicated.sort(key=lambda a: a.published_at or "", reverse=True)
    dedup_audit_path = save_dedup_audit(
        articles=selected,
        audit=dedup_audit,
        start_date=start_date,
        end_date=end_date,
    )
    print(f"\nEvent dedup audit CSV:\n{dedup_audit_path}")

    # 12. 최종 기사에만 이미지 요청. 실패해도 기사 출력은 계속한다.
    progress("대표 이미지 수집 중", 0.80)
    image_assets = {}
    seen_image_digests = {}
    print("\n" + "=" * 60 + "\nARTICLE IMAGES\n" + "=" * 60)
    for idx, article in enumerate(deduplicated, 1):
        progress(
            f"대표 이미지 수집 {idx}/{len(deduplicated)}",
            0.80 + 0.14 * idx / max(len(deduplicated), 1),
        )
        article.image_url = None
        try:
            asset = fetch_representative_image(
                article,
                excluded_digests=seen_image_digests.setdefault(
                    article.source, set()
                ),
            )
            if asset:
                article.image_url = asset.url
                image_assets[article.url] = asset
                seen_image_digests[article.source].add(asset.digest)
                print(f"[{idx}/{len(deduplicated)}] IMAGE | {article.title}")
            else:
                print(f"[{idx}/{len(deduplicated)}] NO IMAGE | {article.title}")
        except Exception as exc:
            print(f"[{idx}/{len(deduplicated)}] IMAGE WARNING | {exc}")
    # 같은 언론사에서 서로 다른 기사에 반복된 공통 로고/기본 이미지는 제외.
    repeated_images = remove_repeated_site_images(deduplicated, image_assets)
    for article in repeated_images:
        print(f"[IMAGE FILTER] 공통 이미지 제외 | {article.title}")
    print(f"이미지 확보: {len(image_assets)}/{len(deduplicated)}개")

    # 13. 3개 문단으로 TXT/HTML 출력. 분류 입력은 전체 본문 그대로 유지.
    progress("TXT 및 HTML 보고서 생성", 0.96)
    txt_path = save_text_report(
        content=format_articles(deduplicated),
        start_date=start_date,
        end_date=end_date,
    )
    decisions_by_url = {
        result["article"].url: result["decision"]
        for result in classification_results
    }
    html_path = save_html_report(
        articles=deduplicated,
        image_assets=image_assets,
        decisions_by_url=decisions_by_url,
        start_date=start_date,
        end_date=end_date,
    )

    # 14. 요약
    counts = Counter(result["decision"] for result in classification_results)
    print("\n" + "=" * 60 + "\nPIPELINE RESULT\n" + "=" * 60)
    print(f"Raw collected: {raw_count}")
    print(f"Static excluded: {len(unwanted)}")
    print(f"Quality rejected: {len(rejected)}")
    print(f"Valid articles: {len(valid_articles)}")
    print(f"New DB articles: {len(new_articles)}")
    for decision in ("KEEP", "DROP", "REVIEW"):
        print(f"Rubric {decision}: {counts[decision]}")
    print(f"Rubric selected: {len(selected)}")
    print(f"Event duplicates removed: {len(selected) - len(deduplicated)}")
    print(f"Final articles: {len(deduplicated)}")
    print(f"Images: {len(image_assets)}")
    print(f"\nClassification audit: {classification_audit_path}")
    print(f"Event dedup audit: {dedup_audit_path}")
    print(f"Final TXT: {txt_path}")
    print(f"Final HTML: {html_path}")
    progress("보고서 생성 완료", 1.0)
    return {
        "raw": raw_count,
        "keep": counts["KEEP"],
        "drop": counts["DROP"],
        "review": counts["REVIEW"],
        "final": len(deduplicated),
        "images": len(image_assets),
        "txt_path": txt_path,
        "html_path": html_path,
        "articles": deduplicated,
        "image_assets": image_assets,
        "decisions_by_url": decisions_by_url,
        "source_counts": source_counts,
        "generated_at": datetime.now().astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        ),
    }


def main():
    start_date, end_date = ask_date_range()
    return run_pipeline(start_date, end_date)


if __name__ == "__main__":
    main()

