import os
import logging
from pathlib import Path
from typing import List, Literal, get_args, get_origin

# define project base
# `config.py` now lives under `core/utils/`, but runtime artifacts still belong
# to the skill root directory.
app_base_dir = Path(__file__).resolve().parents[2]
output_files_dir = os.path.join(app_base_dir, "output")
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

PREMIUM_LLM_DEFAULT_MODEL = "google/gemini-3.1-pro-preview"
PREMIUM_LLM_DEFAULT_API_BASE_URL = "https://openrouter.ai/api/v1"
SUPPORTED_SLIDEA_MODES = ("PREMIUM", "ECONOMIC")
config_logger = logging.getLogger("slidea.config")


class Settings(BaseSettings):
    """Application settings with an optional fallback BaseSettings implementation."""
    model_config = {"extra": "allow"}

    # log
    LOG_LEVEL: str = "INFO"
    SETUP_COMPLETED: bool = False

    # Runtime routing mode
    SLIDEA_MODE: str = "ECONOMIC"

    # Default LLM Settings
    DEFAULT_LLM_MODEL: str = ""
    DEFAULT_LLM_API_KEY: str = ""
    DEFAULT_LLM_API_BASE_URL: str = ""

    # Premium LLM Settings
    PREMIUM_LLM_MODEL: str = PREMIUM_LLM_DEFAULT_MODEL
    PREMIUM_LLM_API_KEY: str = ""
    PREMIUM_LLM_API_BASE_URL: str = PREMIUM_LLM_DEFAULT_API_BASE_URL

    # Default VLM Settings
    DEFAULT_VLM_MODEL: str = ""
    DEFAULT_VLM_API_KEY: str = ""
    DEFAULT_VLM_API_BASE_URL: str = ""

    # Embedding Settings
    EMBEDDING_MODEL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE_URL: str = ""

    # Image Settings
    TOP_N_IMAGE: int = 4

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

    def get_slidea_mode(self) -> Literal["PREMIUM", "ECONOMIC"]:
        raw_mode = str(self.SLIDEA_MODE or "").strip()
        if not raw_mode:
            config_logger.warning("SLIDEA_MODE is empty. Falling back to ECONOMIC mode.")
            return "ECONOMIC"
        if raw_mode in SUPPORTED_SLIDEA_MODES:
            return raw_mode  # type: ignore[return-value]
        raise ValueError(
            f"Invalid SLIDEA_MODE={raw_mode!r}. Supported values: PREMIUM, ECONOMIC, or empty."
        )

    def missing_premium_llm_settings(self) -> List[str]:
        missing = []
        if not self.PREMIUM_LLM_MODEL:
            missing.append("PREMIUM_LLM_MODEL")
        if not self.PREMIUM_LLM_API_KEY:
            missing.append("PREMIUM_LLM_API_KEY")
        if not self.PREMIUM_LLM_API_BASE_URL:
            missing.append("PREMIUM_LLM_API_BASE_URL")
        return missing

    def has_premium_llm_config(self) -> bool:
        return not self.missing_premium_llm_settings()

    def has_premium_llm_api_key(self) -> bool:
        return bool(self.PREMIUM_LLM_API_KEY)

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
settings.get_slidea_mode()
