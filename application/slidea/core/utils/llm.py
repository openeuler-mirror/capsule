import asyncio
import json
from dataclasses import dataclass
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
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

from core.utils.config import settings

MAX_INVOKE_ATTEMPTS = 5
RETRY_SLEEP_SECONDS = 10
SLIDEA_AGENT_ID = "slidea"
AGENT_PROFILE_AGENT_HEADER = "X-Agent-Id"
AGENT_PROFILE_WORK_NODE_HEADER = "X-Work-Node"


class LLMInvokeError(RuntimeError):
    """Raised when an LLM or VLM call exhausts retries."""


class ModelRoute(str, Enum):
    DEFAULT = "default"
    PREMIUM = "premium"


class ModelKind(str, Enum):
    LLM = "llm"
    VLM = "vlm"


@dataclass(frozen=True)
class _ChatClientConfig:
    display_model_name: str
    request_model_name: str
    api_key: str
    base_url: str
    timeout: int
    max_retries: int
    streaming: bool
    missing_settings: list[str]


@dataclass(frozen=True)
class _RouteResolution:
    client_name: str
    fallback_client_name: str | None = None
    warning: str = ""


@dataclass
class InvokeOptions:
    config: Any = None
    pydantic_schema: Any = None
    json_schema: Any = None
    work_node: str | None = None


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


def _is_non_retryable_invalid_image_error(error: Exception) -> bool:
    """Identify deterministic invalid-image requests that retries cannot fix."""
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code != 400:
        return False

    message = str(error).lower()
    image_markers = ("image data", "image_url", "invalid image", "invalid_image")
    invalid_markers = ("invalid", "valid image", "unsupported", "cannot decode")
    return any(marker in message for marker in image_markers) and any(
        marker in message for marker in invalid_markers
    )


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


class _RequestHeaderClient:
    def __init__(self, client: Any, headers: dict[str, str]):
        self._client = client
        self._headers = headers
        self.model_name = _client_model_name(client)
        self.model = self.model_name

    async def ainvoke(self, *args, **kwargs):
        extra_headers = kwargs.pop("extra_headers", None) or {}
        headers = {**self._headers, **extra_headers}
        return await self._client.ainvoke(*args, **kwargs, extra_headers=headers)

    def with_structured_output(self, *args, **kwargs):
        return _RequestHeaderClient(
            self._client.with_structured_output(*args, **kwargs),
            self._headers,
        )


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
    return _resolve_chat_client_config(client_name).display_model_name


def _resolve_handover_chat_client_config() -> _ChatClientConfig | None:
    if not settings.should_handover_model_routing():
        return None

    return _ChatClientConfig(
        display_model_name="model_service:auto",
        request_model_name="",
        api_key=settings.DEFAULT_LLM_API_KEY,
        base_url=settings.DEFAULT_LLM_API_BASE_URL,
        timeout=600,
        max_retries=5,
        streaming=False,
        missing_settings=settings.missing_default_llm_settings(),
    )


def _has_handover_chat_client_config() -> bool:
    handover_config = _resolve_handover_chat_client_config()
    return handover_config is not None and not handover_config.missing_settings


def _resolve_chat_client_config(client_name: str) -> _ChatClientConfig:
    handover_config = _resolve_handover_chat_client_config()
    if handover_config is not None:
        return handover_config

    if client_name == "premium_llm":
        return _ChatClientConfig(
            display_model_name=settings.PREMIUM_LLM_MODEL or "unconfigured:premium_llm",
            request_model_name=settings.PREMIUM_LLM_MODEL,
            api_key=settings.PREMIUM_LLM_API_KEY,
            base_url=settings.PREMIUM_LLM_API_BASE_URL,
            timeout=600,
            max_retries=5,
            streaming=False,
            missing_settings=settings.missing_premium_llm_settings(),
        )

    if client_name == "default_vlm":
        return _ChatClientConfig(
            display_model_name=settings.DEFAULT_VLM_MODEL or "unconfigured:default_vlm",
            request_model_name=settings.DEFAULT_VLM_MODEL,
            api_key=settings.DEFAULT_VLM_API_KEY,
            base_url=settings.DEFAULT_VLM_API_BASE_URL,
            timeout=300,
            max_retries=5,
            streaming=False,
            missing_settings=settings.missing_default_vlm_settings(),
        )

    return _ChatClientConfig(
        display_model_name=settings.DEFAULT_LLM_MODEL or "unconfigured:default_llm",
        request_model_name=settings.DEFAULT_LLM_MODEL,
        api_key=settings.DEFAULT_LLM_API_KEY,
        base_url=settings.DEFAULT_LLM_API_BASE_URL,
        timeout=600,
        max_retries=5,
        streaming=False,
        missing_settings=settings.missing_default_llm_settings(),
    )


