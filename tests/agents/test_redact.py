"""Redaction must remove anything credential-like before text is sent to the model.

Fake secrets are assembled at runtime so the repository never contains a literal
token-shaped string (keeps secret scanners and push protection quiet).
"""

import pytest

from agents.redact import REDACTED, find_secrets, redact

FAKE_GITLAB = "glpat" + "-" + "a1B2c3D4e5F6g7H8i9J0k"
FAKE_ANTHROPIC = "sk-ant-" + "api03-" + "x" * 8 + "Y7z" * 8
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_JWT = (
    "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N"
)
FAKE_KEY = (
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n-----END OPENSSH "
    "PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    "secret",
    [FAKE_GITLAB, FAKE_ANTHROPIC, FAKE_AWS, FAKE_JWT],
)
def test_token_formats_are_redacted(secret):
    result = redact(f"curl -H 'X: {secret}' https://gitlab.com/api", environ={})
    assert secret not in result.text
    assert result.total >= 1


def test_private_key_block_is_redacted():
    result = redact(f"key:\n{FAKE_KEY}\nnext line", environ={})
    assert "b3BlbnNzaC1rZXktdjEAAAA" not in result.text
    assert "next line" in result.text
    assert result.counts.get("private_key") == 1


@pytest.mark.parametrize(
    "line,secret",
    [
        ('proxmox_token_secret = "s3cr3t-Value-99"', "s3cr3t-Value-99"),
        ("DB_PASSWORD=Hunter2Hunter2", "Hunter2Hunter2"),
        ('{"api_key": "k3y-0123456789"}', "k3y-0123456789"),
        ("SMTP_PASSWORD: 'App-Pass-2024'", "App-Pass-2024"),
        ("Authorization: Bearer abcdef0123456789abcdef", "abcdef0123456789abcdef"),
    ],
)
def test_assignments_are_redacted_but_keys_kept(line, secret):
    result = redact(line, environ={})
    assert secret not in result.text
    assert REDACTED in result.text
    # The key name stays so the model still understands the context.
    assert line.split(secret)[0].strip()[:6] in result.text


def test_url_credentials_are_redacted():
    result = redact("postgresql://infraforge:infraforge123@db:5432/infraforge", environ={})
    assert "infraforge123" not in result.text
    assert "postgresql://infraforge:" in result.text


@pytest.mark.parametrize(
    "line",
    [
        'token = os.getenv("GITLAB_TOKEN", "")',
        "gitlab_token: SecretStr | None = None",
        "password = settings.db_password",
        'api_key = "${ANTHROPIC_API_KEY}"',
        "secret: {{ vault_secret }}",
        "token = var.proxmox_token",
        "max_output_tokens: int = 8000",
    ],
)
def test_code_references_and_placeholders_are_not_redacted(line):
    assert redact(line, environ={}).text == line


def test_env_values_marked_secret_are_redacted():
    env = {"AGENT_GITLAB_TOKEN": "plain-looking-value", "CI_PROJECT_ID": "12345678"}
    result = redact("token is plain-looking-value, project 12345678", environ=env)
    assert "plain-looking-value" not in result.text
    assert "12345678" in result.text  # not a secret-looking variable name
    assert result.counts["env_value"] == 1


def test_ips_are_redacted_but_meaningful_ones_kept():
    text = "ssh 10.100.20.5 and 8.8.8.8 cidr 10.100.40.0/24 open 0.0.0.0/0 lo 127.0.0.1"
    result = redact(text, environ={})
    assert "10.100.20.5" not in result.text
    assert "8.8.8.8" not in result.text
    assert "[PRIVATE_IP]/24" in result.text
    assert "0.0.0.0/0" in result.text
    assert "127.0.0.1" in result.text


def test_ip_redaction_can_be_disabled():
    assert redact("host 10.0.0.1", redact_ips=False, environ={}).text == "host 10.0.0.1"


def test_version_strings_are_not_ips():
    assert redact("terraform 1.7.0 provider 2.9.14", environ={}).text.endswith("2.9.14")


def test_find_secrets_reports_line_numbers_and_honours_allowlist():
    text = "\n".join(
        [
            "ok = 1",
            f"token = {FAKE_GITLAB}",
            f"token = {FAKE_GITLAB}  # pragma: allowlist secret",
        ]
    )
    hits = find_secrets(text)
    assert [(name, line) for name, line, _ in hits] == [("gitlab_token", 2)]
