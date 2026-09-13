"""One structured-output call to Gemini, plus error mapping (architecture doc §16).

Uses the Google Gen AI SDK directly rather than a framework — §23 recommends exactly
that for V1. The SDK converts a Pydantic model into Gemini's ``response_schema``
itself, so unlike the raw-JSON-schema path this module hands it the class and lets
the SDK own the conversion.

Two transports, chosen by configuration: Vertex AI (what the design document
specifies, and what bills to the GCP project) or the Gemini Developer API key for
local work. Callers cannot tell them apart.

Every caller is expected to catch ``LLMError`` and fall back to the deterministic
engine — no single model failure may take a run down (§18).
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: Any = None

def _thinking_budget(effort: str | None) -> int:
    """Resolve an effort tier to a thinking budget.

    Every tier reads configuration. "medium" used to return a hardcoded 4096, which
    quietly exempted the reranker — the one stage that asks for it — from the
    determinism settings: temperature and seed were pinned while its reasoning trace
    was still free to vary. Nothing is exempt now.

    Extraction keeps its own budget because it is the high-volume, low-judgement
    stage and is worth tuning separately from the reasoning stages.
    """
    if effort == "low":
        return settings.extraction_thinking_budget
    return settings.recruiter_thinking_budget


class LLMError(RuntimeError):
    """Any reason a model call did not produce usable structured output."""


class LLMRefusal(LLMError):
    """The model declined the request (finish_reason SAFETY / PROHIBITED_CONTENT)."""


def llm_available() -> bool:
    return settings.llm_available


def _get_client() -> Any:
    global _client
    if _client is None:
        from google import genai

        if settings.google_genai_use_vertexai:
            # Credentials resolve from Application Default Credentials — never
            # hardcoded. `gcloud auth application-default login` locally, or the
            # Cloud Run service account in deployment (§19).
            _client = genai.Client(
                vertexai=True,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
            )
        else:
            _client = genai.Client(api_key=settings.google_api_key)
    return _client


def harden_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic-generated JSON schema acceptable as a Gemini response schema.

    Gemini accepts an OpenAPI 3.0 subset. It rejects ``additionalProperties``,
    ``$defs``/``$ref`` and the annotation keys Pydantic emits, so this inlines
    references and strips what is not in the subset.

    Kept public because the reranker and the parsers build schemas by hand and need
    the same treatment, and because it is directly unit-tested.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node

        if "$ref" in node:
            ref = node["$ref"].rsplit("/", 1)[-1]
            return resolve(defs.get(ref, {}))

        out: dict[str, Any] = {}
        for key, value in node.items():
            # Not part of the OpenAPI subset Gemini accepts.
            if key in ("$defs", "additionalProperties", "title", "default", "examples"):
                continue
            if key == "properties" and isinstance(value, dict):
                # Keys inside `properties` are field names, not schema keywords. A
                # blanket strip here would delete a field genuinely called "title"
                # — which ParsedJobDescription has — and the model would never be
                # asked for it.
                out[key] = {field: resolve(sub) for field, sub in value.items()}
                continue
            out[key] = resolve(value)

        # anyOf carrying a null branch is Pydantic's `X | None`; Gemini expresses
        # that as nullable on the non-null branch instead.
        if "anyOf" in out:
            branches = [b for b in out["anyOf"] if b.get("type") != "null"]
            if len(branches) == 1 and len(out["anyOf"]) > 1:
                merged = dict(branches[0])
                merged["nullable"] = True
                out.pop("anyOf")
                out.update(merged)
        return out

    return resolve(schema)


def run_structured(
    *,
    system: str,
    prompt: str,
    schema: type[T],
    effort: str | None = None,
    max_tokens: int = 16_000,
    model: str | None = None,
) -> T:
    """Call Gemini once and return a validated instance of ``schema``."""
    if not llm_available():
        raise LLMError("No Gemini credentials configured.")

    from google.genai import errors as genai_errors
    from google.genai import types

    budget = _thinking_budget(effort)
    target_model = model or settings.recruiter_model

    config = types.GenerateContentConfig(
        system_instruction=system,
        # Greedy decoding. The scoring engine is only reproducible if the category
        # scores feeding it are, and the API default (~1.0) samples randomly.
        temperature=settings.llm_temperature,
        # Temperature 0 plus a fixed seed removes the sampling variance. A thinking
        # model can still vary run to run — the reasoning trace is not pinned by
        # either — so identical scores are likely, not guaranteed. The screening
        # record is the real reproducibility guarantee: it stores the evidence and
        # the numbers, and is never recomputed.
        seed=settings.llm_seed if settings.llm_seed >= 0 else None,
        response_mime_type="application/json",
        # The SDK converts the Pydantic model into Gemini's schema dialect, which is
        # more reliable than hand-converting it here.
        response_schema=schema,
        max_output_tokens=max_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=budget),
    )

    try:
        response = _get_client().models.generate_content(
            model=target_model,
            contents=prompt,
            config=config,
        )
    except genai_errors.ClientError as exc:
        # 401/403 land here too; the message carries which.
        raise LLMError(f"Gemini rejected the request: {exc}") from exc
    except genai_errors.ServerError as exc:
        raise LLMError(f"Gemini server error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - transport failures must not escape
        raise LLMError(f"Could not reach Gemini: {exc}") from exc

    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish = str(getattr(candidates[0], "finish_reason", "") or "")
        if "SAFETY" in finish or "PROHIBITED" in finish or "BLOCKLIST" in finish:
            raise LLMRefusal(f"The model declined this request ({finish}).")
        if "MAX_TOKENS" in finish:
            raise LLMError("Model output hit max_output_tokens before the schema was complete.")

    text = getattr(response, "text", None)
    if not text:
        raise LLMError("Model returned no text to parse.")

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        logger.debug(
            "llm call model=%s in=%s out=%s thoughts=%s",
            target_model,
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
            getattr(usage, "thoughts_token_count", "?"),
        )

    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        raise LLMError(f"Model output did not match {schema.__name__}: {exc}") from exc