def _build_chat_client(
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
    max_retries: int,
    streaming: bool = False,
    default_headers: dict[str, str] | None = None,
):
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        streaming=streaming,
        default_headers=default_headers,
    )


def _build_chat_client_for_name(client_name: str, default_headers: dict[str, str] | None = None):
    if ChatOpenAI is None:
        return MissingDependencyClient("langchain_openai")

    client_config = _resolve_chat_client_config(client_name)
    if client_config.missing_settings:
        return MissingConfigClient(client_name, client_config.missing_settings, client_config.display_model_name)

    return _build_chat_client(
        model=client_config.request_model_name,
        api_key=client_config.api_key,
        base_url=client_config.base_url,
        timeout=client_config.timeout,
        max_retries=client_config.max_retries,
        streaming=client_config.streaming,
        default_headers=default_headers,
    )


premium_llm = _ClientHandle("premium_llm")
default_llm = _ClientHandle("default_llm")
default_vlm = _ClientHandle("default_vlm")


def _build_handover_work_node_headers(work_node: str | None) -> dict[str, str]:
    if _resolve_handover_chat_client_config() is None or work_node is None:
        return {}
    normalized_work_node = str(work_node).strip()
    if not normalized_work_node:
        return {}
    return {
        AGENT_PROFILE_AGENT_HEADER: SLIDEA_AGENT_ID,
        AGENT_PROFILE_WORK_NODE_HEADER: normalized_work_node,
    }


def _build_model_invoke_headers(client: Any, work_node: str | None) -> dict[str, str]:
    return _build_handover_work_node_headers(work_node)


def _with_model_request_headers(client: Any, work_node: str | None):
    headers = _build_model_invoke_headers(client, work_node)
    if not headers:
        return client

    if isinstance(client, _ClientHandle):
        return _RequestHeaderClient(client, headers)

    bind = getattr(client, "bind", None)
    if callable(bind):
        return bind(extra_headers=headers)

    return _RequestHeaderClient(client, headers)


def _build_invoke_error(model_name: str, schema_name: str, last_error: Exception | None) -> LLMInvokeError:
    schema_detail = f", schema={schema_name}" if schema_name else ""
    if last_error is None:
        return LLMInvokeError(
            f"LLM invoke failed for model={model_name}{schema_detail}: unknown error"
        )

    hint = _infer_llm_error_hint(last_error)
    detail = f" {hint}" if hint else ""
    return LLMInvokeError(
        f"LLM invoke exhausted retries for model={model_name}{schema_detail}: "
        f"{last_error}.{detail}"
    )


def _response_content(response: Any) -> Any:
    return response.content if hasattr(response, "content") else response


def _schema_json(pydantic_schema: Any) -> Any:
    if hasattr(pydantic_schema, "model_json_schema"):
        return pydantic_schema.model_json_schema()
    return None


def _validate_pydantic_schema(pydantic_schema: Any, json_info: Any):
    if hasattr(pydantic_schema, "model_validate"):
        return pydantic_schema.model_validate(json_info)
    return pydantic_schema(**json_info)


def _parse_json_response_content(content: Any, json_schema: Any = None) -> Any:
    if isinstance(content, (dict, list)):
        json_info = content
    else:
        json_info = repair_json(str(content), ensure_ascii=False, return_objects=True)
    if json_schema:
        validate(instance=json_info, schema=json_schema)
    logger.debug(json.dumps(json_info, indent=4, ensure_ascii=False, default=str))
    return json_info


