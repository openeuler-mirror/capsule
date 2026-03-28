from typing import TypedDict, Optional


class GenPPTState(TypedDict):
    request: str 

    thought:str
    deep_report:str
    references: str

    htmls: list
    final_pdf_path: Optional[str]
    final_pptx_path: Optional[str]


class PPTInputSchema(TypedDict):
    request: str