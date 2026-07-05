"""OpenAI-compatible LLM client and structured-output helpers."""

from wayfinder.llm.client import ChatClient
from wayfinder.llm.config import LLMConfig, load_llm_config
from wayfinder.llm.structured import generate_brain_recommendation

__all__ = [
    "ChatClient",
    "LLMConfig",
    "generate_brain_recommendation",
    "load_llm_config",
]
