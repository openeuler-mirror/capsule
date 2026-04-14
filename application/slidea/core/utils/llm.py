import asyncio
import json
from enum import Enum
from typing import Any

from core.utils.logger import logger
try:
    from json_repair import repair_json
except ImportError:  # pragma: no cover - optional dependency in minimal test environments
    def repair_json(value, ensure_ascii=False, return_objects=False):
        if return_objects:
            if isinstance(value, (dict, list)):
                return value
            return json.loads(value)
        return value

try:
    from jsonschema import validate
except ImportError:  # pragma: no cover - optional dependency in minimal test environments
    def validate(*_args, **_kwargs):
        return None

try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
except ImportError:  # pragma: no cover - exercised in minimal runtime environments
    ChatOpenAI = None
    OpenAIEmbeddings = None

from core.utils.config import settings

MAX_INVOKE_ATTEMPTS = 5
RETRY_SLEEP_SECONDS = 10


class LLMInvokeError(RuntimeError):
    """Raised when an LLM or VLM call exhausts retries."""


class ModelRoute(str, Enum):
    DEFAULT = "default"
    PREMIUM = "premium"


class ModelKind(str, Enum):
    LLM = "llm"
    VLM = "vlm"


def _infer_llm_error_hint(error: Exception) -> str:
    message = str(error).lower()
    if any(token in message for token in ["insufficient_quota", "quota", "billing", "余额", "欠费", "payment", "402"]):
        return "Possible quota or billing issue."
    if any(token in message for token in ["401", "unauthorized", "invalid api key", "authentication"]):
        return "Possible API key or authentication issue."
    if any(token in message for token in ["429", "rate limit", "too many requests"]):
        return "Possible rate-limit issue."
    if any(token in message for token in ["timeout", "timed out"]):
        return "Possible upstream timeout."
    return ""


def _client_model_name(client: Any) -> str:
    return getattr(client, "model_name", None) or getattr(client, "model", "") or "unknown"


def _normalize_model_route(route: ModelRoute | str) -> ModelRoute:
    raw_route = route.value if isinstance(route, ModelRoute) else str(route).strip().lower()
    if raw_route == ModelRoute.DEFAULT.value:
        return ModelRoute.DEFAULT
    if raw_route == ModelRoute.PREMIUM.value:
        return ModelRoute.PREMIUM
    raise ValueError(
        f"Unsupported model route {route!r}. "
        f"Only {ModelRoute.DEFAULT.value!r} and "
        f"{ModelRoute.PREMIUM.value!r} are supported."
    )


def _kind_display_name(kind: ModelKind) -> str:
    return "LLM" if kind == ModelKind.LLM else "VLM"


class MissingDependencyClient:
    def __init__(self, dependency_name: str):
        self.dependency_name = dependency_name
        self.model_name = f"missing-dependency:{dependency_name}"

    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError(
            f"Missing optional dependency '{self.dependency_name}'. "
            "Install requirements.txt to enable LLM calls."
        )

    def with_structured_output(self, *_args, **_kwargs):
        return self


class MissingConfigClient:
    def __init__(self, client_name: str, missing_settings: list[str], model_name: str = ""):
        self.client_name = client_name
        self.missing_settings = missing_settings
        self.model_name = model_name or f"missing-config:{client_name}"

    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError(
            f"Missing configuration for {self.client_name}: {', '.join(self.missing_settings)}"
        )

    def with_structured_output(self, *_args, **_kwargs):
        return self


class _ClientHandle:
    def __init__(self, client_name: str):
        self.client_name = client_name
        self._client = None

    def _get_or_create_client(self):
        if self._client is None:
            self._client = _build_chat_client_for_name(self.client_name)
        return self._client

    @property
    def model_name(self) -> str:
        if self._client is None:
            return _configured_model_name(self.client_name)
        return _client_model_name(self._client)

    @property
    def model(self) -> str:
        return self.model_name

    async def ainvoke(self, *args, **kwargs):
        return await self._get_or_create_client().ainvoke(*args, **kwargs)

    def with_structured_output(self, *args, **kwargs):
        return self._get_or_create_client().with_structured_output(*args, **kwargs)


