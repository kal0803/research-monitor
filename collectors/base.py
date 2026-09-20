from abc import ABC, abstractmethod
from datetime import date

from models import Article


class BaseNewsCollector(ABC):
    """
    모든 뉴스 사이트 collector가 따라야 하는 공통 규격.
    """

    source_name: str

    @abstractmethod
    def collect_articles(
        self,
        start_date: date,
        end_date: date,
    ) -> list[Article]:
        """
        start_date부터 end_date까지의 기사를 수집한다.
        """
        pass