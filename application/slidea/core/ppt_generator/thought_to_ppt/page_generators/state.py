try:
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - minimal fallback for test environments
    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_json_schema(cls):
            return {"title": cls.__name__, "type": "object"}

    def Field(default=None, **_kwargs):
        return default


class TemplateResult(BaseModel):
    reason: str = Field(..., description="选择该模板的理由。")
    name: str = Field(..., description="选择模板的name。")