def _configured_model_name(client_name: str) -> str:
    if client_name == "premium_llm":
        return settings.PREMIUM_LLM_MODEL or "unconfigured:premium_llm"
    if client_name == "default_vlm":
        return settings.DEFAULT_VLM_MODEL or "unconfigured:default_vlm"
    return settings.DEFAULT_LLM_MODEL or "unconfigured:default_llm"


def _missing_client_config(client_name: str) -> list[str]:
    if client_name == "premium_llm":
        return settings.missing_premium_llm_settings()
    if client_name == "default_vlm":
        return settings.missing_default_vlm_settings()
    return settings.missing_default_llm_settings()


def _build_chat_client(
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    max_retries: int,
    streaming: bool = False,
):
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        streaming=streaming,
    )


def _build_chat_client_for_name(client_name: str):
    if ChatOpenAI is None:
        return MissingDependencyClient("langchain_openai")

    missing_settings = _missing_client_config(client_name)
    if missing_settings:
        return MissingConfigClient(client_name, missing_settings, _configured_model_name(client_name))

    if client_name == "premium_llm":
        return _build_chat_client(
            model=settings.PREMIUM_LLM_MODEL,
            api_key=settings.PREMIUM_LLM_API_KEY,
            base_url=settings.PREMIUM_LLM_API_BASE_URL,
            timeout=600,
            max_retries=5,
            streaming=False,
        )

    if client_name == "default_vlm":
        return _build_chat_client(
            model=settings.DEFAULT_VLM_MODEL,
            api_key=settings.DEFAULT_VLM_API_KEY,
            base_url=settings.DEFAULT_VLM_API_BASE_URL,
            timeout=300,
            max_retries=5,
            streaming=False,
        )

    return _build_chat_client(
        model=settings.DEFAULT_LLM_MODEL,
        api_key=settings.DEFAULT_LLM_API_KEY,
        base_url=settings.DEFAULT_LLM_API_BASE_URL,
        timeout=600,
        max_retries=5,
        streaming=False,
    )


if OpenAIEmbeddings is None:
    embedding_llm = MissingDependencyClient("langchain_openai")
else:
    embedding_llm = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        openai_api_base=settings.EMBEDDING_API_BASE_URL,
        openai_api_key=settings.EMBEDDING_API_KEY,
    )

premium_llm = _ClientHandle("premium_llm")
default_llm = _ClientHandle("default_llm")
default_vlm = _ClientHandle("default_vlm")


def _build_invoke_error(model_name: str, schema_name: str, last_error: Exception | None) -> LLMInvokeError:
    if last_error is None:
        return LLMInvokeError(
            f"LLM invoke failed for model={model_name}, schema={schema_name or 'plain_text'}: unknown error"
        )

    hint = _infer_llm_error_hint(last_error)
    detail = f" {hint}" if hint else ""
    return LLMInvokeError(
        f"LLM invoke exhausted retries for model={model_name}, schema={schema_name or 'plain_text'}: "
        f"{last_error}.{detail}"
    )


