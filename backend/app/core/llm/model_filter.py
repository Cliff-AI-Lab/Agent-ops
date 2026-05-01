from __future__ import annotations

BLOCKED_KEYWORDS = (
    "embed",
    "embedding",
    "tts",
    "whisper",
    "funasr",
    "image",
    "audio",
    "speech",
    "vision",
    "rerank",
    "reranker",
    "bge",
    "ace-step",
    "wan-ti2v",
    "wan-t2v",
    "wan-i2v",
    "t2v",
    "i2v",
    "t2i",
    "i2i",
)


def is_chat_model(model_id: str) -> bool:
    """Return True when a model identifier is suitable for chat."""
    normalized = model_id.strip().lower()
    if not normalized:
        return False
    return not any(keyword in normalized for keyword in BLOCKED_KEYWORDS)
