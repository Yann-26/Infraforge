"""Offline stand-in for ``opa eval`` used ONLY by the evaluation script.

When the ``opa`` binary is not installed, the heuristic eval still needs reviewer
numbers. This reads the rule table straight out of policies/opa/terraform_security.rego
(so there is a single source of truth) and applies it with Python regexes, mirroring
the ``deny`` rule. The request policies (infrastructure.rego) are not replicated; the
eval report says which engine was used. Production code always uses real OPA.
"""

import re
from pathlib import Path

from agents.config import AgentSettings
from agents.diffs import FileDiff
from agents.policy_check import PolicyReport
from agents.schemas import ReviewFinding

REGO = Path(__file__).resolve().parents[2] / "policies" / "opa" / "terraform_security.rego"

_KIND = re.compile(r'^\t"(\w+)": \{$')
_RULE = re.compile(r'^\t\t"([A-Z0-9_]+)": \{$')
_FIELD = re.compile(r'^\t\t\t"(\w+)": (?:`([^`]*)`|"([^"]*)"),$')


def load_rules(path: Path = REGO) -> dict[str, dict[str, dict[str, str]]]:
    rules: dict[str, dict[str, dict[str, str]]] = {}
    kind = rule = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := _KIND.match(line):
            kind = m.group(1)
            rules[kind] = {}
        elif (m := _RULE.match(line)) and kind:
            rule = m.group(1)
            rules[kind][rule] = {}
        elif (m := _FIELD.match(line)) and kind and rule:
            rules[kind][rule][m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return rules


def replica_policy_runner(files: list[FileDiff], settings: AgentSettings) -> PolicyReport:
    report = PolicyReport(ran=True, notes=["rego replica used (opa not installed)"])
    rules = load_rules()
    for f in files:
        for rule_id, rule in rules.get(f.kind or "", {}).items():
            for line, text in sorted(f.added.items()):
                if not re.search(rule["regex"], text) or "opa:allow" in text:
                    continue
                if rule.get("unless") and re.search(rule["unless"], text):
                    continue
                finding = ReviewFinding(
                    file=f.path,
                    line=line,
                    severity=rule["severity"],
                    rule=rule_id,
                    reason=rule["message"],
                    suggested_fix=rule["fix"],
                    confidence=1.0,
                    source="opa",
                )
                report.findings.append(finding)
    return report
