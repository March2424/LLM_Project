from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None


class ChatModel(Protocol):
    def __call__(self, task: str, prompt: str) -> ModelReply: ...


class Embedder(Protocol):
    def __call__(self, text: str) -> list[float]: ...
