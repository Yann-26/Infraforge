"""Prepare untrusted CI text (job logs, diffs) for the prompt.

Order matters: clean -> tail/limit -> redact. Redaction runs last on exactly the text
that will be sent, so nothing un-redacted can slip in afterwards.
"""

import re

from agents.config import AgentSettings
from agents.redact import redact

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_GITLAB_SECTION = re.compile(r"section_(?:start|end):\d+:[\w.\-]+(?:\[[^\]]*\])?")


def clean_log(raw: str) -> str:
    """Strip ANSI colours, GitLab collapsible-section markers and carriage returns."""
    text = _ANSI.sub("", raw)
    text = _GITLAB_SECTION.sub("", text)
    lines = []
    for line in text.split("\n"):
        # progress bars rewrite the line with \r; keep the final state
        lines.append(line.rsplit("\r", 1)[-1].rstrip())
    return "\n".join(lines)


def tail(text: str, max_lines: int, max_chars: int) -> str:
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    lines = lines[-max_lines:]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[-max_chars:]
        out = out[out.find("\n") + 1 :]  # do not start mid-line
        truncated = True
    return ("[... earlier log lines omitted ...]\n" + out) if truncated else out


def fence_untrusted(text: str, tag: str) -> str:
    """Wrap text in a tag; neutralise any attempt to close the tag from inside."""
    safe = text.replace(f"</{tag}", f"<\\/{tag}")
    return f"<{tag}>\n{safe}\n</{tag}>"


def prepare_log(raw: str, settings: AgentSettings) -> tuple[str, int]:
    """Return ``(redacted_log_tail, redaction_count)``."""
    text = tail(clean_log(raw), settings.log_tail_lines, settings.max_log_chars)
    result = redact(text, redact_ips=settings.redact_ips)
    return result.text, result.total


def prepare_text(text: str, settings: AgentSettings) -> tuple[str, int]:
    result = redact(text, redact_ips=settings.redact_ips)
    return result.text, result.total
