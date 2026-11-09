"""Deterministic (no-LLM) failure classification.

Used for two things:
1. the fallback comment when the LLM is unavailable ("best guess"),
2. the offline baseline in the evaluation script.

Rules are intentionally simple regexes over the job log plus the job/stage name.
"""

import re
from dataclasses import dataclass, field

# (category, weight, pattern). Higher weight wins ties; infra noise is checked first
# because a network error can look like any other failure.
RULES: list[tuple[str, int, re.Pattern[str]]] = [
    (cat, w, re.compile(p, 0 if case_sensitive else re.IGNORECASE))
    for cat, w, p, *case_sensitive in [
        ("flaky_infra", 5, r"could not resolve host|temporary failure in name resolution"),
        ("flaky_infra", 5, r"connection (reset|refused|timed out)|i/o timeout|tls handshake"),
        ("flaky_infra", 5, r"no space left on device|runner system failure|oomkilled"),
        ("flaky_infra", 5, r"execution took longer than|429 too many requests|503 service"),
        ("flaky_infra", 4, r"error response from daemon|toomanyrequests|failed to pull image"),
        ("dependency", 4, r"could not find a version that satisfies|no matching distribution"),
        ("dependency", 4, r"npm err! code (eresolve|e404|etarget)|resolutionimpossible"),
        ("dependency", 4, r"failed to (query|install) (available )?provider|incompatible provider"),
        # Terraform prints "Error:" (often after a "│" box char); pip/npm print "ERROR:".
        ("terraform", 3, r"^\s*[│|]?\s*Error: .+", True),
        ("terraform", 2, r"on [\w./\-]+\.tf(vars)? line \d+"),
        ("ansible_task", 4, r"^fatal: \[|failed! =>|^error! "),
        ("ansible_task", 3, r"play recap.*failed=[1-9]|unreachable=[1-9]"),
        ("lint_policy", 4, r"\b(ansible-lint|flake8|ruff|eslint|black|isort|yamllint)\b.*"),
        ("lint_policy", 4, r"would reformat|\d+ problems? \(\d+ errors?|violations? found"),
        ("lint_policy", 4, r"\[[a-z\-]+\[[a-z\-]+\]\]|\b[EWF]\d{3}\b|policy violation|deny\["),
        (
            "lint_policy",
            4,
            r"potential secrets? (detected|found|about to be committed)|terraform fmt.*-check",
        ),
        (
            "test_failure",
            4,
            r"^FAILED [\w/.\-]+::|=+ .*\d+ failed|assertionerror|tests?: +\d+ failed",
        ),
        ("test_failure", 3, r"^E\s+(assert|\w+Error)"),
    ]
]

_TF_LOCATION = re.compile(r"on ([\w./\-]+\.tf(?:vars)?) line (\d+)")
_PY_LOCATION = re.compile(r'File "(?:/builds/[^/]+/[^/]+/)?([\w./\-]+\.py)", line (\d+)')
_PYTEST_LOCATION = re.compile(r"^([\w./\-]+\.py):(\d+):", re.MULTILINE)
_ANSIBLE_LOCATION = re.compile(r"appears to be in '(?:/builds/[^/]+/[^/]+/)?([^']+)': line (\d+)")
_LINT_LOCATION = re.compile(r"^([\w./\-]+\.(?:ya?ml|py|jsx?|tsx?|tf|rego)):(\d+)", re.MULTILINE)


@dataclass
class HeuristicGuess:
    category: str
    score: int
    evidence: list[str] = field(default_factory=list)
    file: str | None = None
    line: int | None = None


def _terraform_category(job_name: str, stage: str, log: str) -> str:
    hint = f"{job_name} {stage}".lower()
    if "fmt" in hint:
        return "lint_policy"
    if "plan" in hint or "Terraform planned" in log or "terraform plan" in log:
        return "terraform_plan"
    return "terraform_validate"


def locate(log: str) -> tuple[str | None, int | None]:
    for pattern in (
        _TF_LOCATION,
        _ANSIBLE_LOCATION,
        _PYTEST_LOCATION,
        _LINT_LOCATION,
        _PY_LOCATION,
    ):
        matches = pattern.findall(log)
        if matches:
            path, line = matches[-1] if pattern is _PY_LOCATION else matches[0]
            return path, int(line)
    return None, None


def classify(log: str, job_name: str = "", stage: str = "") -> HeuristicGuess:
    scores: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for line in log.splitlines():
        for category, weight, pattern in RULES:
            if pattern.search(line):
                scores[category] = scores.get(category, 0) + weight
                bucket = evidence.setdefault(category, [])
                if len(bucket) < 5 and line.strip() not in bucket:
                    bucket.append(line.strip())
                break  # one rule per line

    # The job name is a strong hint for lint/test jobs whose logs are generic.
    hint = f"{job_name} {stage}".lower()
    for word, category in (("lint", "lint_policy"), ("test", "test_failure")):
        if word in hint and category in scores:
            scores[category] += 3

    if not scores:
        return HeuristicGuess("unknown", 0)
    # Infra noise only wins if nothing more specific matched strongly.
    best = max(scores, key=lambda c: (scores[c], c != "flaky_infra"))
    category = _terraform_category(job_name, stage, log) if best == "terraform" else best
    file, line = locate(log)
    return HeuristicGuess(category, scores[best], evidence.get(best, []), file, line)
