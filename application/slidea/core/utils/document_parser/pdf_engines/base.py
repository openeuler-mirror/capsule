import os
from abc import ABC, abstractmethod
from typing import Optional, Set

from core.utils.document_parser.config import PDFEngineConfig
from core.utils.document_parser.models import ParseResult


class PDFParserBase(ABC):
    priority: int = 999

    def __init__(self, config: PDFEngineConfig):
        self.config = config
        self.output_dir = config.output_dir or "./outputs"
        os.makedirs(self.output_dir, exist_ok=True)

    @classmethod
    def support_file_type(cls) -> Set[str]:
        return {".pdf"}

    @classmethod
    def is_available(cls, config: Optional[PDFEngineConfig] = None) -> bool:
        return True

    @abstractmethod
    async def parse(self, pdf_path: str) -> ParseResult:
        pass
