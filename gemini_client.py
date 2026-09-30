"""Gemini client: turns a list of Canvas items into a weekly digest."""

import os
from typing import Any, Dict, List

import google.generativeai as genai

# The fallback model name. Set "gemini_model" in config.json to override it.
DEFAULT_MODEL = "gemini-3.8-flash"

MAX_DETAIL_CHARS = 500


def _format_item(item: Dict[str, Any]) -> str:
    return (
        f"[{item.get('type', 'Item')}] Course: {item.get('course', 'N/A')}\n"
        f"Title: {item.get('title', 'Untitled')}\n"
        f"Date/Due: {item.get('date') or 'N/A'}\n"
        f"Details: {str(item.get('content', ''))[:MAX_DETAIL_CHARS]}\n"
        f"----------------------------------------"
    )


def _get_model(model_name: str, api_key_env_var: str):
    api_key = os.getenv(api_key_env_var)
    if not api_key:
        raise ValueError(f"Environment variable '{api_key_env_var}' is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model_name, generation_config=genai.GenerationConfig(temperature=0.2)
    )


def summarize(
    items: List[Dict[str, Any]],
    model_name: str = DEFAULT_MODEL,
    api_key_env_var: str = "GEMINI_API_KEY",
    days: int = 7,
) -> str:
    """Builds a digest from scratch. Always called with the full current set
    of items, so the prompt size stays bounded instead of growing all week."""
    if not items:
        return "No recent announcements or upcoming assignments found."

    model = _get_model(model_name, api_key_env_var)

    formatted_content = "\n".join(_format_item(i) for i in items)
    prompt = (
        f"Below are course announcements from the past {days} days and upcoming "
        f"assignments from Canvas for Ontario Tech University:\n\n"
        f"{formatted_content}\n\n"
        f"Please provide a concise, well-structured academic digest summarizing "
        f"upcoming deadlines, key announcements, and important tasks grouped by course."
    )

    response = model.generate_content(prompt)
    return response.text


def merge_summary(
    existing_summary: str,
    new_items: List[Dict[str, Any]],
    model_name: str = DEFAULT_MODEL,
    api_key_env_var: str = "GEMINI_API_KEY",
) -> str:
    """Merges new items into an existing summary during background incremental checks."""
    if not new_items:
        return existing_summary

    model = _get_model(model_name, api_key_env_var)

    formatted_new = "\n".join(_format_item(i) for i in new_items)
    prompt = (
        f"Here is the existing course digest:\n{existing_summary}\n\n"
        f"Here are NEW announcements/assignments posted on Canvas:\n{formatted_new}\n\n"
        f"Please update the existing digest by integrating these new items cleanly. "
        f"Keep the formatting organized by course."
    )

    response = model.generate_content(prompt)
    return response.text