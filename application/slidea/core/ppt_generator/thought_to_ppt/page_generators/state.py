from pydantic import BaseModel, Field


class TemplateResult(BaseModel):
    reason: str = Field(..., description="选择该模板的理由。")
    name: str = Field(..., description="选择模板的name。")
