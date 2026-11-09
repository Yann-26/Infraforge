# Agent evaluation — mode `heuristic`

> **Read this first.** This is the *no-LLM baseline* (rule-based classifier + deterministic
> OPA/security rules, `opa` replaced by `rego_replica.py` because it was not installed).
> Two heuristic rules were corrected while this eval set was built, so the 100% category
> accuracy is optimistic: the heuristic has effectively seen these cases. The numbers that
> matter for the LLM are file accuracy, fix quality (keyword hit) and reviewer recall on
> issues no rule can catch (`rev-01`: missing authz + SQL injection, `rev-02`: needs real
> OPA). LLM results: run `make -f agents.mk agents-eval-llm` with `ANTHROPIC_API_KEY` set.

_Generated 2026-11-09 17:56 UTC by tests/agent_eval/run_eval.py._

| Metric | Value |
|---|---|
| Investigator cases | 14 |
| Investigator category accuracy | 100% |
| Investigator file accuracy | 36% |
| Investigator fix mentions expected keyword | n/a |
| Investigator fallback rate | 0% |
| Reviewer cases | 8 (3 clean diffs) |
| Reviewer recall | 50% |
| Reviewer precision | 100% |
| Reviewer false-positive rate (noise) | 0% |
| Clean diffs with any finding | 0 / 3 |
| LLM calls / tokens in / out | 0 / 0 / 0 |
| Cost total / per run | $0.0000 / $0.0000 |
| Latency mean / max | 0.0s / 0.0s |

## Investigator cases

| Case | Expected | Predicted | File | Fallback |
|---|---|---|---|---|
| `inv-01-tf-unsupported-argument` | terraform_validate | ✅ terraform_validate | ✅ |  |
| `inv-02-tf-plan-missing-secret-variable` | terraform_plan | ✅ terraform_plan | – |  |
| `inv-03-tf-plan-invalid-vlan` | terraform_plan | ✅ terraform_plan | ❌ |  |
| `inv-04-ansible-package-typo` | ansible_task | ✅ ansible_task | ❌ |  |
| `inv-05-ansible-yaml-syntax` | ansible_task | ✅ ansible_task | ✅ |  |
| `inv-06-pytest-assertion` | test_failure | ✅ test_failure | ✅ |  |
| `inv-07-ansible-lint` | lint_policy | ✅ lint_policy | ✅ |  |
| `inv-08-eslint-hooks` | lint_policy | ✅ lint_policy | ❌ |  |
| `inv-09-secret-scan` | lint_policy | ✅ lint_policy | ❌ |  |
| `inv-10-flaky-dns` | flaky_infra | ✅ flaky_infra | – |  |
| `inv-11-flaky-image-pull` | flaky_infra | ✅ flaky_infra | – |  |
| `inv-12-dependency-version` | dependency | ✅ dependency | ❌ |  |
| `inv-13-terraform-fmt` | lint_policy | ✅ lint_policy | ❌ |  |
| `inv-14-pytest-collection-import-error` | test_failure | ✅ test_failure | ❌ |  |

## Reviewer cases

| Case | Expected | Found | Reported | FP |
|---|---|---|---|---|
| `rev-01-sqli-and-removed-authz` | 2 | 0 | 0 | 0 |
| `rev-02-isolated-with-internet` | 1 | 0 | 0 | 0 |
| `rev-03-hardcoded-proxmox-secret` | 1 | 1 | 1 | 0 |
| `rev-04-ci-auto-apply` | 1 | 1 | 1 | 0 |
| `rev-05-react-xss` | 1 | 1 | 1 | 0 |
| `rev-06-clean-python-refactor` | 0 | 0 | 0 | 0 |
| `rev-07-clean-react-loading-state` | 0 | 0 | 0 | 0 |
| `rev-08-clean-terraform-validation` | 0 | 0 | 0 | 0 |
