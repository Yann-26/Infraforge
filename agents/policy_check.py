"""Deterministic policy checks for the MR reviewer (no LLM involved).

Two sources, both evaluated with the ``opa`` CLI against ``policies/opa``:

1. ``data.infraforge.policies.violations`` — the existing request policies
   (infrastructure.rego). Each changed ``terraform/environments/*/terraform.tfvars`` is
   turned into an ``input.request`` so e.g. "isolated + allow_internet" is caught at
   review time, not only at provisioning time.
2. ``data.infraforge.terraform_security.deny`` — code rules over added lines
   (terraform_security.rego).

If ``opa`` is missing or a policy fails to compile, the reviewer continues and the
comment says which checks did not run.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from agents.config import AgentSettings
from agents.diffs import FileDiff
from agents.schemas import ReviewFinding

SECURITY_QUERY = "data.infraforge.terraform_security.deny"
REQUEST_QUERY = "data.infraforge.policies.violations"
SECURITY_POLICY_FILE = "terraform_security.rego"

_TFVAR = re.compile(r'^\s*([A-Za-z_][\w]*)\s*=\s*("(?:[^"\\]|\\.)*"|true|false|-?\d+(?:\.\d+)?)')
_OPA_SEVERITY = {"critical": "critical", "error": "high", "warning": "medium", "info": "low"}


@dataclass
class PolicyReport:
    findings: list[ReviewFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ran: bool = False


def parse_tfvars(text: str) -> dict[str, object]:
    """Parse top-level scalar ``key = value`` pairs (enough for InfraForge tfvars)."""
    values: dict[str, object] = {}
    for line in text.splitlines():
        match = _TFVAR.match(line)
        if not match:
            continue
        key, raw = match.groups()
        values[key] = json.loads(raw) if raw[0] in '"tf-0123456789' else raw
    return values


def tfvars_to_request(values: dict[str, object]) -> dict:
    """Map tfvars onto the ``input.request`` shape used by infrastructure.rego."""
    resources = {
        k: values[k]
        for k in ("isolated", "allow_internet", "cpu_cores", "memory_mb", "gpu")
        if k in values
    }
    request: dict[str, object] = {"resources": resources}
    for key in ("environment", "data_classification", "duration_days"):
        if key in values:
            request["environment_type" if key == "environment" else key] = values[key]
    return {"request": request}


class OpaRunner:
    def __init__(self, settings: AgentSettings):
        self.binary = shutil.which(settings.opa_binary)
        self.policies = Path(settings.policies_dir)

    @property
    def available(self) -> bool:
        return self.binary is not None and self.policies.is_dir()

    def eval(self, query: str, input_data: dict, data_paths: list[Path]) -> object:
        cmd = [self.binary, "eval", "--format", "json", "--v0-compatible", "--stdin-input"]
        for path in data_paths:
            cmd += ["--data", str(path)]
        cmd.append(query)
        proc = subprocess.run(
            cmd,
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:500] or f"opa exited {proc.returncode}")
        result = json.loads(proc.stdout).get("result") or []
        return result[0]["expressions"][0]["value"] if result else None


def _line_of(f: FileDiff, key: str) -> int:
    for line, text in f.added.items():
        if text.strip().startswith(key):
            return line
    return min(f.added) if f.added else 1


def _security_findings(opa: OpaRunner, files: list[FileDiff], report: PolicyReport):
    payload = {
        "files": [
            {
                "path": f.path,
                "kind": f.kind,
                "added": [{"line": n, "text": t} for n, t in sorted(f.added.items())],
            }
            for f in files
            if f.kind and f.added
        ]
    }
    if not payload["files"]:
        return
    try:
        denies = opa.eval(SECURITY_QUERY, payload, [opa.policies / SECURITY_POLICY_FILE]) or []
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        report.notes.append(f"security rules did not run: {exc}")
        return
    for d in denies:
        report.findings.append(
            ReviewFinding(
                file=d["file"],
                line=d["line"],
                severity=d["severity"],
                rule=d["rule"],
                reason=d["message"],
                suggested_fix=d["fix"],
                confidence=1.0,
                source="opa",
            )
        )


def _request_policy_findings(opa: OpaRunner, files: list[FileDiff], report: PolicyReport):
    tfvars = [f for f in files if f.path.endswith(".tfvars") and f.added]
    for f in tfvars:
        # Use all visible new-side lines: an unchanged `isolated = true` matters too.
        values = parse_tfvars("\n".join(f.new_lines[n] for n in sorted(f.new_lines)))
        try:
            violations = opa.eval(REQUEST_QUERY, tfvars_to_request(values), [opa.policies]) or []
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            report.notes.append(f"request policies (infrastructure.rego) did not run: {exc}")
            return
        for v in violations:
            key = "allow_internet" if "internet" in v.get("policy_id", "") else "isolated"
            report.findings.append(
                ReviewFinding(
                    file=f.path,
                    line=_line_of(f, key),
                    severity=_OPA_SEVERITY.get(v.get("severity", ""), "medium"),
                    rule=v.get("policy_id", "opa_policy"),
                    reason=v.get("message", "OPA policy violation"),
                    suggested_fix=v.get("remediation", "See policies/opa/infrastructure.rego."),
                    confidence=1.0,
                    source="opa",
                )
            )


def run_policies(files: list[FileDiff], settings: AgentSettings) -> PolicyReport:
    report = PolicyReport()
    opa = OpaRunner(settings)
    if not opa.available:
        report.notes.append("OPA checks skipped: `opa` binary or policies directory not found")
        logger.warning(report.notes[-1])
        return report
    report.ran = True
    _security_findings(opa, files, report)
    _request_policy_findings(opa, files, report)
    return report


def policy_sources(settings: AgentSettings, max_chars: int = 8000) -> str:
    """Policy source text included in the reviewer prompt as reference."""
    out, size = [], 0
    for path in sorted(Path(settings.policies_dir).glob("*.rego")):
        if path.name.endswith("_test.rego"):
            continue
        text = f"# file: {path.name}\n{path.read_text(encoding='utf-8')}\n"
        if size + len(text) > max_chars:
            break
        out.append(text)
        size += len(text)
    return "".join(out) or "(no policies found)"
