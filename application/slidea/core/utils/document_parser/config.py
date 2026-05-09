import os
from dataclasses import dataclass
from typing import Any, Optional, Union

from core.utils.llm import default_vlm
from core.utils.config import output_files_dir


@dataclass
class PDFEngineConfig:
    engine: Optional[str] = None
    output_dir: Optional[str] = None
    extract_images: Optional[bool] = None
    vlm_model: Optional[Any] = None

    def __post_init__(self):
        if self.vlm_model is None:
            self.vlm_model = default_vlm
        if self.output_dir is None:
            self.output_dir = output_files_dir
        if self.extract_images is None:
            self.extract_images = False


@dataclass
class TextConfig:
    encoding: Optional[str] = None
    output_dir: Optional[str] = None

    def __post_init__(self):
        if self.encoding is None:
            self.encoding = "utf-8"

        if self.output_dir is None:
            self.output_dir = output_files_dir


@dataclass
class DocxConfig:
    extract_images: Optional[bool] = None
    output_dir: Optional[str] = None

    def __post_init__(self):
        if self.extract_images is None:
            self.extract_images = False

        if self.output_dir is None:
            self.output_dir = output_files_dir


@dataclass
class HtmlConfig:
    extract_images: Optional[bool] = None
    output_dir: Optional[str] = None

    def __post_init__(self):
        if self.extract_images is None:
            self.extract_images = False

        if self.output_dir is None:
            self.output_dir = output_files_dir


@dataclass
class PptxConfig:
    extract_images: Optional[bool] = None
    output_dir: Optional[str] = None

    def __post_init__(self):
        if self.extract_images is None:
            self.extract_images = False

        if self.output_dir is None:
            self.output_dir = output_files_dir


@dataclass
class ImageConfig:
    vlm_model: Optional[Any] = None
    output_dir: Optional[str] = None

    def __post_init__(self):
        if self.vlm_model is None:
            self.vlm_model = default_vlm
        if self.output_dir is None:
            self.output_dir = output_files_dir


@dataclass
class MarkdownConfig:
    extract_images: Optional[bool] = None
    output_dir: Optional[str] = None

    def __post_init__(self):
        if self.extract_images is None:
            self.extract_images = False

        if self.output_dir is None:
            self.output_dir = output_files_dir


@dataclass
class ParserConfig:
    output_dir: Optional[str] = None
    extract_images: Optional[bool] = None
    pdf: Optional[Union[dict, PDFEngineConfig]] = None
    text: Optional[Union[dict, TextConfig]] = None
    docx: Optional[Union[dict, DocxConfig]] = None
    html: Optional[Union[dict, HtmlConfig]] = None
    pptx: Optional[Union[dict, PptxConfig]] = None
    markdown: Optional[Union[dict, MarkdownConfig]] = None
    image: Optional[Union[dict, ImageConfig]] = None

    def __post_init__(self):
        if self.output_dir is None:
            self.output_dir = os.path.join(output_files_dir, "documents/")
        if self.extract_images is None:
            self.extract_images = False

        pdf_config = self.pdf or {}
        pdf_config.setdefault("output_dir", self.output_dir)
        pdf_config.setdefault("extract_images", self.extract_images)
        self.pdf = PDFEngineConfig(**pdf_config)

        text_config = self.text or {}
        text_config.setdefault("output_dir", self.output_dir)
        self.text = TextConfig(**text_config)

        docx_config = self.docx or {}
        docx_config.setdefault("output_dir", self.output_dir)
        docx_config.setdefault("extract_images", self.extract_images)
        self.docx = DocxConfig(**docx_config)

        html_config = self.html or {}
        html_config.setdefault("output_dir", self.output_dir)
        html_config.setdefault("extract_images", self.extract_images)
        self.html = HtmlConfig(**html_config)

        pptx_config = self.pptx or {}
        pptx_config.setdefault("output_dir", self.output_dir)
        pptx_config.setdefault("extract_images", self.extract_images)
        self.pptx = PptxConfig(**pptx_config)

        md_config = self.markdown or {}
        md_config.setdefault("output_dir", self.output_dir)
        md_config.setdefault("extract_images", self.extract_images)
        self.markdown = MarkdownConfig(**md_config)

        image_config = self.image or {}
        image_config.setdefault("output_dir", self.output_dir)
        self.image = ImageConfig(**image_config)
