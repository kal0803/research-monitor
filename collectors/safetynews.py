import re

from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from collectors.generic import GenericNewsCollector


class SafetyNewsCollector(GenericNewsCollector):
    source_key = "safetynews"
    target_category = "건설안전"

    # 검색일과 실제 기사 입력일이 어긋날 가능성에 대비한다.
    candidate_margin_days = 3

    # 기존 누락 기사 복구 한도는 유지한다.
    fallback_scan_margin = 30
    max_fallback_requests = 100

    # ========================================================
    # LIST DATE
    # ========================================================

    def extract_listing_date(self, text, target_day):
        """
        목록의 YYYY.MM.DD 또는 MM.DD HH:MM 형식을 date로 변환한다.
        목록 날짜는 최종 수집 여부를 결정하지 않는다.
        MM.DD의 연도는 검색일과 가장 가까운 연도로 추정한다.
        """
        text = self.clean_text(text)
        if not text:
            return None

        match = re.search(
            r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
            text,
        )
        if match:
            try:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                ).date()
            except ValueError:
                pass

        match = re.search(
            r"(?<!\d)(\d{1,2})[.\-](\d{1,2})\s+\d{1,2}:\d{2}",
            text,
        )
        if not match:
            return None

        month = int(match.group(1))
        day = int(match.group(2))
        candidates = []

        for year in (target_day.year - 1, target_day.year, target_day.year + 1):
            try:
                candidates.append(datetime(year, month, day).date())
            except ValueError:
                continue

        if not candidates:
            return None

        return min(candidates, key=lambda candidate: abs(candidate - target_day))

    # ========================================================
    # TOTAL COUNT
    # ========================================================

    def extract_total_count(self, html):
        """기존 pagination 링크의 total 값을 읽는다."""
        soup = BeautifulSoup(html, "html.parser")
        totals = []

        for link in soup.find_all("a", href=True):
            match = re.search(r"[?&]total=(\d+)", link.get("href", ""))
            if not match:
                continue
            try:
                totals.append(int(match.group(1)))
            except ValueError:
                continue

        return max(totals) if totals else None

    # ========================================================
    # LIST ENTRIES
    # ========================================================

    def extract_search_entries(self, html, target_day):
        """
        ul.type1 > li에서 URL, 제목, 목록 날짜, 건설안전 여부를 추출한다.
        목록 날짜가 검색일과 달라도 제외하지 않는다.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("ul.type1 > li")
        entries = []
        seen_ids = set()

        for item in items:
            title_element = item.select_one("h4.titles")
            if title_element is None:
                continue

            link = title_element.find("a", href=True)
            if link is None:
                continue

            href = link.get("href", "")
            match = re.search(r"idxno=(\d+)", href)
            if not match:
                continue

            idxno = match.group(1)
            if idxno in seen_ids:
                continue
            seen_ids.add(idxno)

            url = urljoin(self.base_url, href)
            title = self.clean_text(title_element.get_text(" ", strip=True))
            full_text = self.clean_text(item.get_text(" ", strip=True))
            article_date = self.extract_listing_date(full_text, target_day)

            # 제목 내 '건설안전' 문구를 카테고리로 오인하지 않는다.
            metadata = full_text
            if title:
                metadata = metadata.replace(title, "", 1).strip()

            entries.append({
                "idxno": idxno,
                "url": url,
                "title": title,
                "date": article_date,
                "is_target": self.target_category in metadata,
                "source": "list",
            })

        return entries

    # ========================================================
    # DAY SEARCH
    # ========================================================

    def fetch_day_search(self, target_day, order="E"):
        """
        정상 작동하는 하루 단위 날짜검색을 사용한다.
        E/C 정렬이 서로 다른 일부 기사를 제공하므로 합집합을 사용한다.
        """
        url = urljoin(self.base_url, "news/articleList.html")
        day_text = target_day.strftime("%Y-%m-%d")
        params = {
            "page": 1,
            "sc_sdate": day_text,
            "sc_edate": day_text,
            "sc_order_by": order,
            "view_type": "sm",
        }
        response = self.session.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.text

    # ========================================================
    # ARTICLE DATE: INPUT ONLY
    # ========================================================

    def extract_published_at(self, page_text):
        """
        최종 Article.published_at은 화면의 '입력' 날짜만 사용한다.
        Generic의 승인/등록/무표기 날짜 fallback은 사용하지 않는다.
        """
        page_text = self.clean_text(page_text)
        match = re.search(
            r"(?<![가-힣A-Za-z])(?:기사\s*입력|입력)\s*[:：]?\s*"
            r"(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
            r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)",
            page_text,
        )
        if match is None:
            return None
        return self.normalize_datetime(match.group(1))

    # ========================================================
    # DETAIL FALLBACK
    # ========================================================

    def extract_detail_metadata(self, html):
        """
        누락된 idxno의 상세페이지 제목, 카테고리 및 '입력' 날짜 확인.
        수정일 또는 이름 없는 날짜를 입력일로 대신 사용하지 않는다.
        """
        soup = BeautifulSoup(html, "html.parser")
        title = ""

        meta_title = soup.find("meta", attrs={"property": "og:title"})
        if meta_title is not None and meta_title.get("content"):
            title = self.clean_text(meta_title.get("content"))
        if not title:
            h1 = soup.find("h1")
            if h1 is not None:
                title = self.clean_text(h1.get_text(" ", strip=True))

        category = ""
        section_meta = soup.find(
            "meta", attrs={"property": "article:section1"}
        )
        if section_meta is not None and section_meta.get("content"):
            category = self.clean_text(section_meta.get("content"))

        page_text = self.clean_text(soup.get_text(" ", strip=True))
        input_datetime = self.extract_published_at(page_text)
        article_date = self.published_at_to_date(input_datetime)

        return {
            "title": title,
            "category": category,
            "date": article_date,
        }

    def recover_missing_entries(
        self,
        target_day,
        entries_by_id,
        expected_total,
    ):
        """
        기존 누락 ID 탐색을 유지한다.
        검색일과 상세 입력일이 최대 3일 달라도 후보로 복구한다.
        최종 요청 기간 판정은 Generic.collect_articles()에서 수행한다.
        """
        if expected_total is None or len(entries_by_id) >= expected_total:
            return 0

        numeric_ids = sorted(
            int(idxno)
            for idxno in entries_by_id.keys()
            if str(idxno).isdigit()
        )
        if not numeric_ids:
            return 0

        minimum = min(numeric_ids)
        maximum = max(numeric_ids)
        known = set(numeric_ids)
        candidates = []

        for idxno in range(minimum, maximum + 1):
            if idxno not in known:
                candidates.append(idxno)

        for offset in range(1, self.fallback_scan_margin + 1):
            lower = minimum - offset
            upper = maximum + offset
            if lower > 0 and lower not in known:
                candidates.append(lower)
            if upper not in known:
                candidates.append(upper)

        recovered = 0
        request_count = 0
        candidate_start = target_day - timedelta(days=self.candidate_margin_days)
        candidate_end = target_day + timedelta(days=self.candidate_margin_days)

        for idxno in candidates:
            if len(entries_by_id) >= expected_total:
                break
            if request_count >= self.max_fallback_requests:
                break

            request_count += 1
            url = urljoin(
                self.base_url,
                f"news/articleView.html?idxno={idxno}",
            )

            try:
                response = self.session.get(url, timeout=30)
                if response.status_code != 200:
                    continue
            except Exception:
                continue

            metadata = self.extract_detail_metadata(response.text)
            detail_date = metadata["date"]
            if detail_date is None or not (
                candidate_start <= detail_date <= candidate_end
            ):
                continue

            entry = {
                "idxno": str(idxno),
                "url": url,
                "title": metadata["title"],
                "date": detail_date,
                "is_target": metadata["category"] == self.target_category,
                "source": "fallback",
            }
            entries_by_id[str(idxno)] = entry
            recovered += 1

            print(
                f"[안전신문] {target_day} 누락 기사 복구: "
                f"{idxno} | 상세입력={detail_date} | "
                f"{metadata['category'] or '카테고리 미확인'} | "
                f"{metadata['title']}"
            )

        return recovered

    # ========================================================
    # ARTICLE URL COLLECTION
    # ========================================================

    def collect_article_urls(self, start_date=None, end_date=None):
        """
        하루 날짜검색 E/C → ID fallback → 건설안전 후보 확보.
        요청 기간 전후 3일도 검색하되 목록 날짜로 제외하지 않는다.
        최종 판정은 상세페이지의 입력일을 읽는 Generic.collect_articles()가 수행한다.
        """
        if start_date is None or end_date is None:
            raise ValueError(
                "안전신문 수집에는 start_date와 end_date가 필요합니다."
            )
        if start_date > end_date:
            raise ValueError("start_date가 end_date보다 늦습니다.")

        # 안전신문은 기존 Generic의 목록 메타데이터를 사용하지 않는다.
        # 따라서 Generic.choose_published_at은 상세페이지 입력일을 채택한다.
        self._listing_metadata = {}

        scan_start = start_date - timedelta(days=self.candidate_margin_days)
        scan_end = end_date + timedelta(days=self.candidate_margin_days)

        print(
            f"[안전신문] 검색 후보 기간={scan_start} ~ {scan_end} | "
            f"최종 수집 기간={start_date} ~ {end_date} | "
            "최종 기준=상세페이지 입력일"
        )

        urls = []
        seen_urls = set()
        target_day = scan_start

        while target_day <= scan_end:
            try:
                e_html = self.fetch_day_search(target_day=target_day, order="E")
            except Exception as error:
                print(
                    f"[WARNING] 안전신문 {target_day} E 검색 실패: {error}"
                )
                target_day += timedelta(days=1)
                continue

            e_entries = self.extract_search_entries(e_html, target_day)
            expected_total = self.extract_total_count(e_html)
            entries_by_id = {
                entry["idxno"]: entry for entry in e_entries
            }
            e_count = len(entries_by_id)

            # E의 total이 비거나 부정확한 경우에도 C의 고유 기사를
            # 발견할 수 있도록 C를 함께 조회한다.
            c_count = 0
            try:
                c_html = self.fetch_day_search(target_day=target_day, order="C")
                c_entries = self.extract_search_entries(c_html, target_day)
                c_count = len(c_entries)

                c_total = self.extract_total_count(c_html)
                if c_total is not None:
                    expected_total = max(expected_total or 0, c_total)

                for entry in c_entries:
                    entries_by_id.setdefault(entry["idxno"], entry)
            except Exception as error:
                print(
                    f"[WARNING] 안전신문 {target_day} C 정렬 검색 실패: {error}"
                )

            union_count = len(entries_by_id)
            if expected_total is None:
                expected_total = union_count

            recovered = 0
            if union_count < expected_total:
                recovered = self.recover_missing_entries(
                    target_day=target_day,
                    entries_by_id=entries_by_id,
                    expected_total=expected_total,
                )

            final_count = len(entries_by_id)

            # 목록 날짜가 검색일과 다른 기사를 더 이상 버리지 않는다.
            # 상세 입력일 범위는 collect_articles에서 마지막에 검증한다.
            target_entries = [
                entry for entry in entries_by_id.values()
                if entry["is_target"]
            ]

            selected = 0
            for entry in target_entries:
                url = entry["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                urls.append(url)
                selected += 1

            print(
                f"[안전신문] {target_day}: "
                f"total {expected_total} | E {e_count} | C {c_count} | "
                f"합집합 {union_count} | fallback {recovered} | "
                f"최종확보 {final_count} | 건설안전 후보 {selected}"
            )

            if final_count < expected_total:
                print(
                    f"[WARNING] 안전신문 {target_day}: "
                    f"검색결과는 {expected_total}개지만 "
                    f"{final_count}개만 복구했습니다."
                )

            target_day += timedelta(days=1)

        print(
            f"[안전신문] 상세 입력일 확인 후보 URL {len(urls)}개 확보"
        )
        return urls

    # ========================================================
    # TITLE CLEANUP
    # ========================================================

    def extract_title(self, soup):
        """기존 상세기사 제목 뒤의 매체명 접미사를 제거한다."""
        title = super().extract_title(soup) or ""
        title = re.sub(r"\s*[-|]\s*안전신문\s*$", "", title)
        return self.clean_text(title)
