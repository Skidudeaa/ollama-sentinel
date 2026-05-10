"""Triage orchestration: input → extract → recipe → model → output."""
from __future__ import annotations

import logging
import pathlib
from typing import Sequence

from ollama_sentinel.context import TokenCounter, build_triage_context
from ollama_sentinel.models import OllamaModelConfig, SentinelConfig
from ollama_sentinel.processor import OllamaClient
from ollama_sentinel.triage.extractor import extract_references
from ollama_sentinel.triage.prompts import TRIAGE_SYSTEM_PROMPT

log = logging.getLogger("ollama-sentinel")


async def run_triage(
    *,
    input_text: str,
    config: SentinelConfig,
    cwd: pathlib.Path,
    model_role: str = "triage",
    explicit_context: Sequence[pathlib.Path] = (),
    extract: bool = True,
) -> str:
    """Return the rendered triage markdown. Caller handles printing / saving."""
    if not input_text or not input_text.strip():
        log.info("Empty input; skipping triage.")
        return ""

    references = extract_references(input_text, cwd=cwd) if extract else []

    default_model = config.ollama.models["default"]
    total_budget = max(
        1024,
        default_model.context_window - default_model.output_reserve_tokens,
    )

    counter = TokenCounter()
    prompt = await build_triage_context(
        tool_output=input_text,
        references=references,
        explicit_context_files=list(explicit_context),
        counter=counter,
        total_budget=total_budget,
        cwd=cwd,
    )

    # Resolve the model config with hybrid fallback.
    models = config.ollama.models
    if model_role in models:
        model_cfg = models[model_role]
    elif model_role == "triage":
        log.info(
            "triage role not configured; using default model with built-in prompt"
        )
        default = models["default"]
        model_cfg = OllamaModelConfig(
            name=default.name,
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            temperature=default.temperature,
            top_p=default.top_p,
            think=default.think,
            max_tokens=default.max_tokens,
            context_window=default.context_window,
            output_reserve_tokens=default.output_reserve_tokens,
        )
    else:
        raise KeyError(
            f"Model role '{model_role}' not found in config "
            "(and automatic fallback only applies to the default 'triage' role)."
        )

    client = OllamaClient(config.ollama.model_dump())
    try:
        return await client.generate_with_model(
            model_cfg.model_dump(), prompt,
        )
    finally:
        await client.close()
