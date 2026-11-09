"""OPA integration: tfvars mapping, opa invocation, graceful degradation.

The real ``opa`` binary is exercised in CI by the ``opa_policy_tests`` job
(``opa test policies/opa``). Here a fake ``opa`` script records its arguments.
"""

import json
import re
import stat
import sys
from pathlib import Path

import pytest

from agents.config import AgentSettings
from agents.diffs import parse_unified_diff
from agents.policy_check import parse_tfvars, run_policies, tfvars_to_request

REGO = Path("policies/opa/terraform_security.rego")

TFVARS_DIFF = """diff --git a/terraform/environments/isolated/terraform.tfvars b/terraform/environments/isolated/terraform.tfvars
--- a/terraform/environments/isolated/terraform.tfvars
+++ b/terraform/environments/isolated/terraform.tfvars
@@ -1,5 +1,5 @@
 environment = "isolated"
 vlan_id     = 400
 network_cidr = "10.100.40.0/24"
 isolated    = true
-allow_internet = false
+allow_internet = true
"""


def test_parse_tfvars_scalars():
    values = parse_tfvars('env = "isolated"\nisolated = true\ncpu_cores = 8\n# comment\nx = [1]')
    assert values == {"env": "isolated", "isolated": True, "cpu_cores": 8}


def test_tfvars_to_request_matches_infrastructure_rego_shape():
    request = tfvars_to_request(
        {"environment": "isolated", "isolated": True, "allow_internet": True}
    )
    assert request == {
        "request": {
            "resources": {"isolated": True, "allow_internet": True},
            "environment_type": "isolated",
        }
    }


def test_missing_opa_is_reported_not_raised(tmp_path):
    settings = AgentSettings(_env_file=None, opa_binary="definitely-not-opa")
    report = run_policies(parse_unified_diff(TFVARS_DIFF), settings)
    assert not report.ran and report.findings == []
    assert "OPA checks skipped" in report.notes[0]


@pytest.fixture
def fake_opa(tmp_path):
    """A stand-in `opa` that answers both queries and logs each invocation."""
    log = tmp_path / "calls.jsonl"
    script = tmp_path / "opa"
    script.write_text(f"""#!{sys.executable}
import json, sys
args = sys.argv[1:]
data = json.load(sys.stdin)
with open({str(log)!r}, "a") as fh:
    fh.write(json.dumps({{"args": args, "input": data}}) + "\\n")
query = args[-1]
if query.endswith("terraform_security.deny"):
    value = [{{"rule": "TF_TEST", "file": f["path"], "line": f["added"][0]["line"],
              "severity": "high", "message": "m", "fix": "f"}} for f in data["files"]]
else:
    r = data["request"]["resources"]
    value = [{{"policy_id": "internet_access_001", "severity": "critical",
              "message": "Isolated environments cannot have internet access",
              "remediation": "Disable internet access"}}] if r.get("isolated") and r.get("allow_internet") else []
print(json.dumps({{"result": [{{"expressions": [{{"value": value}}]}}]}}))
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def test_run_policies_maps_opa_output_to_findings(fake_opa, tmp_path):
    script, log = fake_opa
    policies = tmp_path / "policies"
    policies.mkdir()
    settings = AgentSettings(_env_file=None, opa_binary=str(script), policies_dir=str(policies))

    report = run_policies(parse_unified_diff(TFVARS_DIFF), settings)

    assert report.ran and report.notes == []
    rules = {(f.rule, f.severity, f.line, f.source) for f in report.findings}
    assert ("internet_access_001", "critical", 5, "opa") in rules  # line of allow_internet
    assert ("TF_TEST", "high", 5, "opa") in rules

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all("--v0-compatible" in c["args"] for c in calls)
    # the request policy sees the whole visible file, incl. the unchanged `isolated = true`
    assert calls[1]["input"]["request"]["resources"] == {"isolated": True, "allow_internet": True}


def test_opa_compile_error_becomes_a_note(tmp_path):
    script = tmp_path / "opa"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stderr.write('rego_parse_error')\nsys.exit(1)\n"
    )
    script.chmod(0o755)
    (tmp_path / "p").mkdir()
    settings = AgentSettings(
        _env_file=None, opa_binary=str(script), policies_dir=str(tmp_path / "p")
    )
    report = run_policies(parse_unified_diff(TFVARS_DIFF), settings)
    assert report.findings == []
    assert any("rego_parse_error" in n for n in report.notes)


def test_rego_regexes_are_re2_compatible():
    """OPA uses Go RE2: no lookaround or backreferences. Catch that before CI does."""
    patterns = re.findall(r'"(?:regex|unless)":\s*`([^`]*)`', REGO.read_text())
    assert len(patterns) >= 10
    for pattern in patterns:
        assert not re.search(r"\(\?[=!<]|\\[1-9]", pattern), pattern
        re.compile(pattern)