async def _invoke_with_retries(
    raw_client: Any,
    args: Any,
    *,
    config: Any = None,
    pydantic_schema: Any = None,
    json_schema: Any = None,
    schema_name: str = "",
    kind: ModelKind = ModelKind.LLM,
):
    llm = raw_client
    effective_schema_name = schema_name
    if pydantic_schema:
        json_schema = pydantic_schema.model_json_schema()
        effective_schema_name = getattr(pydantic_schema, "__name__", str(pydantic_schema))
        llm = llm.with_structured_output(
            pydantic_schema, include_raw=True, method="json_schema"
        )
    elif json_schema:
        effective_schema_name = "json_schema"

    model_name = _client_model_name(raw_client)
    last_error: Exception | None = None
    for attempt in range(1, MAX_INVOKE_ATTEMPTS + 1):
        try:
            response = await llm.ainvoke(args, config=config)
            if pydantic_schema:
                if not response["parsing_error"]:
                    logger.debug(response["parsed"])
                    return response["parsed"]

                raw_msg = response.get("raw")
                content = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)
                json_info = repair_json(
                    content, ensure_ascii=False, return_objects=True
                )
                logger.debug(json.dumps(json_info, indent=4, ensure_ascii=False))
                validate(instance=json_info, schema=json_schema)
                return pydantic_schema(**json_info)

            if json_schema:
                json_info = repair_json(
                    response.content, ensure_ascii=False, return_objects=True
                )
                validate(instance=json_info, schema=json_schema)
                logger.debug(json.dumps(json_info, indent=4, ensure_ascii=False))
                return json_info

            return response.content
        except Exception as error:
            last_error = error
            if pydantic_schema:
                err_text = str(error).lower()
                if "response_format" in err_text or "json_schema" in err_text or "invalid_request_error" in err_text:
                    try:
                        response = await raw_client.ainvoke(args, config=config)
                        json_info = repair_json(
                            response.content, ensure_ascii=False, return_objects=True
                        )
                        validate(instance=json_info, schema=json_schema)
                        logger.debug(json.dumps(json_info, indent=4, ensure_ascii=False))
                        return pydantic_schema(**json_info)
                    except Exception:
                        pass

            import traceback

            logger.debug(
                f"{_kind_display_name(kind)} invoke attempt {attempt}/{MAX_INVOKE_ATTEMPTS} "
                f"failed for model={model_name}, schema={effective_schema_name or 'plain_text'}: {error}"
            )
            logger.debug(traceback.format_exc())
            if attempt < MAX_INVOKE_ATTEMPTS:
                await asyncio.sleep(RETRY_SLEEP_SECONDS)

    invoke_error = _build_invoke_error(model_name, effective_schema_name, last_error)
    logger.error(str(invoke_error))
    raise invoke_error


async def _raw_ainvoke_with_retries(
    raw_client: Any,
    args: Any,
    *,
    config: Any = None,
    schema_name: str = "plain_text",
    kind: ModelKind = ModelKind.LLM,
):
    model_name = _client_model_name(raw_client)
    last_error: Exception | None = None
    for attempt in range(1, MAX_INVOKE_ATTEMPTS + 1):
        try:
            response = await raw_client.ainvoke(args, config=config)
            return response
        except Exception as error:
            last_error = error
            import traceback

            logger.debug(
                f"{_kind_display_name(kind)} raw invoke attempt {attempt}/{MAX_INVOKE_ATTEMPTS} "
                f"failed for model={model_name}, schema={schema_name}: {error}"
            )
            logger.debug(traceback.format_exc())
            if attempt < MAX_INVOKE_ATTEMPTS:
                await asyncio.sleep(RETRY_SLEEP_SECONDS)

    invoke_error = _build_invoke_error(model_name, schema_name, last_error)
    logger.error(str(invoke_error))
    raise invoke_error


def _resolve_routed_client(kind: ModelKind, route: ModelRoute) -> dict[str, Any]:
    mode = settings.get_slidea_mode()
    default_client = default_llm if kind == ModelKind.LLM else default_vlm
    default_model_name = _client_model_name(default_client)

    resolution = {
        "client": default_client,
        "primary_model": default_model_name,
        "fallback_client": None,
        "fallback_model": "",
        "warning": "",
    }

    if mode == "ECONOMIC" or route == ModelRoute.DEFAULT:
        return resolution

    premium_model_name = _client_model_name(premium_llm)
    if not settings.has_premium_llm_api_key():
        resolution["warning"] = (
            f"SLIDEA_MODE=PREMIUM but PREMIUM_LLM_API_KEY is empty. "
            f"Falling back to ECONOMIC mode for {_kind_display_name(kind)} calls and using {default_model_name}."
        )
        return resolution

    if not settings.has_premium_llm_config():
        resolution["warning"] = (
            f"SLIDEA_MODE=PREMIUM but PREMIUM_LLM settings are incomplete. "
            f"Falling back to ECONOMIC mode for {_kind_display_name(kind)} calls and using {default_model_name}."
        )
        return resolution

    resolution["client"] = premium_llm
    resolution["primary_model"] = premium_model_name
    resolution["fallback_client"] = default_client
    resolution["fallback_model"] = default_model_name
    return resolution


def _has_default_client_config(kind: ModelKind) -> bool:
    if kind == ModelKind.VLM:
        return settings.has_default_vlm_config()
    return settings.has_default_llm_config()


