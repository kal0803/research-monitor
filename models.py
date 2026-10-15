from dataclasses import dataclass, field
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
    # 원문 HTML에서 명시적으로 추출한 부제목. 끝에 두어 기존 positional 생성과 호환한다.
    subheadings: List[str] = field(default_factory=list)
