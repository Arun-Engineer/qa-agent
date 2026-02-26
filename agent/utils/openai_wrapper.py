# agent/utils/openai_wrapper.py
import os
import time
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI

from puvinoise import bootstrap, instrument_openai_call


# -------- Noob-friendly defaults --------
# 1) Loads .env locally (ignored in ECS unless you bake it in)
load_dotenv()

# 2) By default: DO NOT try to export to OTLP (avoids /v1/traces 404 spam)
#    We'll do console/file style observability instead.
os.environ.setdefault("OTEL_TRACES_EXPORTER", "none")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")
os.environ.setdefault("OTEL_LOGS_EXPORTER", "none")

# 3) If user *explicitly* sets an OTLP endpoint, then enable OTLP export
#    Example: set PUVINOISE_OTLP_ENDPOINT=http://localhost:4318
otlp = os.getenv("PUVINOISE_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
if otlp:
    os.environ["OTEL_TRACES_EXPORTER"] = "otlp"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp
    os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")


# -------- Internal state --------
_BOOTSTRAPPED = False
_CLIENT: Optional[OpenAI] = None

# Local “observability” log (works everywhere, including ECS)
LOG_DIR = Path(os.getenv("OBS_LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
OBS_LOG = LOG_DIR / "openai_calls.jsonl"


def _log_event(event: Dict[str, Any]) -> None:
    try:
        with OBS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # never crash app because logging failed
        pass


def get_client(service_name: str = "basic-openai-agent") -> OpenAI:
    """One OpenAI client per process."""
    global _BOOTSTRAPPED, _CLIENT

    if _CLIENT is not None:
        return _CLIENT

    if not _BOOTSTRAPPED:
        bootstrap(service_name=service_name)
        _BOOTSTRAPPED = True

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing. Set it in env or .env")

    _CLIENT = OpenAI(api_key=api_key)
    return _CLIENT


# Backward-compat alias (so old code importing _init won’t break)
def _init(service_name: str = "basic-openai-agent") -> OpenAI:
    return get_client(service_name=service_name)


@instrument_openai_call
def call_model(
    messages: List[Dict[str, str]],
    *,
    model: str = "gpt-4o-mini",
    temperature: Optional[float] = 0,
    service_name: str = "basic-openai-agent",
    **kwargs: Any,
):
    """
    Noob-friendly: same shape as your snippet.
    Adds local jsonl logging (observability) without any collector.
    """
    client = get_client(service_name=service_name)

    started = time.time()
    try:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        resp = client.chat.completions.create(**payload)

        _log_event({
            "ok": True,
            "service": service_name,
            "model": model,
            "duration_ms": int((time.time() - started) * 1000),
            "prompt_chars": sum(len(m.get("content", "")) for m in messages),
            "response_chars": len((resp.choices[0].message.content or "")),
        })
        return resp

    except Exception as e:
        _log_event({
            "ok": False,
            "service": service_name,
            "model": model,
            "duration_ms": int((time.time() - started) * 1000),
            "error": str(e),
        })
        raise


# Generic wrapper name (so you can standardize in your codebase)
def chat_completion(
    messages: List[Dict[str, str]],
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    service_name: str = "basic-openai-agent",
    **kwargs: Any,
):
    return call_model(
        messages=messages,
        model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=temperature,
        service_name=service_name,
        **kwargs,
    )


def basic_agent(user_input: str) -> str:
    messages = [
        {"role": "system", "content": "Reply in one short sentence only."},
        {"role": "user", "content": user_input[:100]},
    ]
    resp = call_model(messages)
    return (resp.choices[0].message.content or "").strip()


if __name__ == "__main__":
    # FIXED: your snippet had `if name == "main":` which is wrong
    from puvinoise import run_with_trace
    print(run_with_trace(basic_agent, "basic-openai-agent", "What is Observability?"))