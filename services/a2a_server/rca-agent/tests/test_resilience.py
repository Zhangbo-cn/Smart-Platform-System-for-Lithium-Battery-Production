from __future__ import annotations

import asyncio

import pytest

from harness.resilience.circuit_breaker import CircuitBreaker, CircuitOpenError
from harness_core.resilience.retry import with_retry


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failures():
    attempts = {"n": 0}

    @with_retry(max_attempts=3, base_delay=0.01)
    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("temporary")
        return "ok"

    assert await flaky() == "ok"
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(name="test", failure_threshold=2, recovery_seconds=10)

    async def boom():
        raise RuntimeError("nope")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    with pytest.raises(CircuitOpenError):
        await cb.call(boom)


@pytest.mark.asyncio
async def test_circuit_recovers_after_timeout():
    cb = CircuitBreaker(name="t2", failure_threshold=1, recovery_seconds=0.05)

    async def boom():
        raise RuntimeError("x")

    async def ok():
        return "yes"

    with pytest.raises(RuntimeError):
        await cb.call(boom)
    with pytest.raises(CircuitOpenError):
        await cb.call(boom)

    await asyncio.sleep(0.06)
    assert await cb.call(ok) == "yes"
