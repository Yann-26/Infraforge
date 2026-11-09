# Agent evaluation set

22 realistic cases built from the real InfraForge code (Terraform VM module and tfvars,
Ansible playbook, FastAPI routes/services, React pages, GitLab CI):

- `inv-*` (14): failed CI jobs for the **investigator**, one or more per category
  (Terraform validate/plan, Ansible task/syntax, pytest, lint/policy/secret scan,
  dependency, flaky infra), including hard ones (an import error that looks like a
  dependency problem, a Terraform error whose cause is in a tfvars file).
- `rev-*` (8): MR diffs for the **reviewer**: 5 with known issues and 3 clean diffs that
  must produce no findings (that is how noise is measured).

## Case format

```
cases/<id>/
  case.yml   agent, job_name/stage or mr_title, description, expected
  job.log    investigator only: raw job log (ANSI and all)
  mr.diff    unified diff of the MR
```

Investigator `expected`: `category` (must match exactly), optional `file` (path the
fix is in), optional `keywords` (at least one should appear in the explanation/fix).

Reviewer `expected.findings`: list of `{file, lines}` — a reported finding counts as a
true positive if it is in that file within ±1 line of the range. Anything else
reported is a false positive. An empty list means the diff is clean.

## Running

```bash
make -f agents.mk agents-eval        # heuristic baseline, offline, free
make -f agents.mk agents-eval-llm    # Claude; needs ANTHROPIC_API_KEY (~22 calls)
python -m tests.agent_eval.run_eval --mode llm --cases rev- --report /tmp/rev.md
```

Each run prints a markdown summary and writes full per-case JSON to `results/`
(git-ignored). Compare runs before changing a prompt version or the model.

Fake credentials in these files are intentional (they exercise redaction) and are
excluded from the secret scan in `quality/quality.yml`.
