from dataclasses import dataclass
from typing import List


@dataclass
class ImageInfo:
    path: str
    description: str = ""
    bbox: tuple = None


@dataclass
class PageContent:
    page_num: int
    items: list
    images: List[ImageInfo]
    page_width: float
    vector_metadata: list = None
    num_cols: int = 1
    col_boundary: float = None

    def __post_init__(self):
        if self.vector_metadata is None:
            self.vector_metadata = []


@dataclass
class ParseResult:
    text: str
    images: List[ImageInfo]
    markdown_file: str

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "images": [
                {"path": img.path, "description": img.description}
                for img in self.images
            ],
            "markdown_file": self.markdown_file,
        }
