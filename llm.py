"""Unified LLM layer: Gemini primary, Groq failover, provider-agnostic tool calling.

Public API
----------
    chat(messages, tools=None) -> dict

`messages` is an OpenAI-ish list:
    [{"role": "system"|"user"|"assistant"|"tool", "content": "..."}]
`tools` is a list of plain JSON schemas:
    [{"name": ..., "description": ..., "parameters": {...json schema...}}]

Returns:
    {
      "provider": "gemini" | "groq" | "none",
      "text": str,                                  # assistant text ("" if only a tool call)
      "tool_calls": [{"name": str, "args": dict}],  # empty list if none
      "error": str | None,                          # set only when every provider failed
    }

Never raises. Worst case you get provider="none" and an error string.
"""

import json
import os
import traceback

from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# This Groq account exposes gpt-oss / qwen, not Llama-3.3. Override via GROQ_MODEL.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

# Flip to True (or set FORCE_GEMINI_FAIL=1) to prove the failover path in a demo.
FORCE_GEMINI_FAIL = os.getenv("FORCE_GEMINI_FAIL", "0") == "1"

# Every chat() result is appended here so the UI can show which provider served what.
CALL_LOG = []

_gemini_client = None
_groq_client = None


def _log(msg):
    print(f"[llm] {msg}", flush=True)


# --------------------------------------------------------------------------
# clients (lazy, so a missing key never blocks import)
# --------------------------------------------------------------------------
def get_gemini():
    global _gemini_client
    if _gemini_client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY missing")
        from google import genai

        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def get_groq():
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY missing")
        from groq import Groq

        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# --------------------------------------------------------------------------
# schema normalization
# --------------------------------------------------------------------------
_ALLOWED = {"type", "description", "properties", "required", "items", "enum"}


def _clean_schema(schema):
    """Strip JSON-Schema keys Gemini rejects (additionalProperties, $schema, etc.)."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k not in _ALLOWED:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _clean_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _clean_schema(v)
        else:
            out[k] = v
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def _split_system(messages):
    """Pull system messages out into one string; return (system_text, rest)."""
    sys_parts = [m["content"] for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return "\n\n".join(sys_parts).strip(), rest


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
def _call_gemini(messages, tools):
    from google.genai import types

    client = get_gemini()
    system_text, rest = _split_system(messages)

    contents = []
    for m in rest:
        # Tool results are folded into a user turn -- keeps both providers happy
        # without any tool_call_id bookkeeping.
        role = "model" if m.get("role") == "assistant" else "user"
        text = m.get("content") or ""
        if m.get("role") == "tool":
            text = f"TOOL RESULT ({m.get('name', 'tool')}):\n{text}"
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))

    cfg = {
        "temperature": 0.3,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if system_text:
        cfg["system_instruction"] = system_text
    if tools:
        cfg["tools"] = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=_clean_schema(t.get("parameters", {"type": "object"})),
                    )
                    for t in tools
                ]
            )
        ]

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(**cfg),
    )

    text_bits, tool_calls = [], []
    for cand in resp.candidates or []:
        for part in (getattr(cand.content, "parts", None) or []):
            if getattr(part, "text", None):
                text_bits.append(part.text)
            fc = getattr(part, "function_call", None)
            if fc:
                tool_calls.append({"name": fc.name, "args": dict(fc.args or {})})

    return {"text": "\n".join(text_bits).strip(), "tool_calls": tool_calls}


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------
def _call_groq(messages, tools):
    client = get_groq()

    msgs = []
    for m in messages:
        role = m.get("role", "user")
        text = m.get("content") or ""
        if role == "tool":
            role, text = "user", f"TOOL RESULT ({m.get('name', 'tool')}):\n{text}"
        msgs.append({"role": role, "content": text})

    kwargs = {"model": GROQ_MODEL, "messages": msgs, "temperature": 0.3}
    if tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]
        kwargs["tool_choice"] = "auto"

    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message

    tool_calls = []
    for tc in (msg.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
        except Exception:
            args = {"_raw": tc.function.arguments}
        tool_calls.append({"name": tc.function.name, "args": args})

    return {"text": (msg.content or "").strip(), "tool_calls": tool_calls}


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------
def chat(messages, tools=None):
    """Gemini first; on ANY Gemini failure fall through to Groq. Never raises."""
    errors = []

    if GEMINI_API_KEY:
        try:
            if FORCE_GEMINI_FAIL:
                raise RuntimeError("FORCE_GEMINI_FAIL is on -- skipping Gemini")
            out = _call_gemini(messages, tools)
            out["provider"] = "gemini"
            out["error"] = None
            _log(f"served by GEMINI ({GEMINI_MODEL})")
            CALL_LOG.append("gemini")
            return out
        except Exception as e:
            errors.append(f"gemini: {type(e).__name__}: {e}")
            _log(f"Gemini failed ({type(e).__name__}: {e}) -> failing over to Groq")
    else:
        errors.append("gemini: GEMINI_API_KEY missing")
        _log("GEMINI_API_KEY missing -> going straight to Groq")

    if GROQ_API_KEY:
        try:
            out = _call_groq(messages, tools)
            out["provider"] = "groq"
            out["error"] = None
            _log(f"served by GROQ ({GROQ_MODEL})")
            CALL_LOG.append("groq")
            return out
        except Exception as e:
            errors.append(f"groq: {type(e).__name__}: {e}")
            _log(f"Groq failed too: {type(e).__name__}: {e}")
            traceback.print_exc()
    else:
        errors.append("groq: GROQ_API_KEY missing")

    return {
        "provider": "none",
        "text": "",
        "tool_calls": [],
        "error": " | ".join(errors),
    }


if __name__ == "__main__":
    r = chat([{"role": "user", "content": "Say OK in one word."}])
    print(r)
