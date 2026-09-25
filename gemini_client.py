import os
import google.generativeai as genai
from typing import List, Dict, Any

def _format_item(item: Dict[str, Any]) -> str:
    return (
        f"[{item['type']}] Course: {item['course']}\n"
        f"Title: {item['title']}\n"
        f"Date/Due: {item['date']}\n"
        f"Details: {item['content'][:500]}\n"
        f"----------------------------------------"
    )

def summarize(
    items: List[Dict[str, Any]],
    model_name: str = "gemini-3.6-flash",
    api_key_env_var: str = "GEMINI_API_KEY",
    days: int = 7
) -> str:
    api_key = os.getenv(api_key_env_var)
    if not api_key:
        raise ValueError(f"Environment variable '{api_key_env_var}' is not set.")
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    formatted_content = "\n".join(_format_item(i) for i in items)
    prompt = (
        f"Below are the recent course announcements and assignments from Canvas "
        f"for Ontario Tech University over the past {days} days:\n\n"
        f"{formatted_content}\n\n"
        f"Please provide a concise, well-structured academic digest summarizing "
        f"upcoming deadlines, key announcements, and important tasks grouped by course."
    )

    response = model.generate_content(prompt)
    return response.text

def merge_summary(
    existing_summary: str,
    new_items: List[Dict[str, Any]],
    model_name: str = "gemini-3.6-flash",
    api_key_env_var: str = "GEMINI_API_KEY"
) -> str:
    api_key = os.getenv(api_key_env_var)
    if not api_key:
        raise ValueError(f"Environment variable '{api_key_env_var}' is not set.")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    formatted_new = "\n".join(_format_item(i) for i in new_items)
    prompt = (
        f"Here is the existing course digest:\n{existing_summary}\n\n"
        f"Here are NEW announcements/assignments posted on Canvas:\n{formatted_new}\n\n"
        f"Please update the existing digest by integrating these new items cleanly. "
        f"Keep the formatting organized by course."
    )

    response = model.generate_content(prompt)
    return response.text