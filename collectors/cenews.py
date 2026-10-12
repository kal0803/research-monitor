import re
import html

from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from collectors.browser import launch_chromium
from collectors.generic import GenericNewsCollector


class CENewsCollector(GenericNewsCollector):

    source_key = "cenews"

    max_pages = 200
    old_page_streak_limit = 2
    candidate_margin_days = 3

    # ========================================================
    # DATE
    # ========================================================

    def parse_ajax_date(self, value):
        """
        AJAX, 목록, 상세페이지의 날짜 문자열을
        date로 변환한다.
        """

        if not value:
            return None

        text = str(value).strip()

        match = re.search(
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
            text,
        )

        if not match:
            return None

        try:
            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            return None

    def parse_listing_date(self, text):
        """
        첫 HTML 목록의 '10-07 10:21' 형식을 해석한다.
        기존 연도 추정 로직을 유지한다.
        """

        text = self.clean_text(text)

        full_date = self.parse_ajax_date(text)

        if full_date:
            return full_date

        match = re.search(
            r"(?<!\d)(\d{1,2})-(\d{1,2})"
            r"\s+\d{1,2}:\d{2}",
            text,
        )

        if not match:
            return None

        month = int(match.group(1))
        day = int(match.group(2))

        reference = date.today() + timedelta(days=1)

        for year in range(
            reference.year,
            reference.year - 5,
            -1,
        ):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue

            if candidate <= reference:
                return candidate

        return None

    # ========================================================
    # DETAIL-PAGE DATE
    # ========================================================

    def extract_detail_article_date(self, html_text):
        """
        상세페이지 화면의 기사 '승인' 날짜를 읽는다.

        사이트 공통 상단의 '발행일'이나
        별도 '수정일'은 사용하지 않는다.
        """

        soup = BeautifulSoup(
            html_text,
            "html.parser",
        )

        # 건설이코노미뉴스의 확인된 기사 날짜 표기:
        # 승인 2026.06.20 14:56
        #
        # 입력/등록은 템플릿 변경 시 보조 레이블로 사용한다.

        date_pattern = re.compile(
            r"(?:^|[\s|·])"
            r"(?:승인|기사\s*입력|입력|기사\s*등록|등록)"
            r"\s*[:：]?\s*"
            r"(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
            r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
        )

        # 기사 상단의 날짜 컨테이너를 먼저 확인한다.

        selectors = (
            ".article-view-header",
            ".article-head",
            ".article-header",
            ".article-info",
            ".article-view-info",
            "ul.infomation",
            "ul.information",
            "#article-view",
            "article",
        )

        for selector in selectors:

            for root in soup.select(selector):

                candidates = root.select(
                    "li, span, time, small, p, div"
                )

                candidates.insert(0, root)

                for element in candidates:

                    label_text = element.get_text(
                        " ",
                        strip=True,
                    )

                    if (
                        not label_text
                        or len(label_text) > 180
                    ):
                        continue

                    match = date_pattern.search(
                        label_text
                    )

                    if match:

                        parsed = self.parse_ajax_date(
                            match.group(1)
                        )

                        if parsed:
                            return parsed

        # 페이지 구조 변경에 대비한 fallback.
        # '발행일'은 사이트 공통 정보와 혼동될 수 있어
        # 승인/입력/등록 레이블만 허용한다.

        for element in soup.select(
            "li, span, time, small"
        ):

            label_text = element.get_text(
                " ",
                strip=True,
            )

            if (
                not label_text
                or len(label_text) > 180
            ):
                continue

            match = date_pattern.search(
                label_text
            )

            if match:

                parsed = self.parse_ajax_date(
                    match.group(1)
                )

                if parsed:
                    return parsed

        # 승인 텍스트와 날짜가 별도 태그에
        # 들어간 경우 부모 요소에서도 확인한다.

        for string in soup.find_all(
            string=re.compile(
                r"승인|기사\s*입력|기사\s*등록"
            )
        ):

            parent = string.parent

            for element in (
                parent,
                getattr(parent, "parent", None),
            ):

                if element is None:
                    continue

                label_text = element.get_text(
                    " ",
                    strip=True,
                )

                if (
                    not label_text
                    or len(label_text) > 180
                ):
                    continue

                match = date_pattern.search(
                    label_text
                )

                if match:

                    parsed = self.parse_ajax_date(
                        match.group(1)
                    )

                    if parsed:
                        return parsed

        return None

    def fetch_detail_article_date(
        self,
        detail_page,
        url,
    ):
        """
        별도 브라우저 탭으로 상세페이지를 열어
        목록의 더보기 상태를 보존한다.
        """

        response = detail_page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        if (
            response is None
            or response.status >= 400
        ):

            status = (
                response.status
                if response is not None
                else "응답 없음"
            )

            raise RuntimeError(
                f"상세페이지 HTTP 오류: {status}"
            )

        return self.extract_detail_article_date(
            detail_page.content()
        )

    # ========================================================
    # FIRST PAGE
    # ========================================================

    def extract_first_page_entries(self, html_text):
        """
        기존 HTML 첫 페이지의 실제 기사 목록만 추출한다.

        ul.altlist-text
          > li.altlist-text-item
        """

        soup = BeautifulSoup(
            html_text,
            "html.parser",
        )

        items = soup.select(
            "ul.altlist-text > li.altlist-text-item"
        )

        entries = []
        seen = set()

        for item in items:

            link = item.select_one(
                "h2.altlist-subject a[href]"
            )

            if link is None:
                continue

            href = link.get("href", "")

            if "articleView.html?idxno=" not in href:
                continue

            url = urljoin(
                self.section_url,
                href,
            )

            if url in seen:
                continue

            title = self.clean_text(
                link.get_text(" ", strip=True)
            )

            listing_text = self.clean_text(
                item.get_text(" ", strip=True)
            )

            published_date = self.parse_listing_date(
                listing_text
            )

            entries.append({
                "url": url,
                "title": title,
                "date": published_date,
            })

            seen.add(url)

        return entries

    # ========================================================
    # AJAX RESPONSE
    # ========================================================

    def extract_ajax_entries(self, payload):
        """
        기존 AJAX의 data 목록을 읽는다.

        토목·구조 관련 기사가 여러 섹션에 있어
        section_code로 사전 제외하지 않는다.
        """

        if not isinstance(payload, dict):
            raise ValueError(
                "건설이코노미뉴스 AJAX 최상위 구조가 "
                "dict가 아닙니다."
            )

        raw_items = payload.get("data")

        if not isinstance(raw_items, list):
            raise ValueError(
                "건설이코노미뉴스 AJAX data가 "
                "list 형식이 아닙니다."
            )

        entries = []
        seen = set()

        for item in raw_items:

            if not isinstance(item, dict):
                continue

            idxno = str(
                item.get("idxno") or ""
            ).strip()

            if not re.fullmatch(
                r"\d+",
                idxno,
            ):
                continue

            url = urljoin(
                self.base_url,
                f"news/articleView.html?idxno={idxno}",
            )

            if url in seen:
                continue

            title = html.unescape(
                str(item.get("title") or "")
            )

            title = self.clean_text(title)

            published_date = None

            for key in (
                "reg_date",
                "approve_date",
                "pub_date",
                "viewDateTime",
            ):

                published_date = self.parse_ajax_date(
                    item.get(key)
                )

                if published_date:
                    break

            entries.append({
                "url": url,
                "title": title,
                "date": published_date,
            })

            seen.add(url)

        return entries

    # ========================================================
    # PAGE PROCESSING
    # ========================================================

    def process_page_entries(
        self,
        entries,
        page_number,
        start_date,
        end_date,
        urls,
        seen_urls,
        detail_page,
    ):
        """
        목록 날짜로 상세페이지 방문 후보를 찾고,
        실제 상세페이지 승인 날짜로 최종 선별한다.

        반환:
        페이지 최신 목록 날짜,
        페이지 signature,
        목록 날짜 미확인 기사 수
        """

        if not entries:
            return None, None, 0

        signature = tuple(
            entry["url"]
            for entry in entries
        )

        dated_entries = [
            entry
            for entry in entries
            if entry["date"] is not None
        ]

        dates = [
            entry["date"]
            for entry in dated_entries
        ]

        newest = (
            max(dates)
            if dates
            else None
        )

        oldest = (
            min(dates)
            if dates
            else None
        )

        undated_count = (
            len(entries) - len(dated_entries)
        )

        candidate_start = (
            start_date
            - timedelta(days=self.candidate_margin_days)
        )

        candidate_end = (
            end_date
            + timedelta(days=self.candidate_margin_days)
        )

        matched = 0
        detail_checked = 0
        outside_margin = 0

        for entry in entries:

            url = entry["url"]

            if url in seen_urls:
                continue

            listing_date = entry["date"]

            # 목록 날짜는 최종 판정에 사용하지 않는다.
            # 다만 무제한 상세 방문을 피하기 위해
            # 전후 3일 범위의 후보만 확인한다.
            #
            # 목록 날짜가 없다면 후보에 유지한다.

            if (
                listing_date is not None
                and not (
                    candidate_start
                    <= listing_date
                    <= candidate_end
                )
            ):

                outside_margin += 1
                continue

            seen_urls.add(url)

            try:

                detail_date = (
                    self.fetch_detail_article_date(
                        detail_page,
                        url,
                    )
                )

                detail_checked += 1

            except Exception as exc:

                print(
                    "[WARNING] "
                    "건설이코노미뉴스 "
                    "상세페이지 확인 실패: "
                    f"{url} | {exc}"
                )

                continue

            if detail_date is None:

                print(
                    "[WARNING] "
                    "건설이코노미뉴스 "
                    "승인 날짜 확인 불가: "
                    f"{url} | 목록={listing_date}"
                )

                continue

            if (
                listing_date is not None
                and listing_date != detail_date
            ):

                print(
                    "[건설이코노미뉴스][DATE] "
                    f"목록={listing_date} | "
                    f"승인={detail_date} | "
                    f"{entry['title']}"
                )

            # 최종 수집 여부는 상세페이지 승인일 기준.

            if (
                start_date
                <= detail_date
                <= end_date
            ):

                urls.append(url)
                matched += 1

        page_range = (
            f"{oldest} ~ {newest}"
            if dates
            else "목록 날짜 확인 불가"
        )

        print(
            f"[건설이코노미뉴스] page {page_number}: "
            f"{page_range} | "
            f"기사 {len(entries)}개 | "
            f"상세 확인 {detail_checked}개 | "
            f"목록 날짜 없음 {undated_count}개 | "
            f"여유 범위 밖 {outside_margin}개 | "
            f"기간 일치 {matched}개"
        )

        return newest, signature, undated_count

    # ========================================================
    # HISTORICAL PAGINATION
    # ========================================================

    def collect_article_urls(
        self,
        start_date=None,
        end_date=None,
    ):
        """
        건설이코노미뉴스 과거 기사 수집.

        1. 첫 목록 페이지 HTML 추출
        2. 더보기 버튼 클릭
        3. AJAX JSON 응답 수집
        4. 목록 날짜로 상세 방문 후보 선별
        5. 상세페이지 승인 날짜로 최종 판정
        6. 후보 시작일보다 과거인 페이지가
           2개 연속 확인되면 종료

        기존 Playwright 더보기 방식은 유지한다.
        """

        if start_date is None or end_date is None:
            raise ValueError(
                "start_date와 end_date가 필요합니다."
            )

        if start_date > end_date:
            raise ValueError(
                "시작일이 종료일보다 늦습니다."
            )

        candidate_start = (
            start_date
            - timedelta(days=self.candidate_margin_days)
        )

        candidate_end = (
            end_date
            + timedelta(days=self.candidate_margin_days)
        )

        print(
            "[건설이코노미뉴스] "
            f"후보 기간={candidate_start} ~ "
            f"{candidate_end} | "
            "최종 기준=상세페이지 승인 날짜"
        )

        urls = []
        seen_urls = set()

        previous_signature = None
        old_page_streak = 0

        with sync_playwright() as p:

            browser = launch_chromium(p)

            try:

                # --------------------------------------------
                # BROWSER CONTEXT
                # --------------------------------------------
                # browser.new_page() 대신 명시적으로
                # Context를 생성한다.
                #
                # 목록과 상세페이지 탭은 같은 Context에
                # 속하므로 쿠키와 세션을 공유한다.
                # --------------------------------------------

                context = browser.new_context(
                    locale="ko-KR",
                )

                page = context.new_page()

                page.goto(
                    self.section_url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                page.locator(
                    "ul.altlist-text > li.altlist-text-item"
                ).first.wait_for(
                    state="attached",
                    timeout=20000,
                )

                # 기존 목록 브라우저 탭을 보존하고
                # 별도 탭에서 상세페이지 날짜를 확인한다.

                detail_page = context.new_page()

                # --------------------------------------------
                # FIRST HTML PAGE
                # --------------------------------------------

                first_entries = (
                    self.extract_first_page_entries(
                        page.content()
                    )
                )

                if not first_entries:
                    raise RuntimeError(
                        "건설이코노미뉴스 첫 페이지의 "
                        "기사 목록을 찾지 못했습니다."
                    )

                (
                    newest,
                    previous_signature,
                    undated_count,
                ) = self.process_page_entries(
                    entries=first_entries,
                    page_number=1,
                    start_date=start_date,
                    end_date=end_date,
                    urls=urls,
                    seen_urls=seen_urls,
                    detail_page=detail_page,
                )

                if (
                    newest is not None
                    and newest < candidate_start
                    and undated_count == 0
                ):
                    old_page_streak = 1

                # --------------------------------------------
                # AJAX PAGE 2, 3, ...
                # --------------------------------------------

                for page_number in range(
                    2,
                    self.max_pages + 1,
                ):

                    if (
                        old_page_streak
                        >= self.old_page_streak_limit
                    ):

                        print(
                            "[건설이코노미뉴스] "
                            "후보 시작일보다 과거인 "
                            f"페이지 "
                            f"{self.old_page_streak_limit}개 "
                            "연속 확인 → 탐색 종료"
                        )

                        break

                    button = page.locator(
                        "button.list-btn-more, "
                        "a.list-btn-more"
                    ).first

                    if (
                        button.count() == 0
                        or not button.is_visible()
                    ):

                        print(
                            "[건설이코노미뉴스] "
                            "더보기 버튼 없음 "
                            "→ 탐색 종료"
                        )

                        break

                    # 기존 AJAX 응답 감지 방식 유지.

                    def is_target_response(response):

                        if (
                            "ajaxArticlePaging.php"
                            not in response.url
                        ):
                            return False

                        query = parse_qs(
                            urlparse(
                                response.url
                            ).query
                        )

                        return (
                            query.get("page")
                            == [str(page_number)]
                        )

                    with page.expect_response(
                        is_target_response,
                        timeout=30000,
                    ) as response_info:

                        button.click(
                            timeout=10000
                        )

                    ajax_response = (
                        response_info.value
                    )

                    if ajax_response.status != 200:

                        raise RuntimeError(
                            f"AJAX page {page_number} "
                            f"HTTP {ajax_response.status}"
                        )

                    payload = (
                        ajax_response.json()
                    )

                    entries = (
                        self.extract_ajax_entries(
                            payload
                        )
                    )

                    if not entries:

                        print(
                            "[건설이코노미뉴스] "
                            f"page {page_number}: "
                            "기사 없음 → 탐색 종료"
                        )

                        break

                    signature = tuple(
                        entry["url"]
                        for entry in entries
                    )

                    if signature == previous_signature:

                        print(
                            "[건설이코노미뉴스] "
                            f"page {page_number}: "
                            "이전 페이지와 동일 "
                            "→ 탐색 종료"
                        )

                        break

                    (
                        newest,
                        previous_signature,
                        undated_count,
                    ) = self.process_page_entries(
                        entries=entries,
                        page_number=page_number,
                        start_date=start_date,
                        end_date=end_date,
                        urls=urls,
                        seen_urls=seen_urls,
                        detail_page=detail_page,
                    )

                    # 날짜 미확인 기사가 존재하면
                    # 페이지 전체를 과거라고 단정하지 않는다.

                    if (
                        newest is not None
                        and newest < candidate_start
                        and undated_count == 0
                    ):

                        old_page_streak += 1

                    else:

                        old_page_streak = 0

                    # 기존 AJAX 버튼 상태 갱신 대기 유지.

                    page.wait_for_timeout(300)

                else:

                    print(
                        "[WARNING] 건설이코노미뉴스 "
                        f"최대 탐색 페이지 "
                        f"{self.max_pages}개 도달"
                    )

            finally:

                browser.close()

        print(
            "[건설이코노미뉴스] "
            f"요청 기간 기사 URL "
            f"{len(urls)}개 확보"
        )

        return urls

    # ========================================================
    # TITLE CLEANUP
    # ========================================================

    def extract_title(self, soup):
        """
        기존 상세기사 제목의
        매체명 접미사 제거 기능 유지.
        """

        title = super().extract_title(soup)

        title = html.unescape(
            title or ""
        )

        title = re.sub(
            r"\s*[-|]\s*건설이코노미뉴스\s*$",
            "",
            title,
        )

        return self.clean_text(title)
    