def can_invoke_route(kind: ModelKind, route: ModelRoute | str) -> bool:
    normalized_route = _normalize_model_route(route)
    if settings.get_slidea_mode() == "ECONOMIC" or normalized_route == ModelRoute.DEFAULT:
        return _has_default_client_config(kind)
    if not settings.has_premium_llm_api_key():
        return _has_default_client_config(kind)
    return settings.has_premium_llm_config() or _has_default_client_config(kind)


def can_vlm_invoke_route(route: ModelRoute | str) -> bool:
    return can_invoke_route(ModelKind.VLM, route)


async def _execute_routed_invoke(
    kind: ModelKind,
    route: ModelRoute | str,
    *,
    invoke_func,
    invoke_kwargs: dict[str, Any],
):
    normalized_route = _normalize_model_route(route)
    resolution = _resolve_routed_client(kind, normalized_route)
    if resolution["warning"]:
        logger.warning(resolution["warning"])

    try:
        return await invoke_func(
            resolution["client"],
            kind=kind,
            **invoke_kwargs,
        )
    except Exception as primary_error:
        fallback_client = resolution["fallback_client"]
        if fallback_client is None:
            raise

        fallback_model = resolution["fallback_model"] or _client_model_name(fallback_client)
        logger.warning(
            f"{_kind_display_name(kind)} premium call failed for model={resolution['primary_model']}. "
            f"Fallback to {fallback_model}. Error: {primary_error}"
        )
        try:
            return await invoke_func(
                fallback_client,
                kind=kind,
                **invoke_kwargs,
            )
        except Exception as fallback_error:
            raise LLMInvokeError(
                f"{_kind_display_name(kind)} premium call failed and fallback also failed. "
                f"Primary model={resolution['primary_model']}; fallback model={fallback_model}. "
                f"Primary error: {primary_error}; fallback error: {fallback_error}"
            ) from fallback_error


async def _raw_ainvoke_routed_client(
    kind: ModelKind,
    route: ModelRoute | str,
    args: Any,
    *,
    config: Any = None,
    schema_name: str = "plain_text",
):
    return await _execute_routed_invoke(
        kind,
        route,
        invoke_func=_raw_ainvoke_with_retries,
        invoke_kwargs={
            "args": args,
            "config": config,
            "schema_name": schema_name,
        },
    )


def get_llm_by_route(route: ModelRoute | str):
    normalized_route = _normalize_model_route(route)
    return _resolve_routed_client(ModelKind.LLM, normalized_route)["client"]


async def llm_invoke(route_or_client, args, config=None, pydantic_schema=None, json_schema=None):
    """统一的文本模型调用接口。"""

    if isinstance(route_or_client, (ModelRoute, str)):
        return await _execute_routed_invoke(
            ModelKind.LLM,
            route_or_client,
            invoke_func=_invoke_with_retries,
            invoke_kwargs={
                "args": args,
                "config": config,
                "pydantic_schema": pydantic_schema,
                "json_schema": json_schema,
            },
        )

    return await _invoke_with_retries(
        route_or_client,
        args,
        config=config,
        pydantic_schema=pydantic_schema,
        json_schema=json_schema,
        kind=ModelKind.LLM,
    )


async def vlm_invoke(route_or_client, args, config=None, pydantic_schema=None, json_schema=None):
    """统一的视觉模型调用接口。"""

    if isinstance(route_or_client, (ModelRoute, str)):
        return await _execute_routed_invoke(
            ModelKind.VLM,
            route_or_client,
            invoke_func=_invoke_with_retries,
            invoke_kwargs={
                "args": args,
                "config": config,
                "pydantic_schema": pydantic_schema,
                "json_schema": json_schema,
            },
        )

    return await _invoke_with_retries(
        route_or_client,
        args,
        config=config,
        pydantic_schema=pydantic_schema,
        json_schema=json_schema,
        kind=ModelKind.VLM,
    )


async def vlm_raw_invoke(route: ModelRoute | str, args, config=None, schema_name="plain_text"):
    """视觉模型原始调用接口。"""

    return await _raw_ainvoke_routed_client(
        ModelKind.VLM,
        route,
        args,
        config=config,
        schema_name=schema_name,
    )
