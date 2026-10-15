
"""Research Monitor 내부 사용자용 Streamlit 웹 화면.

실행: streamlit run app.py
설정: .env의 RESEARCH_MONITOR_PASSWORD, OPENAI_API_KEY
무료 클라우드 배포: Streamlit 앱 설정의 Secrets에도 같은 이름으로 등록.
"""

from contextlib import contextmanager
from datetime import date, timedelta
import hmac
import os
from pathlib import Path

import fcntl  # Codespaces / Streamlit Community Cloud: Linux 환경
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from st_copy import copy_button
from formatters.kpsea_formatter import kpsea_html
from formatters.source_links import public_article_url



load_dotenv()
st.set_page_config(page_title="Research Monitor", page_icon="📰", layout="centered")

MAX_PERIOD_DAYS = 7
MAX_LOGIN_ATTEMPTS = 5
LOCK_PATH = Path("outputs/.pipeline.lock")


def secret(name):
    """로컬 .env 우선, 배포 시 Streamlit Secrets도 지원한다."""
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:
        return ""


def require_password():
    stored = secret("RESEARCH_MONITOR_PASSWORD")
    if not stored:
        st.error("관리자 설정이 필요합니다: RESEARCH_MONITOR_PASSWORD가 없습니다.")
        st.stop()

    if st.session_state.get("authenticated", False):
        return

    st.title("Research Monitor")
    st.write("내부 사용자 전용 뉴스 수집 시스템")
    if st.session_state.get("failed_logins", 0) >= MAX_LOGIN_ATTEMPTS:
        st.error("로그인 시도가 너무 많습니다. 브라우저 세션을 종료하고 관리자에게 문의하세요.")
        st.stop()

    with st.form("login_form", clear_on_submit=True):
        password = st.text_input("공통 비밀번호", type="password")
        submitted = st.form_submit_button("접속", type="primary", use_container_width=True)
    if submitted:
        if hmac.compare_digest(password, stored):
            st.session_state.authenticated = True
            st.session_state.failed_logins = 0
            st.rerun()
        else:
            st.session_state.failed_logins = st.session_state.get("failed_logins", 0) + 1
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


def default_dates():
    """당일 미발행 기사를 피하도록 어제까지의 기간을 기본값으로 둔다.

    월요일: 금~일, 일요일: 금~토, 그 외: 전일을 제안한다.
    """
    today = date.today()
    last = today - timedelta(days=1)
    first = last
    if today.weekday() == 0:  # 월요일
        first = last - timedelta(days=2)
    elif today.weekday() == 6:  # 일요일
        first = last - timedelta(days=1)
    return first, last


