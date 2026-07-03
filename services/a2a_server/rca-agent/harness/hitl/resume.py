from __future__ import annotations

from typing import Any

from langgraph.types import Command, Interrupt


def extract_interrupt(result: dict[str, Any]) -> Interrupt | None:
    """Return the first LangGraph interrupt from an invoke result, if any."""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    return first if isinstance(first, Interrupt) else None


def is_interrupted(result: dict[str, Any]) -> bool:
    return extract_interrupt(result) is not None


def interrupt_payload(result: dict[str, Any]) -> dict[str, Any]:
    intr = extract_interrupt(result)
    if intr is None:
        return {}
    value = intr.value
    return value if isinstance(value, dict) else {"value": value}


def graph_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def resume_command(feedback: dict[str, Any]) -> Command:
    return Command(resume=feedback)