async def _invoke_with_retries(
    raw_client: Any,
    args: Any,
    *,
    config: Any = None,
    pydantic_schema: Any = None,
    json_schema: Any = None,
    schema_name: str = "",
    kind: ModelKind = ModelKind.LLM,
    work_node: str | None = None,
):
    llm = _with_model_request_headers(raw_client, work_node)
    if pydantic_schema:
        json_schema = _schema_json(pydantic_schema)

    model_name = _client_model_name(raw_client)
    last_error: Exception | None = None
    for attempt in range(1, MAX_INVOKE_ATTEMPTS + 1):
        try:
            response = await llm.ainvoke(args, config=config)
            content = _response_content(response)
            if pydantic_schema:
                json_info = _parse_json_response_content(content, json_schema)
                return _validate_pydantic_schema(pydantic_schema, json_info)

            if json_schema:
                return _parse_json_response_content(content, json_schema)

            return content
        except Exception as error:
            last_error = error
            import traceback

            logger.debug(
                f"{_kind_display_name(kind)} invoke attempt {attempt}/{MAX_INVOKE_ATTEMPTS} "
                f"failed for model={model_name}: {error}"
            )
            logger.debug(traceback.format_exc())
            if _is_non_retryable_invalid_image_error(error):
                logger.warning("Invalid VLM image request is not retryable; aborting retries.")
                break
            if attempt < MAX_INVOKE_ATTEMPTS:
                await asyncio.sleep(RETRY_SLEEP_SECONDS)

    invoke_error = _build_invoke_error(model_name, schema_name, last_error)
    logger.error(str(invoke_error))
    raise invoke_error


async def _raw_ainvoke_with_retries(
    raw_client: Any,
    args: Any,
    *,
    config: Any = None,
    schema_name: str = "plain_text",
    kind: ModelKind = ModelKind.LLM,
    work_node: str | None = None,
):
    model_name = _client_model_name(raw_client)
    client = _with_model_request_headers(raw_client, work_node)
    last_error: Exception | None = None
    for attempt in range(1, MAX_INVOKE_ATTEMPTS + 1):
        try:
            response = await client.ainvoke(args, config=config)
            return response
        except Exception as error:
            last_error = error
            import traceback

            logger.debug(
                f"{_kind_display_name(kind)} raw invoke attempt {attempt}/{MAX_INVOKE_ATTEMPTS} "
                f"failed for model={model_name}, schema={schema_name}: {error}"
            )
            logger.debug(traceback.format_exc())
            if _is_non_retryable_invalid_image_error(error):
                logger.warning("Invalid VLM image request is not retryable; aborting retries.")
                break
            if attempt < MAX_INVOKE_ATTEMPTS:
                await asyncio.sleep(RETRY_SLEEP_SECONDS)

    invoke_error = _build_invoke_error(model_name, schema_name, last_error)
    logger.error(str(invoke_error))
    raise invoke_error


def _default_client_name(kind: ModelKind) -> str:
    return "default_vlm" if kind == ModelKind.VLM else "default_llm"


def _client_handle_for_name(client_name: str):
    if client_name == "premium_llm":
        return premium_llm
    if client_name == "default_vlm":
        return default_vlm
    return default_llm


def _resolve_route_resolution(kind: ModelKind, route: ModelRoute) -> _RouteResolution:
    if _resolve_handover_chat_client_config() is not None:
        return _RouteResolution(client_name="default_llm")

    mode = settings.get_slidea_mode()
    default_client_name = _default_client_name(kind)
    default_client = _client_handle_for_name(default_client_name)
    default_model_name = _client_model_name(default_client)

    if mode == "ECONOMIC" or route == ModelRoute.DEFAULT:
        return _RouteResolution(client_name=default_client_name)

    if not settings.has_premium_llm_api_key():
        return _RouteResolution(
            client_name=default_client_name,
            warning=(
                f"SLIDEA_MODE=PREMIUM but PREMIUM_LLM_API_KEY is empty. "
                f"Falling back to ECONOMIC mode for {_kind_display_name(kind)} calls and using {default_model_name}."
            ),
        )

    if not settings.has_premium_llm_config():
        return _RouteResolution(
            client_name=default_client_name,
            warning=(
                f"SLIDEA_MODE=PREMIUM but PREMIUM_LLM settings are incomplete. "
                f"Falling back to ECONOMIC mode for {_kind_display_name(kind)} calls and using {default_model_name}."
            ),
        )

    return _RouteResolution(
        client_name="premium_llm",
        fallback_client_name=default_client_name,
    )


