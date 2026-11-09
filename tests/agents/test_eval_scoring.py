"""Scoring logic of the evaluation script (so reported numbers can be trusted)."""

from tests.agent_eval.rego_replica import load_rules
from tests.agent_eval.run_eval import keyword_hit, load_cases, match_findings, same_file, summarize


def test_same_file_handles_prefixes():
    assert same_file("terraform/main.tf", "terraform/main.tf")
    assert same_file("orchestrator/tests/test_x.py", "tests/test_x.py")
    assert not same_file("terraform/main.tf", "main.tf.bak")
    assert not same_file(None, "a.py")


def test_match_findings_counts_tp_fp_and_found():
    expected = [{"file": "a.py", "lines": [10, 11]}, {"file": "b.tf", "lines": [3]}]
    findings = [
        {"file": "a.py", "line": 12},  # within tolerance -> TP
        {"file": "a.py", "line": 10},  # second hit on the same issue -> TP, not new "found"
        {"file": "c.js", "line": 1},  # FP
    ]
    found, tp, fp, _ = match_findings(expected, findings)
    assert (found, tp, fp) == (1, 2, 1)


def test_keyword_hit():
    assert keyword_hit(["TF_VAR"], "set tf_var_x") is True
    assert keyword_hit(["x"], "nothing") is False
    assert keyword_hit([], "anything") is None


def test_eval_set_is_well_formed():
    cases = load_cases()
    assert len(cases) >= 10
    inv = [c for c in cases if c.agent == "investigator"]
    rev = [c for c in cases if c.agent == "reviewer"]
    assert len(inv) >= 10 and len(rev) >= 5
    assert any(not c.meta["expected"]["findings"] for c in rev), "need clean diffs for noise"
    for c in inv:
        assert c.read("job.log").strip(), c.id
        assert c.meta["expected"]["category"]
    for c in rev:
        assert c.read("mr.diff").startswith("diff --git"), c.id


def test_summary_rates():
    results = [
        {"agent": "investigator", "correct": True, "file_correct": None, "keyword_hit": None,
         "fallback": False, "latency_seconds": 1, "usage": {"cost_usd": 0.02, "input_tokens": 1,
                                                             "output_tokens": 1, "calls": 1}},
        {"agent": "reviewer", "expected": 1, "found": 1, "reported": 2, "true_positives": 1,
         "false_positives": 1, "clean_case": False, "fallback": False, "latency_seconds": 3,
         "usage": {"cost_usd": 0.04, "input_tokens": 1, "output_tokens": 1, "calls": 1}},
    ]  # fmt: skip
    s = summarize(results)
    assert s["investigator"]["category_accuracy"] == 1.0
    assert s["reviewer"]["false_positive_rate"] == 0.5
    assert s["cost"]["per_run_usd"] == 0.03


def test_rego_replica_reads_every_rule():
    rules = load_rules()
    assert {"terraform", "ansible", "ci", "python", "react"} <= set(rules)
    assert all(
        {"regex", "severity", "message", "fix"} <= set(r)
        for k in rules.values()
        for r in k.values()
    )
