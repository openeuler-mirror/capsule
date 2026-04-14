from typing import Annotated


try:
    from pydantic import BaseModel, Field as pydantic_field
except ImportError:  # pragma: no cover - minimal fallback for test environments
    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_json_schema(cls):
            return {"title": cls.__name__, "type": "object"}

    def pydantic_field(default=None, **_kwargs):
        return default


class TemplateResult(BaseModel):
    reason: Annotated[str, pydantic_field(description="选择该模板的理由。")]
    name: Annotated[str, pydantic_field(description="选择模板的name。")]
