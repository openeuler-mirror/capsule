import asyncio

import json
from core.utils.logger import logger
from json_repair import repair_json
from jsonschema import validate

try:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
except ImportError:  # pragma: no cover - exercised in minimal runtime environments
    ChatOpenAI = None
    OpenAIEmbeddings = None
from core.utils.config import settings


class LLMInvokeError(RuntimeError):
    """Raised when an LLM call exhausts retries."""


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


class MissingDependencyClient:
    def __init__(self, dependency_name: str):
        self.dependency_name = dependency_name

    async def ainvoke(self, *_args, **_kwargs):
        raise RuntimeError(
            f"Missing optional dependency '{self.dependency_name}'. "
            "Install requirements.txt to enable LLM calls."
        )

    def with_structured_output(self, *_args, **_kwargs):
        return self


if OpenAIEmbeddings is None:
    embedding_llm = MissingDependencyClient("langchain_openai")
    default_llm = MissingDependencyClient("langchain_openai")
    default_vlm = MissingDependencyClient("langchain_openai")
else:
    embedding_llm = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        openai_api_base=settings.EMBEDDING_API_BASE_URL,
        openai_api_key=settings.EMBEDDING_API_KEY,
    )

    default_llm = ChatOpenAI(
        model=settings.DEFAULT_LLM_MODEL,
        api_key=settings.DEFAULT_LLM_API_KEY,
        base_url=settings.DEFAULT_LLM_API_BASE_URL,
        timeout=600,
        max_retries=5,
        streaming=False,
    )

    default_vlm = ChatOpenAI(
        model=settings.DEFAULT_VLM_MODEL,
        api_key=settings.DEFAULT_VLM_API_KEY,
        base_url=settings.DEFAULT_VLM_API_BASE_URL,
        timeout=300,
        max_retries=5,
    )


async def llm_invoke(llm, args, config=None, pydantic_schema=None, json_schema=None):
    """统一的LLM调用接口"""

    raw_llm = llm
    schema_name = ""
    if pydantic_schema:
        json_schema = pydantic_schema.model_json_schema()
        schema_name = getattr(pydantic_schema, "__name__", str(pydantic_schema))
        llm = llm.with_structured_output(
            pydantic_schema, include_raw=True, method="json_schema"
        )
    elif json_schema:
        schema_name = "json_schema"

    llm_model = getattr(raw_llm, "model_name", None) or getattr(raw_llm, "model", "") or settings.DEFAULT_LLM_MODEL or "unknown"
    last_error: Exception | None = None

    for attempt in range(1, 6):
        try:
            response = await llm.ainvoke(args, config=config)
            if pydantic_schema:
                if not response["parsing_error"]:
                    logger.debug(response["parsed"])
                    return response["parsed"]
                else:
                    raw_msg = response.get("raw")
                    content = (
                        raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)
                    )
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
        except Exception as e:
            last_error = e
            # fallback: providers that don't support response_format
            if pydantic_schema:
                err_text = str(e).lower()
                if "response_format" in err_text or "json_schema" in err_text or "invalid_request_error" in err_text:
                    try:
                        response = await raw_llm.ainvoke(args, config=config)
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
                f"LLM invoke attempt {attempt}/5 failed for model={llm_model}, schema={schema_name or 'plain_text'}: {e}"
            )
            logger.debug(traceback.format_exc())
        await asyncio.sleep(10)

    if last_error is None:
        logger.error(f"LLM invoke failed for model={llm_model}, schema={schema_name or 'plain_text'}: unknown error")
        return None

    hint = _infer_llm_error_hint(last_error)
    detail = f" {hint}" if hint else ""
    logger.error(
        f"LLM invoke exhausted retries for model={llm_model}, schema={schema_name or 'plain_text'}: {last_error}.{detail}"
    )
    return None
