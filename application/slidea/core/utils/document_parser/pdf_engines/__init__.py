from typing import Dict, Type

from core.utils.logger import logger
from core.utils.document_parser.pdf_engines.base import PDFParserBase

ENGINE_REGISTRY: Dict[str, Type[PDFParserBase]] = {}

try:
    from core.utils.document_parser.pdf_engines.docling_parser import DoclingPDFParser
    ENGINE_REGISTRY["docling"] = DoclingPDFParser
except ImportError:
    logger.debug("DoclingPDFParser 不可用: docling 未安装")

try:
    from core.utils.document_parser.pdf_engines.fitz_parser import FitzPDFParser
    ENGINE_REGISTRY["fitz"] = FitzPDFParser
except ImportError:
    logger.debug("FitzPDFParser 不可用: fitz 未安装")

try:
    from core.utils.document_parser.pdf_engines.fitz_vlm_parser import FitzVLMPDFParser
    ENGINE_REGISTRY["fitz_vlm"] = FitzVLMPDFParser
except ImportError:
    logger.debug("FitzVLMPDFParser 不可用: fitz 或依赖未安装")

__all__ = [
    "PDFParserBase",
    "ENGINE_REGISTRY",
]
