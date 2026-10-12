from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Document:
    source: str
    source_id: str
    title: str
    authors: List[str]
    published_at: Optional[str]
    url: str
    abstract: Optional[str]


@dataclass
class Article:
    source: str
    title: str
    reporter: Optional[str]
    published_at: Optional[str]
    image_caption: Optional[str]
    paragraphs: List[str]
    url: str
    # 보고서용 이미지 URL. 기존 Article 생성 코드와 호환된다.
    # 분류기의 article_hash에는 포함되지 않는다.
    image_url: Optional[str] = None
