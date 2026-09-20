from dataclasses import dataclass
from typing import Optional, List


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
    