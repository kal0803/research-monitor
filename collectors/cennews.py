from datetime import timedelta

from collectors.generic import GenericNewsCollector


class CENewsCollector(GenericNewsCollector):

    source_key = "cennews"

    pagination_enabled = True

    max_pages = 50
    old_page_stop_streak = 2

    candidate_margin_days = 3

    def decode_response(self, response):
        for encoding in ("utf-8-sig", "cp949"):
            try:
                return response.content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return super().decode_response(response)

    # ========================================================
    # DETAIL DATE PRIORITY
    # ========================================================

    def choose_published_at(
        self,
        url,
        detail_published_at,
    ):
        """
        건설산업신문은 상세페이지에 표시되는
        '승인' 날짜를 최종 기사 날짜로 사용한다.

        목록 날짜는 최종 판정에 사용하지 않는다.
        """

        metadata = self._listing_metadata.get(
            url,
            {},
        )

        listing_published_at = metadata.get(
            "published_at"
        )

        if detail_published_at:

            detail_date = self.published_at_to_date(
                detail_published_at
            )

            listing_date = self.published_at_to_date(
                listing_published_at
            )

            if (
                detail_date is not None
                and listing_date is not None
                and detail_date != listing_date
            ):

                print(
                    f"[{self.source_name}][DATE] "
                    f"목록={listing_published_at} | "
                    f"상세승인={detail_published_at} | "
                    f"{url}"
                )

            return detail_published_at

        print(
            f"[WARNING] {self.source_name} "
            "상세페이지 승인일 확인 불가 | "
            f"목록={listing_published_at} | "
            f"{url}"
        )

        return None

    # ========================================================
    # HISTORICAL PAGINATION
    # ========================================================

    def collect_article_urls(
        self,
        start_date=None,
        end_date=None,
    ):
        """
        건설산업신문 기사 URL 수집.

        1. 기존 Generic 페이지 요청 방식 사용
        2. 목록에서 기사 URL 및 보조 날짜 추출
        3. 요청 날짜 전후 3일 범위의 후보 확보
        4. 목록 날짜가 없는 기사도 후보 유지
        5. 상세페이지 승인일로 최종 판정
        6. 과거 페이지가 연속 확인되면 탐색 종료

        기존 본문, 기자명, 캡션 추출은 변경하지 않는다.
        """

        self._listing_metadata = {}

        if start_date is None:
            start_date = self._active_start_date

        if end_date is None:
            end_date = self._active_end_date

        if (start_date is None) != (end_date is None):

            raise ValueError(
                "start_date와 end_date를 "
                "모두 지정해야 합니다."
            )

        if (
            start_date is not None
            and start_date > end_date
        ):

            raise ValueError(
                "시작일이 종료일보다 늦습니다."
            )

        candidate_start = (
            start_date
            - timedelta(
                days=self.candidate_margin_days
            )
            if start_date is not None
            else None
        )

        candidate_end = (
            end_date
            + timedelta(
                days=self.candidate_margin_days
            )
            if end_date is not None
            else None
        )

        print(
            f"[{self.source_name}] "
            f"후보 기간={candidate_start} ~ "
            f"{candidate_end} | "
            "최종 기준=상세페이지 승인일"
        )

        urls = []

        seen_urls = set()
        seen_signatures = set()

        old_page_streak = 0

        # ====================================================
        # PAGE LOOP
        # ====================================================

        for page_number in range(
            1,
            self.max_pages + 1,
        ):

            page_url = self.build_page_url(
                page_number
            )

            try:

                soup = self.get_soup(
                    page_url
                )

            except Exception as error:

                print(
                    f"[WARNING] {self.source_name} "
                    f"page {page_number} 요청 실패"
                )

                print(
                    f"  URL: {page_url}"
                )

                print(
                    f"  {error}"
                )

                print(
                    f"[WARNING] {self.source_name} "
                    "페이지 수집이 중단되었습니다. "
                    "결과가 불완전할 수 있습니다."
                )

                break

            # =================================================
            # LIST ENTRIES
            # =================================================

            entries = self.extract_listing_entries(
                soup=soup,
                page_url=page_url,
            )

            if not entries:

                print(
                    f"[{self.source_name}] "
                    f"page {page_number}: "
                    "기사 URL 없음 → 탐색 종료"
                )

                break

            # =================================================
            # DUPLICATE PAGE DETECTION
            # =================================================

            signature = tuple(
                entry["url"]
                for entry in entries
            )

            if signature in seen_signatures:

                print(
                    f"[WARNING] {self.source_name} "
                    f"page {page_number}: "
                    "이미 방문한 페이지와 "
                    "기사 목록 동일 → 탐색 종료"
                )

                break

            seen_signatures.add(
                signature
            )

            # =================================================
            # DATE AND CANDIDATE FILTERING
            # =================================================

            known_dates = []

            missing_date_count = 0
            matched_candidates = 0
            outside_margin_count = 0

            for entry in entries:

                url = entry["url"]

                listing_published_at = entry.get(
                    "published_at"
                )

                listing_date = self.published_at_to_date(
                    listing_published_at
                )

                if listing_date is None:

                    missing_date_count += 1

                else:

                    known_dates.append(
                        listing_date
                    )

                # 목록 날짜는 후보 탐색에만 사용한다.
                # 날짜가 없으면 상세 확인 후보에 유지한다.

                if (
                    candidate_start is not None
                    and listing_date is not None
                    and not (
                        candidate_start
                        <= listing_date
                        <= candidate_end
                    )
                ):

                    outside_margin_count += 1

                    continue

                if url in seen_urls:
                    continue

                seen_urls.add(
                    url
                )

                self._listing_metadata[url] = {
                    "title": entry.get(
                        "title",
                        "",
                    ),
                    "published_at": listing_published_at,
                }

                urls.append(
                    url
                )

                matched_candidates += 1

            # =================================================
            # PAGE DATE RANGE
            # =================================================

            if known_dates:

                page_oldest = min(
                    known_dates
                )

                page_newest = max(
                    known_dates
                )

                page_range = (
                    f"{page_oldest} ~ {page_newest}"
                )

            else:

                page_newest = None

                page_range = (
                    "목록 날짜 확인 불가"
                )

            print(
                f"[{self.source_name}] "
                f"page {page_number}: "
                f"{page_range} | "
                f"기사 {len(entries)}개 | "
                f"후보 {matched_candidates}개 | "
                f"날짜 없음 {missing_date_count}개 | "
                f"여유 범위 밖 {outside_margin_count}개"
            )

            # =================================================
            # PAGINATION STOP
            # =================================================

            # 날짜를 모르는 기사가 존재하는 페이지는
            # 전체가 과거 기사라고 단정하지 않는다.

            if (
                candidate_start is not None
                and page_newest is not None
                and page_newest < candidate_start
                and missing_date_count == 0
            ):

                old_page_streak += 1

            else:

                old_page_streak = 0

            if (
                old_page_streak
                >= self.old_page_stop_streak
            ):

                print(
                    f"[{self.source_name}] "
                    "후보 시작일보다 과거인 "
                    f"페이지 {old_page_streak}개 "
                    "연속 → 탐색 종료"
                )

                break

        else:

            print(
                f"[WARNING] {self.source_name} "
                f"최대 탐색 페이지 "
                f"{self.max_pages}개 도달"
            )

        print(
            f"[{self.source_name}] "
            f"요청 기간 후보 기사 URL "
            f"{len(urls)}개 확보"
        )

        return urls
