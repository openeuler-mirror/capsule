import os
from pathlib import Path

from core.utils.logger import logger
from core.utils.document_parser.models import ParseResult
from core.utils.document_parser.config import TextConfig


class TextReader:
    def __init__(self, config: TextConfig):
        self.output_dir = config.output_dir
        self.encoding = config.encoding
        os.makedirs(self.output_dir, exist_ok=True)

    def parse(self, file_path: str) -> ParseResult:
        logger.info("TextReader 开始解析: {}", file_path)
        with open(file_path, "r", encoding=self.encoding) as f:
            text = f.read()

        md_filename = Path(file_path).stem + ".md"
        md_path = os.path.join(self.output_dir, md_filename)

        with open(md_path, "w", encoding=self.encoding) as f:
            f.write(text)

        logger.info("TextReader 解析完成，文本长度: {} 字符", len(text))
        return ParseResult(text=text, images=[], markdown_file=md_path)
