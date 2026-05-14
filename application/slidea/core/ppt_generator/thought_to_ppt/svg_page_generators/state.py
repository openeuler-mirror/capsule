try:
    from pydantic import BaseModel, Field as pydantic_field
except ImportError:  # pragma: no cover - minimal fallback for test environments
    _FIELD_UNSET = object()

    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_json_schema(cls):
            return {"title": cls.__name__, "type": "object"}

    def pydantic_field(default=_FIELD_UNSET, *, default_factory=None, **_kwargs):
        if default_factory is not None:
            return default_factory()
        if default is _FIELD_UNSET:
            return ...
        return default


class TemplateResult(BaseModel):
    reason: str = pydantic_field(description="选择该模板的理由。")
    name: str = pydantic_field(description="选择模板的name。")