def _resolve_routed_client(kind: ModelKind, route: ModelRoute) -> dict[str, Any]:
    route_resolution = _resolve_route_resolution(kind, route)
    client = _client_handle_for_name(route_resolution.client_name)
    fallback_client = (
        _client_handle_for_name(route_resolution.fallback_client_name)
        if route_resolution.fallback_client_name
        else None
    )
    return {
        "client": client,
        "primary_model": _client_model_name(client),
        "fallback_client": fallback_client,
        "fallback_model": _client_model_name(fallback_client) if fallback_client else "",
        "warning": route_resolution.warning,
    }


def _has_default_client_config(kind: ModelKind) -> bool:
    if _resolve_handover_chat_client_config() is not None:
        return _has_handover_chat_client_config()
    if kind == ModelKind.VLM:
        return settings.has_default_vlm_config()
    return settings.has_default_llm_config()


def can_invoke_route(kind: ModelKind, route: ModelRoute | str) -> bool:
    if _resolve_handover_chat_client_config() is not None:
        return _has_handover_chat_client_config()

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
    work_node: str | None = None,
):
    return await _execute_routed_invoke(
        kind,
        route,
        invoke_func=_raw_ainvoke_with_retries,
        invoke_kwargs={
            "args": args,
            "config": config,
            "schema_name": schema_name,
            "work_node": work_node,
        },
    )


def get_llm_by_route(route: ModelRoute | str):
    normalized_route = _normalize_model_route(route)
    return _resolve_routed_client(ModelKind.LLM, normalized_route)["client"]


async def llm_invoke(
    route_or_client,
    args,
    options: InvokeOptions | None = None,
):
    """统一的文本模型调用接口。"""

    opts = options or InvokeOptions()

    if isinstance(route_or_client, (ModelRoute, str)):
        return await _execute_routed_invoke(
            ModelKind.LLM,
            route_or_client,
            invoke_func=_invoke_with_retries,
            invoke_kwargs={
                "args": args,
                "config": opts.config,
                "pydantic_schema": opts.pydantic_schema,
                "json_schema": opts.json_schema,
                "work_node": opts.work_node,
            },
        )

    return await _invoke_with_retries(
        route_or_client,
        args,
        config=opts.config,
        pydantic_schema=opts.pydantic_schema,
        json_schema=opts.json_schema,
        kind=ModelKind.LLM,
        work_node=opts.work_node,
    )


async def vlm_invoke(
    route_or_client,
    args,
    options: InvokeOptions | None = None,
):
    """统一的视觉模型调用接口。"""

    opts = options or InvokeOptions()

    if isinstance(route_or_client, (ModelRoute, str)):
        return await _execute_routed_invoke(
            ModelKind.VLM,
            route_or_client,
            invoke_func=_invoke_with_retries,
            invoke_kwargs={
                "args": args,
                "config": opts.config,
                "pydantic_schema": opts.pydantic_schema,
                "json_schema": opts.json_schema,
                "work_node": opts.work_node,
            },
        )

    return await _invoke_with_retries(
        route_or_client,
        args,
        config=opts.config,
        pydantic_schema=opts.pydantic_schema,
        json_schema=opts.json_schema,
        kind=ModelKind.VLM,
        work_node=opts.work_node,
    )


async def vlm_raw_invoke(
    route: ModelRoute | str,
    args,
    config=None,
    schema_name="plain_text",
    work_node=None,
):
    """视觉模型原始调用接口。"""

    return await _raw_ainvoke_routed_client(
        ModelKind.VLM,
        route,
        args,
        config=config,
        schema_name=schema_name,
        work_node=work_node,
    )
