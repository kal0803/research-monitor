from dataclasses import fields
import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from bs4 import BeautifulSoup

from collectors.article_subheading import (
    extract_article_prebody_subheadings,
    extract_dnews_prebody_subheadings,
    extract_mtn_prebody_subheadings,
    extract_page_subheadings,
)

# Pillow is only used when decoding an actual image payload. The image stage
# below is isolated with a fake page and no image candidates.
if "PIL" not in sys.modules:
    pillow = ModuleType("PIL")
    pillow.Image = ModuleType("PIL.Image")
    sys.modules["PIL"] = pillow
    sys.modules["PIL.Image"] = pillow.Image

from collectors.article_image import fetch_representative_image
from collectors.constimes import ConTimesCollector
from collectors.generic import GenericNewsCollector
from collectors.quality import filter_articles_by_quality
from formatters.html_writer import render_article
from formatters.kpsea_formatter import kpsea_html
from formatters.news_formatter import (
    article_subheadings,
    excerpt_paragraphs,
    format_article,
)
from models import Article


CONSTIMES_HTML = """
<meta property="og:title" content="AI가 교량 안전성 10분 만에 진단…건설연, 평가시스템 개발 - 건설타임즈">
<title>AI가 교량 안전성 10분 만에 진단…건설연, 평가시스템 개발 - 건설타임즈</title>
<div class="article-body">
  <h2 class="subheading">계측데이터·시뮬레이션 융합…10분 내 안전성 분석<br>
  현수교 실증서 기존 프로그램 대비 정확도 90% 이상</h2>
  <p>한국건설기술연구원이 교량 안전성 분석 시스템을 개발했다.</p>
</div>
"""

DNEWS_HTML = """
<h1>“LH, 비위업체 제재 완화 고무줄” 질타 [2026 국감]</h1>
<span>기사입력 2026-10-13 18:38:01</span>
<div class="summary">
  <p>윤종군 의원, 불공정 사례 추궁</p>
  <p>근거없이 공모제한 2년→1년 단축</p>
  <p>제재 끝나자 감점 없이 4억대 수주</p>
  <p>업체선정ㆍ제재심의 인력풀도 도마위</p>
  <p>위원회 분리ㆍ사전접촉 감점 등 주문</p>
</div>
<p>[대한경제=전동훈 기자] LH가 업체 제재 기준을 변경했다.</p>
"""

SAFETYNEWS_HTML = """
<div class="article-view-header">
  <h1>안전신문 기사 제목</h1>
  <p class="sub-title">현장 안전관리 체계 개선 방안 마련</p>
</div>
<aside><a>관련기사 제목은 부제목에 포함하지 않는다</a></aside>
<div id="article-view-content-div"><p>기사 본문 첫 문장입니다.</p></div>
"""

MTN_HTML = """
<p>다음뉴스 채널구독</p>
<h1>위례과천선, 원안대로 추진 전망… 국토부 “변경 가능성 있어”</h1>
<p>국토부, 관계기관 의견 반영한 전략환경영향평가서 초안 공개</p>
<p>3자 공고, 올 연말 실시 목표… “확정 노선은 우협과 협의할 것”</p>
<p>최남영 기자</p><p>2026-10-14 15:00:01</p>
<p><a>구글에서 머니투데이방송 추가</a></p>
<table>
  <tr><td><img src="https://news.mtn.co.kr/route.png"></td></tr>
  <tr><td>위례과천선 노선 예정도. /제공=국토교통부</td></tr>
</table>
<p>수도권 동남권 주요 교통 인프라로 꼽히고 있는 위례과천선 사업이다.</p>
"""


