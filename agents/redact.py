"""Secret redaction.

Everything that leaves the CI job for the model (job logs, diffs) goes through
``redact()`` first. The same patterns are reused by ``quality/secrets_scan.py`` so
"what we redact" and "what we refuse to commit" stay in sync.

Redaction is deliberately aggressive: a false positive costs a little context, a
false negative leaks a credential to a third party.
"""

import ipaddress
import os
import re
from dataclasses import dataclass, field
from functools import partial

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]
    # Index of the capture group holding the secret; 0 means the whole match.
    group: int = 0


def _p(name: str, pattern: str, group: int = 0, flags: int = 0) -> SecretPattern:
    return SecretPattern(name, re.compile(pattern, flags), group)


SECRET_PATTERNS: list[SecretPattern] = [
    _p(
        "private_key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    ),
    _p(
        "gitlab_token",
        r"\b(?:glpat|gldt|glrt|glptt|glcbt|glimt|glagent|glsoat)-[A-Za-z0-9_\-]{20,}",
    ),
    _p("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    _p("anthropic_key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}"),
    _p("openai_style_key", r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    _p("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    _p("slack_token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    _p("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    _p("bearer_token", r"(?i)\b(?:bearer|token)\s+([A-Za-z0-9_\-\.=]{16,})", group=1),
    _p("url_credentials", r"(?i)\b[a-z][a-z0-9+\-.]*://[^\s:/@]+:([^\s@/]+)@", group=1),
    # key = value / key: value / "key": "value" style assignments
    _p(
        "assignment",
        r"""(?ix)
        \b[\w\-.]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key
            |client[_\-]?secret|auth[_\-]?token|authorization)[\w\-.]*["']?
        \s*[:=](?!=)\s*
        ["']?([^\s"'`,;(){}\[\]]{6,})(?=["'\s,;})\]]|$)
        """,
        group=1,
    ),
]

# Values that look like assignments but are clearly not secrets.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:\$\{?[\w.]+\}?|\{\{.*\}\}|<[^>]+>|var\.\w+|local\.\w+|data\.[\w.]+|none|null|true"
    r"|false|changeme|example|placeholder|bearer|basic|\[REDACTED(?::[a-z_]+)?\]|x{6,}|\*{6,})$"
)

# Code references rather than literal values: settings.token, gitlab_token, SecretStr.
_CODE_REFERENCE = re.compile(
    r"^(?:[A-Za-z_]\w*(?:\.\w+)+"  # settings.token
    r"|[a-z][a-z0-9]*(?:_[a-z0-9]+)+"  # snake_case
    r"|[a-z]+(?:[A-Z][a-z0-9]+)+"  # camelCase
    r"|[A-Z][a-z]+(?:[A-Z][a-z]+)+)$"  # CamelCase
)
# File paths (e.g. a *path* to a key file) are not secrets themselves.
_PATH_VALUE = re.compile(r"^(?:~|\.{1,2})?/[\w./\-]+$")
# Keys that name an identifier/location of a secret rather than the secret.
_NON_SECRET_KEY = re.compile(
    r"(?i)(?:_id|_file|_path|_url|_name|_type|_ttl|_expiration|_length)[\"']?\s*[:=]"
)

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")
# Addresses that carry review meaning and are never sensitive.
_KEEP_IPS = {"0.0.0.0", "127.0.0.1", "255.255.255.255"}

_SENSITIVE_ENV = re.compile(r"(?i)(token|secret|password|passwd|api_key|apikey|private_key|_key$)")


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _sensitive_env_values(environ: dict[str, str] | None = None) -> list[str]:
    """Values of env vars whose *name* looks secret (CI masked variables etc.)."""
    env = os.environ if environ is None else environ
    values = {v for k, v in env.items() if _SENSITIVE_ENV.search(k) and v and len(v) >= 8}
    # Longest first so overlapping values are fully replaced.
    return sorted(values, key=len, reverse=True)


def _redact_ip(match: re.Match[str]) -> str:
    raw = match.group(0)
    addr, _, prefix = raw.partition("/")
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return raw  # not an IP (e.g. a version string like 1.2.3.400)
    if addr in _KEEP_IPS or prefix == "0":
        return raw
    label = "[PRIVATE_IP]" if ip.is_private else "[IP]"
    return f"{label}/{prefix}" if prefix else label


def _replace_secret(match: re.Match[str], rule: SecretPattern, counts: dict[str, int]) -> str:
    value = match.group(rule.group)
    if value is None or _PLACEHOLDER.match(value):
        return match.group(0)
    if rule.group and (_CODE_REFERENCE.match(value) or _PATH_VALUE.match(value)):
        return match.group(0)
    if rule.name == "assignment" and _NON_SECRET_KEY.search(match.group(0)):
        return match.group(0)
    counts[rule.name] = counts.get(rule.name, 0) + 1
    if rule.group == 0:
        return f"[REDACTED:{rule.name}]"
    start, end = match.span(rule.group)
    offset, whole = match.start(0), match.group(0)
    return whole[: start - offset] + REDACTED + whole[end - offset :]


def _replace_ip(match: re.Match[str], counts: dict[str, int]) -> str:
    replaced = _redact_ip(match)
    if replaced != match.group(0):
        counts["ip_address"] = counts.get("ip_address", 0) + 1
    return replaced


def redact(
    text: str, *, redact_ips: bool = True, environ: dict[str, str] | None = None
) -> RedactionResult:
    """Return ``text`` with secrets (and optionally IPv4 addresses) replaced."""
    counts: dict[str, int] = {}
    for value in _sensitive_env_values(environ):
        if value in text:
            counts["env_value"] = counts.get("env_value", 0) + text.count(value)
            text = text.replace(value, REDACTED)
    for rule in SECRET_PATTERNS:
        text = rule.regex.sub(partial(_replace_secret, rule=rule, counts=counts), text)
    if redact_ips:
        text = _IPV4.sub(partial(_replace_ip, counts=counts), text)
    return RedactionResult(text=text, counts=counts)


def find_secrets(text: str) -> list[tuple[str, int, str]]:
    """Return ``(pattern_name, line_number, line)`` for each line containing a secret.

    Used by the secret scan quality check. IPs and env values are not considered.
    """
    hits: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if "pragma: allowlist secret" in line:
            continue
        result = redact(line, redact_ips=False, environ={})
        for name in result.counts:
            hits.append((name, lineno, line.strip()))
    return hits
