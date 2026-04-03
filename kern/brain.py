import os
import json
from pathlib import Path
from kern.db import get_config

KERN_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "kern.md"


def get_kern_prompt() -> str:
    return KERN_PROMPT_PATH.read_text()


def build_system_prompt(memory_context: str = "", tools_manifest: str = "") -> str:
    kern = get_kern_prompt()
    dynamic_parts = []
    if memory_context:
        dynamic_parts.append(memory_context)
    if tools_manifest:
        dynamic_parts.append(tools_manifest)
    if dynamic_parts:
        return kern + "\n\n---\n\n" + "\n\n".join(dynamic_parts)
    return kern


def get_llm_client():
    provider = get_config("llm_provider", "anthropic")
    api_key = get_config("llm_api_key", "")

    if provider == "anthropic":
        import anthropic
        return ("anthropic", anthropic.Anthropic(api_key=api_key))

    elif provider == "openai":
        import openai
        return ("openai", openai.OpenAI(api_key=api_key))

    elif provider == "openrouter":
        import openai
        return ("openrouter", openai.OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        ))

    raise ValueError(f"Unbekannter Provider: {provider}")


def get_model() -> str:
    provider = get_config("llm_provider", "anthropic")
    saved = get_config("llm_model")
    if saved:
        return saved
    defaults = {
        "anthropic": "claude-sonnet-4-6",
        "openai": "gpt-4o",
        "openrouter": "anthropic/claude-sonnet-4-6",
    }
    return defaults.get(provider, "claude-opus-4-6")


def chat(messages: list[dict], system: str = "") -> str:
    provider, client = get_llm_client()
    model = get_model()

    if provider == "anthropic":
        response = client.messages.create(
            model=model,
            max_tokens=8096,
            system=system,
            messages=messages
        )
        return response.content[0].text

    else:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)
        response = client.chat.completions.create(
            model=model,
            messages=all_messages,
            max_tokens=8096
        )
        return response.choices[0].message.content


def chat_stream(messages: list[dict], system: str = ""):
    provider, client = get_llm_client()
    model = get_model()

    if provider == "anthropic":
        with client.messages.stream(
            model=model,
            max_tokens=8096,
            system=system,
            messages=messages
        ) as stream:
            for text in stream.text_stream:
                yield text

    else:
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)
        stream = client.chat.completions.create(
            model=model,
            messages=all_messages,
            max_tokens=8096,
            stream=True
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