def make_article(source="건설타임즈", **overrides):
    values = {
        "source": source,
        "title": "검증 기사 제목",
        "reporter": "홍길동",
        "published_at": "2026-10-15 10:00:00",
        "image_caption": "교량 전경 /제공=건설연",
        "paragraphs": [
            "첫 번째 본문 문단에는 기사 내용이 충분히 포함되어 있습니다.",
            "두 번째 본문 문단에는 추가 설명과 수치 정보가 들어 있습니다.",
            "세 번째 본문 문단은 사업의 향후 계획을 설명합니다.",
        ],
        "url": "https://example.com/article/1",
    }
    values.update(overrides)
    return Article(**values)


def test_article_subheadings_is_a_compatible_dataclass_field():
    article = make_article()
    assert "subheadings" in {item.name for item in fields(Article)}
    assert article.subheadings == []
    assert Article("src", "title", None, None, None, [], "https://e.test").subheadings == []


def test_constimes_br_split_subheadings_and_title_suffix():
    soup = BeautifulSoup(CONSTIMES_HTML, "html.parser")
    article = make_article(
        title="AI가 교량 안전성 10분 만에 진단…건설연, 평가시스템 개발"
    )
    collector = ConTimesCollector.__new__(ConTimesCollector)
    assert collector.extract_title(soup) == article.title
    assert extract_article_prebody_subheadings(soup, article) == [
        "계측데이터·시뮬레이션 융합…10분 내 안전성 분석",
        "현수교 실증서 기존 프로그램 대비 정확도 90% 이상",
    ]


def test_dnews_mobile_prebody_five_subheadings():
    soup = BeautifulSoup(DNEWS_HTML, "html.parser")
    article = make_article(
        source="대한경제",
        title="“LH, 비위업체 제재 완화 고무줄” 질타 [2026 국감]",
        image_caption=None,
    )
    assert extract_dnews_prebody_subheadings(soup, article) == [
        "윤종군 의원, 불공정 사례 추궁",
        "근거없이 공모제한 2년→1년 단축",
        "제재 끝나자 감점 없이 4억대 수주",
        "업체선정ㆍ제재심의 인력풀도 도마위",
        "위원회 분리ㆍ사전접촉 감점 등 주문",
    ]


def test_safetynews_explicit_subtitle_and_related_title_rejection():
    soup = BeautifulSoup(SAFETYNEWS_HTML, "html.parser")
    article = make_article(source="안전신문", title="안전신문 기사 제목")
    assert extract_article_prebody_subheadings(soup, article) == [
        "현장 안전관리 체계 개선 방안 마련"
    ]


def test_generic_collector_saves_subheadings_before_image_stage(monkeypatch):
    soup = BeautifulSoup(
        "<div class='article-view-header'>"
        "<h1>건설산업신문 기사 제목</h1>"
        "<h2 class='sub-title'>공공 공사 안전관리 절차 개선</h2></div>"
        "<div id='article-view-content-div'><p>본문 첫 문단입니다.</p></div>",
        "html.parser",
    )
    collector = GenericNewsCollector.__new__(GenericNewsCollector)
    collector.source_name = "건설산업신문"
    monkeypatch.setattr(collector, "get_soup", lambda url: soup)
    monkeypatch.setattr(collector, "extract_published_at", lambda text: "2026-10-15 10:00:00")
    monkeypatch.setattr(
        collector,
        "choose_published_at",
        lambda url, detail_published_at: detail_published_at,
    )
    monkeypatch.setattr(collector, "extract_reporter", lambda text: "홍길동")
    monkeypatch.setattr(collector, "extract_caption", lambda page: None)
    monkeypatch.setattr(collector, "extract_paragraphs", lambda page: ["본문 첫 문단입니다."])

    article = collector.collect_article("https://example.com/story")
    assert article.subheadings == ["공공 공사 안전관리 절차 개선"]


