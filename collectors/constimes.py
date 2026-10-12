
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from collectors.generic import GenericNewsCollector


class ConTimesCollector(GenericNewsCollector):
    source_key = "constimes"

    excluded_title_terms = (
        "한줄뉴스",
        "한줄 뉴스",
    )

    # config.py에 설정된 건설 섹션 코드
    target_section_code = "S1N1"

    max_pages = 200
    old_page_streak_limit = 2

    # AJAX 날짜와 상세페이지 입력일의 차이를 고려한 유한한 탐색 여유
    candidate_margin_days = 3

    ajax_path = "news/ajaxArticlePaging.php"

    # ========================================================
    # AJAX SETTINGS
    # ========================================================

    def extract_js_param(self, html, name):
        """페이지의 params['total'], params['list_per_page'] 등을 추출한다."""
        patterns = [
            (
                rf"params\[\s*['\"]{re.escape(name)}['\"]\s*\]"
                rf"\s*=\s*['\"]([^'\"]+)['\"]"
            ),
            (
                rf"params\[\s*['\"]{re.escape(name)}['\"]\s*\]"
                rf"\s*=\s*(\d+)"
            ),
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def extract_ajax_settings(self, html):
        """실제 섹션 페이지의 articlePaging 설정값을 읽는다."""
        total = self.extract_js_param(html, "total")
        list_per_page = self.extract_js_param(html, "list_per_page") or "20"
        page_per_page = self.extract_js_param(html, "page_per_page") or "10"
        list_page_limit = self.extract_js_param(html, "list_page_limit") or "200"

        if not total:
            patterns = [
                r"total\s*[:=]\s*['\"]?(\d+)",
                r"['\"]total['\"]\s*:\s*['\"]?(\d+)",
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    total = match.group(1)
                    break

        if not total:
            raise RuntimeError(
                "건설타임즈 AJAX total 값을 페이지에서 찾지 못했습니다."
            )

        try:
            list_page_limit_int = int(list_page_limit)
        except ValueError:
            list_page_limit_int = self.max_pages

        return {
            "total": str(total),
            "list_per_page": str(list_per_page),
            "page_per_page": str(page_per_page),
            "list_page_limit": list_page_limit_int,
            "box_idxno": "0",
        }

    # ========================================================
    # INITIAL PAGE
    # ========================================================

    def load_initial_page(self):
        """최초 섹션 페이지 요청으로 세션 및 AJAX 설정을 확보한다."""
        response = self.session.get(self.section_url, timeout=30)
        response.raise_for_status()
        return response.text

    # ========================================================
    # AJAX DATE
    # ========================================================

    def parse_ajax_date(self, value):
        """AJAX 날짜 또는 상세페이지에서 발견한 날짜 문자열을 date로 변환한다."""
        if not value:
            return None

        value = str(value).strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y.%m.%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue

        match = re.search(
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
            value,
        )
        if not match:
            return None

        try:
            return datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            ).date()
        except ValueError:
            return None

    def extract_ajax_article_date(self, item):
        """AJAX의 reg_date 등을 후보 탐색에만 사용한다."""
        date_keys = (
            "reg_date",
            "approve_date",
            "pub_date",
            "viewDateTime",
        )
        for key in date_keys:
            article_date = self.parse_ajax_date(item.get(key))
            if article_date:
                return article_date
        return None

    # ========================================================
    # DETAIL-PAGE DATE (NEW)
    # ========================================================

    def extract_detail_article_date(self, html):
        """
        상세페이지에 화면상 표시된 '입력/기사입력/등록/발행' 날짜를 읽는다.
        '수정' 날짜는 최종 판정에 사용하지 않는다.
        목록 AJAX 날짜, URL 날짜, 숨은 메타데이터는 최종 판정에 사용하지 않는다.
        """
        soup = BeautifulSoup(html, "html.parser")
        date_pattern = re.compile(
            r"(?:^|\s)(?:기사\s*입력|입력|등록|발행)\s*[:：]?\s*"
            r"(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
            r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            re.IGNORECASE,
        )

        # 기사 상단의 입력일을 우선 찾는다. 마지막 li 탐색은
        # 페이지 템플릿 클래스 변경에 대비한 fallback이다.
        selectors = (
            ".article-view-header li",
            ".article-head li",
            ".article-info li",
            ".article-info",
            ".info-group li",
            "ul.infomation li",
            "ul.information li",
            "#article-view time",
            "li",
            "time",
        )
        for selector in selectors:
            for element in soup.select(selector):
                text = element.get_text(" ", strip=True)
                if not text or len(text) > 200:
                    continue
                match = date_pattern.search(text)
                if match:
                    article_date = self.parse_ajax_date(match.group(1))
                    if article_date:
                        return article_date

        # '입력'과 실제 날짜가 서로 다른 중첩 요소에 있는 경우.
        for string in soup.find_all(string=re.compile(r"입력|발행|등록")):
            parent = string.parent
            for element in (parent, getattr(parent, "parent", None)):
                if element is None:
                    continue
                text = element.get_text(" ", strip=True)
                if not text or len(text) > 200:
                    continue
                match = date_pattern.search(text)
                if match:
                    article_date = self.parse_ajax_date(match.group(1))
                    if article_date:
                        return article_date

        return None

    def fetch_detail_article_date(self, url):
        """본문 추출기는 건드리지 않고 입력일 확인용으로만 상세페이지를 요청한다."""
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return self.extract_detail_article_date(response.text)

    # ========================================================
    # AJAX REQUEST
    # ========================================================

    def request_ajax_page(self, page_number, settings):
        """건설타임즈 '더보기'가 호출하는 기존 AJAX endpoint를 요청한다."""
        ajax_url = urljoin(self.base_url, self.ajax_path)
        params = {
            "total": settings["total"],
            "list_per_page": settings["list_per_page"],
            "page_per_page": settings["page_per_page"],
            "page": str(page_number),
            "box_idxno": settings["box_idxno"],
        }
        headers = {
            "Referer": self.section_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        response = self.session.get(
            ajax_url,
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as e:
            raise RuntimeError(
                "건설타임즈 AJAX 응답을 JSON으로 해석하지 못했습니다."
            ) from e

        if not isinstance(payload, dict):
            raise RuntimeError(
                "건설타임즈 AJAX 응답의 최상위 형식이 dict가 아닙니다."
            )

        data = payload.get("data")
        if data is None:
            raise RuntimeError(
                "건설타임즈 AJAX 응답에 'data' 필드가 없습니다."
            )
        if not isinstance(data, list):
            raise RuntimeError(
                "건설타임즈 AJAX 'data'가 list 형식이 아닙니다."
            )

        return data

    # ========================================================
    # AJAX ITEM
    # ========================================================

    def normalize_ajax_item(self, item):
        """기존 AJAX 기사 객체를 크롤러 내부 구조로 변환한다."""
        if not isinstance(item, dict):
            return None

        idxno = str(item.get("idxno", "")).strip()
        if not re.fullmatch(r"\d+", idxno):
            return None

        title = str(item.get("title", "") or "").strip()
        title = re.sub(r"\s+", " ", title).strip()
        section_code = str(item.get("section_code", "") or "").strip()
        sub_section_code = str(item.get("sub_section_code", "") or "").strip()
        article_date = self.extract_ajax_article_date(item)
        url = urljoin(
            self.base_url,
            "news/articleView.html" f"?idxno={idxno}",
        )
        return {
            "idxno": idxno,
            "url": url,
            "title": title,
            "section_code": section_code,
            "sub_section_code": sub_section_code,
            "date": article_date,
        }

    # ========================================================
    # HISTORICAL PAGINATION
    # ========================================================

    def collect_article_urls(self, start_date=None, end_date=None):
        """
        첫 HTML → AJAX 설정 → page=1,2,... 탐색 → 건설 섹션 필터
        → AJAX 날짜는 여유 범위의 후보 탐색용
        → 상세페이지 입력일로 최종 기간 판정 → URL 반환.
        """
        if start_date is None or end_date is None:
            raise ValueError(
                "건설타임즈 수집에는 start_date와 end_date가 필요합니다."
            )
        if start_date > end_date:
            raise ValueError("start_date는 end_date보다 늦을 수 없습니다.")

        candidate_start = start_date - timedelta(days=self.candidate_margin_days)
        candidate_end = end_date + timedelta(days=self.candidate_margin_days)

        initial_html = self.load_initial_page()
        settings = self.extract_ajax_settings(initial_html)
        print(
            "[건설타임즈] AJAX pagination 확인 | "
            f"total={settings['total']} | "
            f"page size={settings['list_per_page']} | "
            f"후보 기간={candidate_start} ~ {candidate_end}"
        )

        max_page = min(self.max_pages, settings["list_page_limit"])
        urls = []
        seen_urls = set()
        previous_signature = None
        old_page_streak = 0

        for page_number in range(1, max_page + 1):
            try:
                raw_items = self.request_ajax_page(
                    page_number=page_number,
                    settings=settings,
                )
            except requests.RequestException as e:
                print(
                    "[WARNING] 건설타임즈 "
                    f"AJAX page {page_number} 요청 실패"
                )
                print(e)
                break
            except Exception as e:
                print(
                    "[WARNING] 건설타임즈 "
                    f"AJAX page {page_number} 처리 실패"
                )
                print(e)
                break

            if not raw_items:
                print(
                    f"[건설타임즈] page {page_number}: "
                    "기사 없음 → 탐색 종료"
                )
                break

            items = []
            for raw_item in raw_items:
                item = self.normalize_ajax_item(raw_item)
                if item is not None:
                    items.append(item)

            if not items:
                print(
                    f"[건설타임즈] page {page_number}: "
                    "정상 기사 객체 없음 → 탐색 종료"
                )
                break

            signature = tuple(item["idxno"] for item in items)
            if signature == previous_signature:
                print(
                    f"[건설타임즈] page {page_number}: "
                    "이전 페이지와 동일 → 탐색 종료"
                )
                break
            previous_signature = signature

            # 종료 판단에 필요한 날짜는 기존처럼 전체 섹션을 대상으로 확인.
            dated_items = [item for item in items if item["date"] is not None]
            dates = [item["date"] for item in dated_items]
            page_newest = max(dates) if dates else None
            page_oldest = min(dates) if dates else None

            # 날짜가 없는 AJAX 기사도 건설 섹션이면 상세페이지까지 확인.
            construction_items = [
                item
                for item in items
                if item["section_code"] == self.target_section_code
            ]

            matched = 0
            detail_checked = 0
            date_unknown = 0

            for item in construction_items:
                title = item["title"] or ""
                if any(term in title for term in self.excluded_title_terms):
                    continue

                url = item["url"]
                if url in seen_urls:
                    continue

                ajax_date = item["date"]
                if ajax_date is None:
                    date_unknown += 1
                elif not (candidate_start <= ajax_date <= candidate_end):
                    continue

                seen_urls.add(url)

                try:
                    detail_date = self.fetch_detail_article_date(url)
                    detail_checked += 1
                except requests.RequestException as e:
                    print(f"[WARNING] 건설타임즈 상세페이지 요청 실패: {url} | {e}")
                    continue
                except Exception as e:
                    print(f"[WARNING] 건설타임즈 상세 입력일 분석 실패: {url} | {e}")
                    continue

                if detail_date is None:
                    print(
                        "[WARNING] 건설타임즈 상세페이지의 "
                        f"입력/발행일 확인 불가: {url} | AJAX={ajax_date}"
                    )
                    continue

                if ajax_date != detail_date:
                    print(
                        "[건설타임즈][DATE] "
                        f"AJAX={ajax_date} | 상세입력={detail_date} | {title}"
                    )

                if start_date <= detail_date <= end_date:
                    urls.append(url)
                    matched += 1

            page_range = (
                f"{page_oldest} ~ {page_newest}"
                if dates
                else "AJAX 날짜 확인 불가"
            )
            print(
                f"[건설타임즈] page {page_number}: "
                f"{page_range} | 전체 {len(items)}개 | "
                f"건설 {len(construction_items)}개 | "
                f"상세 확인 {detail_checked}개 | "
                f"AJAX 날짜 없음 {date_unknown}개 | "
                f"기간 일치 {matched}개"
            )

            # 목록 날짜가 모두 없으면 중단하지 않고 다음 페이지를 확인.
            # 후보 시작일보다 과거인 페이지가 연속될 때만 유한하게 종료.
            if page_newest is not None and page_newest < candidate_start:
                old_page_streak += 1
            else:
                old_page_streak = 0

            if old_page_streak >= self.old_page_streak_limit:
                print(
                    "[건설타임즈] 후보 시작일보다 과거인 페이지가 "
                    f"{self.old_page_streak_limit}개 연속 확인되어 탐색 종료"
                )
                break

        print(f"[건설타임즈] 요청 기간 기사 URL {len(urls)}개 확보")
        return urls