@contextmanager
def single_run():
    """같은 Linux 서버에서 수집 작업의 중복 실행을 방지한다."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("다른 사용자가 이미 뉴스를 수집하고 있습니다.") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_from_ui(start, end):
    if start > end:
        st.error("종료일이 시작일보다 빠릅니다.")
        return
    if end > date.today():
        st.error("미래 날짜는 수집할 수 없습니다.")
        return
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        st.error(f"한 번에 최대 {MAX_PERIOD_DAYS}일까지만 수집할 수 있습니다.")
        return

    key = secret("OPENAI_API_KEY")
    if key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = key
    if not os.environ.get("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY 설정이 필요합니다.")
        return

    st.session_state.pop("last_report", None)
    bar = st.progress(0, text="뉴스 수집 준비 중")
    status = st.empty()

    def on_progress(message, ratio):
        bar.progress(min(100, round(ratio * 100)), text=message)
        status.caption(message)

    try:
        with single_run():
            # 로그인/날짜 위젯을 제외하고 실제 실행할 때만 엔진을 가져온다.
            from main import run_pipeline
            report = run_pipeline(start, end, progress_callback=on_progress)
            if report is None:
                bar.empty()
                st.error("수집 결과가 없거나 분류/중복 제거에 실패했습니다. 서버 실행 로그를 확인해 주세요.")
                return
            st.session_state.last_report = report
            st.success("보고서 생성이 완료됐습니다.")
    except Exception as exc:
        bar.empty()
        st.error(f"수집 작업을 완료하지 못했습니다: {type(exc).__name__}: {exc}")


def show_report(report):
    st.caption(
        f"현재 세션에서 생성한 보고서 · "
        f"{report.get('generated_at', '생성 시각 정보 없음')}"
    )
    a, b, c = st.columns(3)
    a.metric("전체 수집", report["raw"])
    b.metric("최종 기사", report["final"])
    c.metric("대표 이미지", report["images"])
    st.caption(f"KEEP {report['keep']}건 · REVIEW {report['review']}건 · DROP {report['drop']}건 (중복 제거 전 분류 기준)")

    left, right = st.columns(2)
    for col, filename_key, label, mime in (
        (left, "html_path", "사진 포함 HTML", "text/html"),
        (right, "txt_path", "3문단 TXT", "text/plain"),
    ):
        path = Path(report[filename_key])
        if path.is_file():
            col.download_button(
                label=label + " 다운로드", data=path.read_bytes(),
                file_name=path.name, mime=mime, use_container_width=True,
            )

    st.subheader("기사별 확인")
    st.caption("기사별 복사 아이콘으로 KPSEA용 제목과 HTML 본문을 복사할 수 있습니다.")
    for number, article in enumerate(report["articles"], 1):
        decision = report["decisions_by_url"].get(article.url, "KEEP")
        with st.expander(f"{number:02d}. {article.title} · {decision}"):
            st.markdown(f"**{article.title}**")
            asset = report["image_assets"].get(article.url)
            st.markdown("**KPSEA 게시용 복사**")
            title_col, html_col = st.columns(2)

            html_content = kpsea_html(
                article,
                image_url=asset.url if asset else None,
            )

            st.markdown("**본문 미리보기**")
            st.markdown(html_content, unsafe_allow_html=True)

            with title_col:
                st.caption("① 제목 복사")
                copy_button(
                    str(article.title or ""),
                    tooltip="제목 복사",
                    copied_label="복사 완료",
                    key=f"kpsea_title_{number}",
                )

            with html_col:
                st.caption("② HTML 본문 복사")
                copy_button(
                    html_content,
                    tooltip="KPSEA용 HTML 복사",
                    copied_label="복사 완료",
                    key=f"kpsea_html_{number}",
                )

            with st.popover("복사가 안 될 때 · 수동 복사"):
                st.caption("각 코드 영역의 복사 아이콘을 사용하세요.")
                st.code(str(article.title or ""), language=None)
                st.code(
                    html_content,
                    language="html",
                    wrap_lines=True,
                    height=180,
                )

            st.caption("KPSEA 글쓰기의 html 옵션을 켜고 붙여넣으세요.")

            st.write(f"원문출처 : [{article.source}][{article.published_at or '날짜 미상'}] {public_article_url(article.url)}")
            st.link_button("원문 기사 열기", public_article_url(article.url))



def show_saved_report(start, end):
    """저장된 보고서를 API 호출 없이 다시 열기."""
    stem = f"news_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}"
    html_path = Path("outputs") / f"{stem}.html"
    txt_path = Path("outputs") / f"{stem}.txt"

    with st.expander("기존 보고서 보기 · API 호출 없음"):
        if not html_path.is_file() or not txt_path.is_file():
            st.info("선택한 기간에 저장된 보고서가 없습니다.")
            return

        if st.button("저장된 보고서 열기"):
            st.session_state["saved_report_period"] = stem

        if st.session_state.get("saved_report_period") != stem:
            return

        left, right = st.columns(2)
        left.download_button(
            "HTML 다운로드",
            data=html_path.read_bytes(),
            file_name=html_path.name,
            mime="text/html",
        )
        right.download_button(
            "TXT 다운로드",
            data=txt_path.read_bytes(),
            file_name=txt_path.name,
            mime="text/plain",
        )

        st.subheader("저장된 보고서 미리보기 · 기존 생성 파일")
        st.caption("저장 당시 생성된 HTML입니다. 현재 코드의 기사별 미리보기와 다를 수 있습니다.")
        components.html(
            html_path.read_text(encoding="utf-8"),
            height=900,
            scrolling=True,
        )

def main():
    require_password()
    st.title("📰 Research Monitor")
    st.caption("6개 언론사 · Rubric v2 분류 · 동일 사건 제거 · 3문단 발췌")

    with st.sidebar:
        st.write("내부 사용자용")
        if st.button("로그아웃"):
            st.session_state.authenticated = False
            st.session_state.pop("last_report", None)
            st.rerun()

    default_start, default_end = default_dates()
    with st.form("collection_form"):
        start_col, end_col = st.columns(2)
        start = start_col.date_input("시작일", value=default_start)
        end = end_col.date_input("종료일", value=default_end)
        submitted = st.form_submit_button(
            "뉴스 수집 및 보고서 생성", type="primary", use_container_width=True,
        )
    if submitted:
        run_from_ui(start, end)

    show_saved_report(start, end)

    report = st.session_state.get("last_report")
    if report:
        show_report(report)


if __name__ == "__main__":
    main()