def test_mtn_keeps_two_subtitles_photo_caption_and_rejects_subscription_ui():
    soup = BeautifulSoup(MTN_HTML, "html.parser")
    article = make_article(
        source="MTN뉴스",
        title="위례과천선, 원안대로 추진 전망… 국토부 “변경 가능성 있어”",
        image_caption=None,
    )
    assert extract_mtn_prebody_subheadings(soup, article) == [
        "국토부, 관계기관 의견 반영한 전략환경영향평가서 초안 공개",
        "3자 공고, 올 연말 실시 목표… “확정 노선은 우협과 협의할 것”",
    ]
    from collectors.mtn import extract_mtn_table_caption

    assert extract_mtn_table_caption(soup) == "위례과천선 노선 예정도. /제공=국토교통부"
    assert "다음뉴스 채널구독" not in extract_mtn_prebody_subheadings(soup, article)


def test_body_sentence_is_not_invented_as_subheading():
    article = make_article(
        subheadings=[],
        paragraphs=["현장 점검을 통해 안전성을 확인했다."],
    )
    assert article_subheadings(article) == []

    soup = BeautifulSoup(
        "<div class='article-view-header'><h1>제목</h1></div>"
        "<div id='article-view-content-div'><p>짧은 첫 문장입니다.</p>"
        "<h2>본문 중간의 제목</h2></div>",
        "html.parser",
    )
    article.source = "안전신문"
    article.title = "제목"
    article.paragraphs = ["짧은 첫 문장입니다."]
    assert extract_article_prebody_subheadings(soup, article) == []
    assert excerpt_paragraphs(article) == ["짧은 첫 문장입니다."]


def test_explicit_subheadings_are_deduplicated_and_ui_text_is_filtered():
    article = make_article(
        subheadings=[
            "첫 번째 부제목",
            "첫 번째 부제목",
            "공유하기",
            "관련기사 추천 제목",
            "홍길동 기자",
            "광고 구독 안내",
        ]
    )
    assert article_subheadings(article) == ["첫 번째 부제목"]


def test_all_six_sources_return_empty_when_page_has_no_explicit_subtitle():
    soup = BeautifulSoup(
        "<h1>기사 제목</h1><p>이것은 실제 기사 본문 첫 문장입니다.</p>",
        "html.parser",
    )
    for source in (
        "대한경제",
        "MTN뉴스",
        "건설타임즈",
        "건설이코노미뉴스",
        "건설산업신문",
        "안전신문",
    ):
        article = make_article(source=source, title="기사 제목")
        assert extract_page_subheadings(soup, article) == []


def test_kpsea_and_download_html_order_and_adjacent_image_caption():
    article = make_article(
        subheadings=["첫 번째 부제목", "두 번째 부제목"]
    )
    asset = SimpleNamespace(
        url="https://example.com/photo.jpg",
        data_uri="data:image/jpeg;base64,AAAA",
    )

    kpsea = kpsea_html(article, image_url=asset.url)
    downloaded = render_article(article, asset=asset)
    for markup in (kpsea, downloaded):
        subtitle_2 = markup.index("두 번째 부제목")
        image = markup.index("<img")
        caption = markup.index("교량 전경")
        first_body = markup.index("첫 번째 본문 문단")
        second_body = markup.index("두 번째 본문 문단")
        third_body = markup.index("세 번째 본문 문단")
        source_link = markup.index("원문보기[건설타임즈]") if "원문보기[건설타임즈]" in markup else markup.index("원문출처")
        assert subtitle_2 < image < caption < first_body < second_body < third_body < source_link
        assert "<strong>교량 전경" not in markup

    kpsea_soup = BeautifulSoup(kpsea, "html.parser")
    image_tag = kpsea_soup.find("img")
    assert image_tag.next_sibling == "교량 전경 /제공=건설연"
    assert "<br><br>교량 전경" not in kpsea


def test_missing_image_keeps_caption_without_fake_image():
    article = make_article(subheadings=["부제목"], image_url=None)
    kpsea = kpsea_html(article, image_url=None)
    downloaded = render_article(article, asset=None)
    assert "<img" not in kpsea
    assert "<img" not in downloaded
    assert "교량 전경 /제공=건설연" in kpsea
    assert "교량 전경 /제공=건설연" in downloaded


