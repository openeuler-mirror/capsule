import os
from pathlib import Path
from typing import List, Literal, get_args, get_origin

# define project base
# `config.py` now lives under `core/utils/`, but runtime artifacts still belong
# to the skill root directory.
app_base_dir = Path(__file__).resolve().parents[2]
env_file = app_base_dir / ".env"

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency for local env files
    def load_dotenv(*args, **kwargs):
        return False
try:
    from pydantic_settings import BaseSettings
except ImportError:  # pragma: no cover - optional dependency for stricter env parsing
    def _coerce_env_value(raw_value, annotation):
        origin = get_origin(annotation)
        if annotation is bool:
            return raw_value.lower() in {"1", "true", "yes", "on"}
        if annotation is int:
            return int(raw_value)
        if origin in {list, List}:
            return [item.strip() for item in raw_value.split(",") if item.strip()]
        literal_values = get_args(annotation)
        if literal_values and raw_value in literal_values:
            return raw_value
        return raw_value

    class BaseSettings:  # pragma: no cover - exercised indirectly by tests
        def __init__(self, **kwargs):
            annotations = getattr(self.__class__, "__annotations__", {})
            for name, annotation in annotations.items():
                if name in kwargs:
                    value = kwargs[name]
                elif name in os.environ:
                    value = _coerce_env_value(os.environ[name], annotation)
                else:
                    value = getattr(self.__class__, name)
                setattr(self, name, value)

load_dotenv(dotenv_path=env_file, override=True)


class Settings(BaseSettings):
    """Application settings with an optional fallback BaseSettings implementation."""
    model_config = {"extra": "allow"}

    # log
    LOG_LEVEL: str = "INFO"
    SETUP_COMPLETED: bool = False

    # Output root directory. Empty (default) → <app_base_dir>/output.
    # When set to an absolute path, that directory replaces <app_base_dir>/output
    # as the root for every run, cache, and intermediate artifact slidea writes.
    # Relative paths resolve against app_base_dir.
    OUTPUT_DIR: str = ""

    # Default LLM Settings
    DEFAULT_LLM_MODEL: str = ""
    DEFAULT_LLM_API_KEY: str = ""
    DEFAULT_LLM_API_BASE_URL: str = ""

    # Default VLM Settings
    DEFAULT_VLM_MODEL: str = ""
    DEFAULT_VLM_API_KEY: str = ""
    DEFAULT_VLM_API_BASE_URL: str = ""

    # 是否启用基于截图的 VLM 视觉审阅与修改路径
    ENABLE_VLM_VISUAL_REVIEW: bool = False

    # Embedding Settings
    EMBEDDING_MODEL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE_URL: str = ""

    # Image Settings
    TOP_N_IMAGE: int = 10

    # Using Web Image Search
    USE_WEB_IMG_SEARCH: bool = True
    USE_CACHE: bool = True
    DISABLE_EMBEDDING: bool = False
    RESEARCH_MODE_FORCE: Literal["", "skip", "simple", "deep"] = ""

    # Using Image Generation Model
    IMAGE_GEN_PROVIDER: Literal["api", "comfyui_local"] = "api"
    VLM_IMAGE_INPUT_MODE: Literal["raw_base64", "data_url"] = "data_url"
    IMG_GEN_MODEL: str = ""
    IMG_GEN_API_KEY: str = ""
    IMG_GEN_API_BASE_URL: str = ""

    # Local ComfyUI
    COMFYUI_URL: str = ""
    COMFYUI_WORKFLOW: str = ""
    COMFYUI_PROMPT_UTILS_PATH: str = ""
    COMFYUI_CLI_PATH: str = ""
    COMFYUI_PYTHON_BIN: str = ""

    FETCH_WEB_SERVICE_URL: str = ""

    # tavily
    TAVILY_API_KEYS: List[str] = []

    def missing_comfyui_local_settings(self) -> List[str]:
        missing = []
        for name in [
            "COMFYUI_URL",
            "COMFYUI_WORKFLOW",
            "COMFYUI_PROMPT_UTILS_PATH",
            "COMFYUI_CLI_PATH",
            "COMFYUI_PYTHON_BIN",
        ]:
            if not getattr(self, name, ""):
                missing.append(name)
        return missing

    def missing_image_generation_settings(self) -> List[str]:
        if self.IMAGE_GEN_PROVIDER == "comfyui_local":
            return self.missing_comfyui_local_settings()

        missing = []
        if not self.IMG_GEN_MODEL:
            missing.append("IMG_GEN_MODEL")
        if not self.IMG_GEN_API_KEY:
            missing.append("IMG_GEN_API_KEY")
        if not self.IMG_GEN_API_BASE_URL:
            missing.append("IMG_GEN_API_BASE_URL")
        return missing

    def is_image_generation_enabled(self) -> bool:
        return not self.missing_image_generation_settings()

    def missing_embedding_settings(self) -> List[str]:
        missing = []
        if not self.EMBEDDING_MODEL:
            missing.append("EMBEDDING_MODEL")
        if not self.EMBEDDING_API_BASE_URL:
            missing.append("EMBEDDING_API_BASE_URL")
        if not self.EMBEDDING_API_KEY:
            missing.append("EMBEDDING_API_KEY")
        return missing

    def has_tavily_search_config(self) -> bool:
        return bool(self.TAVILY_API_KEYS)

    def missing_default_llm_settings(self) -> List[str]:
        missing = []
        if not self.DEFAULT_LLM_MODEL:
            missing.append("DEFAULT_LLM_MODEL")
        if not self.DEFAULT_LLM_API_KEY:
            missing.append("DEFAULT_LLM_API_KEY")
        if not self.DEFAULT_LLM_API_BASE_URL:
            missing.append("DEFAULT_LLM_API_BASE_URL")
        return missing

    def has_default_llm_config(self) -> bool:
        return not self.missing_default_llm_settings()

    def missing_default_vlm_settings(self) -> List[str]:
        missing = []
        if not self.DEFAULT_VLM_MODEL:
            missing.append("DEFAULT_VLM_MODEL")
        if not self.DEFAULT_VLM_API_KEY:
            missing.append("DEFAULT_VLM_API_KEY")
        if not self.DEFAULT_VLM_API_BASE_URL:
            missing.append("DEFAULT_VLM_API_BASE_URL")
        return missing

    def has_default_vlm_config(self) -> bool:
        return not self.missing_default_vlm_settings()

    def use_data_url_for_vlm_images(self) -> bool:
        return self.VLM_IMAGE_INPUT_MODE == "data_url"


# Create settings instance
settings = Settings()


def _resolve_output_files_dir() -> str:
    """Resolve the output root directory from settings, falling back to <app_base_dir>/output.

    Absolute paths are used as-is. Relative paths resolve against app_base_dir.
    Empty/unset → <app_base_dir>/output (the historical default).
    """
    raw = (getattr(settings, "OUTPUT_DIR", "") or "").strip()
    if not raw:
        return os.path.join(app_base_dir, "output")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(app_base_dir) / p
    return str(p)


output_files_dir = _resolve_output_files_dir()
