from collectors.generic import (
    GenericNewsCollector,
)


class ConTimesCollector(
    GenericNewsCollector
):
    source_key = "constimes"

    excluded_title_terms = (
        "한줄뉴스",
        "한줄 뉴스",
    )
    