def test_html_text_and_links_are_escaped_and_unsafe_urls_are_omitted():
    article = make_article(
        title='<script>alert("title")</script>',
        subheadings=['<img src=x onerror="run()">'],
        image_caption='<script>alert("caption")</script>',
        url="javascript:alert(1)",
    )
    kpsea = kpsea_html(article, image_url="javascript:alert(2)")
    downloaded = render_article(article)
    for markup in (kpsea, downloaded):
        assert "<script>" not in markup
        assert "onerror=\"run()\"" not in markup
        assert "javascript:" not in markup
        assert "&lt;script&gt;" in markup


def test_streamlit_preview_copy_and_manual_copy_share_kpsea_html_value():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    show_report = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "show_report"
    )
    html_assignment = next(
        node for node in ast.walk(show_report)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "html_content"
            for target in node.targets
        )
    )
    assert isinstance(html_assignment.value, ast.Call)
    assert isinstance(html_assignment.value.func, ast.Name)
    assert html_assignment.value.func.id == "kpsea_html"

    html_uses = {
        node.func.attr
        for node in ast.walk(show_report)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and any(
            isinstance(argument, ast.Name) and argument.id == "html_content"
            for argument in node.args
        )
    }
    assert {"markdown", "code"}.issubset(html_uses)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "copy_button"
        and any(
            isinstance(argument, ast.Name) and argument.id == "html_content"
            for argument in node.args
        )
        for node in ast.walk(show_report)
    )


def test_text_output_keeps_subtitle_image_caption_and_body_order():
    article = make_article(
        subheadings=["부제목"],
        image_url="https://example.com/photo.jpg",
    )
    output = format_article(article)
    assert output.index("부제목") < output.index("이미지 보기:")
    assert output.index("이미지 보기:") < output.index("교량 전경")
    assert output.index("교량 전경") < output.index("첫 번째 본문 문단")


def test_quality_classification_dedup_and_image_stage_preserve_subheadings(monkeypatch):
    import classification.rubric_classifier as classifier
    import collectors.article_image as image_module
    from dedup.event_deduplicator import deduplicate_events

    article = make_article(
        subheadings=["데이터 기반 안전성 분석"],
    )
    valid, rejected = filter_articles_by_quality([article], verbose=False)
    assert not rejected
    assert valid == [article]
    assert valid[0].subheadings == ["데이터 기반 안전성 분석"]

    monkeypatch.setattr(classifier, "load_rubric", lambda: "test rubric")
    rubric_hash = classifier.make_rubric_hash("test rubric")
    classifier_hash = classifier.make_classifier_hash()
    article_hash = classifier.make_article_hash(article)
    cache_key = classifier.make_cache_key(article_hash, rubric_hash, classifier_hash)
    monkeypatch.setattr(
        classifier,
        "load_checkpoint",
        lambda **kwargs: {
            cache_key: {
                "decision": "KEEP",
                "matched_rules": [],
                "reason": "cached test decision",
                "needs_more_information": False,
            }
        },
    )
    classified = classifier.classify_articles(valid, verbose=False)
    selected = [item["article"] for item in classified]
    assert selected[0] is article
    assert selected[0].subheadings == ["데이터 기반 안전성 분석"]

    deduplicated, _ = deduplicate_events(selected, verbose=False)
    assert deduplicated[0] is article

    class FakePage:
        url = article.url
        content = b"<html><body>article</body></html>"

        def raise_for_status(self):
            return None

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            return FakePage()

    monkeypatch.setattr(image_module.requests, "Session", FakeSession)
    monkeypatch.setattr(image_module, "candidate_urls", lambda *args: [])
    assert fetch_representative_image(article) is None
    assert article.subheadings == ["데이터 기반 안전성 분석"]
