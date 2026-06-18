"""Minimal OpenAI chat client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from agent_loop.config import OpenAISettings, load_openai_settings


class ChatCompletionClient(Protocol):
    def complete(self, *, prompt: str) -> str: ...


@dataclass(slots=True)
class OpenAIChatClient:
    """Thin wrapper around the OpenAI chat completions API."""

    api_key: str
    model: str

    def complete(self, *, prompt: str) -> str:
        client = OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("OpenAI returned an empty planner response")
        return content


def create_chat_client(settings: OpenAISettings) -> ChatCompletionClient:
    return OpenAIChatClient(api_key=settings.api_key, model=settings.model)


def create_chat_client_from_env() -> ChatCompletionClient | None:
    settings = load_openai_settings()
    if settings is None:
        return None
    return create_chat_client(settings)
