"""
Thin wrapper around the free-tier Gemini API for turning course
emails into a weekly digest - either a full fresh summary, or an
incremental merge of new emails into an existing summary.
"""

import os

import google.generativeai as genai

NEW_MARK = "\U0001F195"  # 🆕

FULL_PROMPT_TEMPLATE = """You are helping a university student stay on top of \
their courses. Below are emails from the last {days} days sent by their \
professors, TAs, or course platforms (like Blackboard/Canvas).

Read them and produce a clear weekly digest in Markdown with exactly \
these sections, in this order:

## Due This Week
Assignments, quizzes, or labs with a date, soonest first. Include the \
course/sender and the deadline.

## Announcements
Anything important that isn't a deadline (schedule changes, cancelled \
classes, exam info, etc).

## Worth a Second Look
Anything ambiguous, or that seems to reference an attachment or a link \
the student should open themselves.

If a section has nothing relevant, write "Nothing this week." under it. \
Be concise - bullet points, no filler. Output ONLY the Markdown digest, \
nothing else.

EMAILS:
{emails_block}
"""

MERGE_PROMPT_TEMPLATE = """You maintain a running weekly digest of course \
emails for a university student, in Markdown with exactly these \
sections, in this order: "## Due This Week", "## Announcements", \
"## Worth a Second Look".

Here is the CURRENT digest:
{current_summary}

Here are {n} NEW emails that just arrived and are not yet reflected \
in the digest above:
{emails_block}

Update the digest to incorporate anything relevant from the new emails:
- Keep every existing bullet exactly as-is, in the same section, UNLESS \
a new email clearly supersedes or cancels it (e.g. a deadline moved or \
an assignment cancelled) - in that case update that bullet in place.
- Add new bullets for anything new and relevant from the new emails.
- Prefix every bullet you ADD or CHANGE with "{new_mark} " (that exact \
marker, then a space) at the very start of the bullet, so it's clear \
what's new. Do NOT add this marker to bullets you're leaving unchanged.
- If a new email has nothing digest-worthy, don't add anything for it.
- Keep the same three section headers, in the same order, even if a \
section ends up empty ("Nothing this week.").
- Output ONLY the updated Markdown digest, nothing else - no preamble, \
no explanation of what you changed.
"""


def _configure(api_key_env_var: str):
    api_key = os.environ.get(api_key_env_var)
    if not api_key:
        raise EnvironmentError(
            f"Set the {api_key_env_var} environment variable to your "
            "Gemini API key (get one free at https://aistudio.google.com/apikey)"
        )
    genai.configure(api_key=api_key)


def _emails_block(emails: list) -> str:
    blocks = []
    for e in emails:
        blocks.append(
            f"---\nFrom: {e['sender']}\nDate: {e['date']}\n"
            f"Subject: {e['subject']}\n\n{e['body']}\n"
        )
    return "\n".join(blocks)


def summarize(emails: list, model_name: str, api_key_env_var: str, days: int) -> str:
    """Full fresh summary from scratch - used for the weekly rebuild."""
    if not emails:
        return "No course emails found in this period."

    _configure(api_key_env_var)
    prompt = FULL_PROMPT_TEMPLATE.format(days=days, emails_block=_emails_block(emails))
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text


def merge_summary(
    current_summary: str, new_emails: list, model_name: str, api_key_env_var: str
) -> str:
    """Merges new_emails into current_summary, marking added/changed
    bullets with NEW_MARK. Used for incremental (mid-week) checks."""
    if not new_emails:
        return current_summary

    _configure(api_key_env_var)
    prompt = MERGE_PROMPT_TEMPLATE.format(
        current_summary=current_summary,
        n=len(new_emails),
        emails_block=_emails_block(new_emails),
        new_mark=NEW_MARK,
    )
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text