from app.llm.base import CompletionRequest
from app.llm.providers import openai_chat_payload, uses_max_completion_tokens


def test_gpt5_uses_max_completion_tokens_not_max_tokens():
    req = CompletionRequest(prompt="hi", max_tokens=400, temperature=0.0)
    payload = openai_chat_payload("gpt-5.4", req)
    assert "max_tokens" not in payload
    assert "temperature" not in payload
    assert payload["max_completion_tokens"] >= 1600
    assert uses_max_completion_tokens("gpt-5.4")


def test_gpt4_family_keeps_classic_chat_params():
    req = CompletionRequest(prompt="hi", max_tokens=400, temperature=0.0)
    payload = openai_chat_payload("gpt-4o-mini", req)
    assert payload["max_tokens"] == 400
    assert payload["temperature"] == 0.0
    assert "max_completion_tokens" not in payload
