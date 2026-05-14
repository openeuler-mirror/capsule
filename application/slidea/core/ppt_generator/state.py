from typing import TypedDict, Optional

try:
    from typing_extensions import NotRequired, Literal
except ImportError:  # pragma: no cover - Python 3.11+ fallback
    from typing import NotRequired, Literal


class GenPPTState(TypedDict):
    request: str 
    render_mode: NotRequired[Literal["html", "svg"]]

    thought:str
    deep_report:str
    references: str
    images: list

    htmls: list
    svgs: NotRequired[list]
    final_pdf_path: Optional[str]
    final_pptx_path: Optional[str]


class PPTInputSchema(TypedDict):
    request: str
    render_mode: NotRequired[Literal["html", "svg"]]
