from __future__ import annotations

import pytest

from app.services.ai_matching import AIServiceError, OpenAIAssistedMatching


@pytest.mark.asyncio
async def test_ai_circuit_breaker_opens_after_threshold(monkeypatch) -> None:
    service = OpenAIAssistedMatching(
        api_key="test-key",
        circuit_breaker_failure_threshold=2,
        circuit_breaker_cooldown_seconds=60,
    )
    calls = 0

    async def fail_request(**_kwargs):
        nonlocal calls
        calls += 1
        raise AIServiceError("simulated outage")

    monkeypatch.setattr(service, "_structured_response_request", fail_request)
    kwargs = dict(
        schema_name="x",
        schema={"type": "object"},
        system="s",
        user="u",
        max_output_tokens=10,
    )

    with pytest.raises(AIServiceError, match="simulated outage"):
        await service._structured_response(**kwargs)
    with pytest.raises(AIServiceError, match="simulated outage"):
        await service._structured_response(**kwargs)
    with pytest.raises(AIServiceError, match="circuit breaker is open"):
        await service._structured_response(**kwargs)

    assert calls == 2


@pytest.mark.asyncio
async def test_ai_circuit_breaker_resets_after_success(monkeypatch) -> None:
    service = OpenAIAssistedMatching(
        api_key="test-key",
        circuit_breaker_failure_threshold=3,
        circuit_breaker_cooldown_seconds=60,
    )
    outcomes = [AIServiceError("temporary"), {"ok": True}]

    async def request(**_kwargs):
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(service, "_structured_response_request", request)
    kwargs = dict(
        schema_name="x",
        schema={"type": "object"},
        system="s",
        user="u",
        max_output_tokens=10,
    )
    with pytest.raises(AIServiceError):
        await service._structured_response(**kwargs)
    assert await service._structured_response(**kwargs) == {"ok": True}
    assert service._circuit_failures == 0
    assert service._circuit_open_until == 0.0
