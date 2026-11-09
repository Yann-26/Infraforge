# AI agents

InfraForge has a small, safe "AI agents" layer that helps engineers ship changes with less
friction. It has three parts:

| Part | What it does | Uses an LLM |
|---|---|---|
| **CI failure investigator** | When a job fails on an MR, it explains why and suggests a fix in an MR comment | Yes (Claude) |
| **MR reviewer** | Reviews Terraform, Ansible, Python and React changes and comments only high-severity findings, plus OPA policy violations | Yes (Claude) + OPA |
| **Code quality checks** | Complexity, function/file size, import cycles, leaked secrets | No |

## Safety guarantees

These hold no matter what the model returns:

- **Human in the loop.** Agents can only **comment** on MRs. They never push, merge, approve,
  retry jobs or run `terraform apply`. `agents/gitlab_client.py` has an endpoint allow-list
  (read pipelines, job logs, MR diffs and notes; create a note). Any other request is
  refused before it is sent, and a unit test covers merge/approve/push/variables endpoints.
- **Never blocks the pipeline.** Every agent job has `allow_failure: true` and a timeout,
  and `python -m agents` always exits 0, even if it crashes. The failed job keeps its own red
  status; the agent only adds information.
- **Least privilege.** The agents use their own token, `AGENT_GITLAB_TOKEN`, not
  `GITLAB_TOKEN`. See [Setup](#setup).
- **Secrets stay out of prompts.** Logs and diffs are redacted before they reach the model
  (see [Redaction](#redaction)). Keys are read only from CI/CD variables.
- **Structured, validated output.** The model must return JSON that passes a strict Pydantic
  schema. Its claims are then checked against the real log and diff before anything is posted.

```
             ┌──────────── GitLab CI (MR pipeline) ────────────┐
 job fails ─►│ ai_investigate_failure (.post, when: on_failure) │
 MR opened ─►│ ai_mr_review (test stage)                        │
             └───────┬─────────────────────────────────────────┘
                     │  python -m agents investigate|review
                     ▼
   GitLab API (read) ─► clean + tail + size-limit ─► REDACT ─► versioned prompt
                                                                  │
                         heuristics / OPA (no LLM) ◄──────────────┤
                                                                  ▼
                                    Claude ─► JSON ─► Pydantic ─► retry once ─► fallback
                                                                  │
                          verify evidence / lines ◄───────────────┘
                                     │
                                     ▼
                     one MR note (deduplicated) + metrics ─► Pushgateway ─► Grafana
```

## How each agent works

### CI failure investigator (`agents/investigator.py`)

1. **Trigger.** Job `ai_investigate_failure` runs in the built-in `.post` stage with
   `when: on_failure`, so it only runs when an earlier job in an MR pipeline failed.
2. **Collect.** It lists the pipeline's failed jobs (skipping `ai_*` jobs and preferring
   jobs that are not `allow_failure`), downloads up to `AGENT_MAX_FAILED_JOBS` job logs, and
   fetches the MR title and diff.
3. **Prepare.** It strips ANSI colours and GitLab section markers, keeps the last
   `AGENT_LOG_TAIL_LINES` lines (and at most `AGENT_MAX_LOG_CHARS` characters), caps the
   diff at `AGENT_MAX_DIFF_CHARS` by dropping whole files, then redacts.
4. **Classify.** Claude gets `agents/prompts/investigator.v1.md` and returns a `Diagnosis`:
   category (`terraform_plan`, `terraform_validate`, `ansible_task`, `test_failure`,
   `lint_policy`, `dependency`, `flaky_infra`, `unknown`), summary, explanation, evidence log
   lines, file and line, suggested fix, optional patch, and confidence.
5. **Verify.** Every evidence line must appear in the log. Lines that don't are dropped; if
   none remain, the diagnosis is discarded. A file the model names that appears in neither
   the log nor the diff is removed.
6. **Comment.** One note per pipeline, with category, location, confidence, explanation,
   the evidence lines it relied on, the suggested fix and an optional patch, plus tokens and
   cost. Duplicates are prevented with a hidden marker.

### MR reviewer (`agents/reviewer.py`)

1. **Trigger.** Job `ai_mr_review` runs in MR pipelines, in parallel (`needs: []`).
2. **Scope.** Only Terraform (`.tf`, `.tfvars`), Ansible (`ansible/**/*.yml`), Python,
   React (`.js/.jsx/.ts/.tsx`), Rego and CI files are reviewed. Lock files, build output and
   binaries are skipped. If nothing is reviewable, no tokens are spent and no comment is posted.
3. **Deterministic checks** (`agents/policy_check.py`, no LLM):
   - **Existing OPA request policies** (`policies/opa/infrastructure.rego`). Each changed
     `terraform/environments/*/terraform.tfvars` is mapped to `input.request` and evaluated,
     so for example *isolated + allow_internet* (`internet_access_001`) is caught at review
     time, not at provisioning time.
   - **Code security rules** (`policies/opa/terraform_security.rego`, with unit tests in
     `terraform_security_test.rego`), applied to added lines: hardcoded credentials, `0.0.0.0/0`,
     `terraform apply -auto-approve`, checks swallowed with `|| true`, `verify=False`, unsafe
     `yaml.load`, `curl | sh`, world-writable modes, `dangerouslySetInnerHTML`, tokens in
     `localStorage`. Add `opa:allow` on a line to accept a deliberate exception.
4. **AI review.** Claude gets `agents/prompts/reviewer.v1.md`, with the diff (new-file line
   numbers), the OPA policy sources, and the deterministic findings so it doesn't repeat them.
   It returns a `Review` with findings (file, line, severity, rule, reason, suggested fix,
   confidence).
5. **Noise filter.** OPA findings are kept if they are at or above the threshold. AI findings
   are dropped if they are below `AGENT_SEVERITY_THRESHOLD` (default `high`), below
   `AGENT_MIN_CONFIDENCE` (0.7), not on a line added by the MR (snapped if within 2 lines),
   duplicates, or beyond `AGENT_MAX_FINDINGS` (10). The comment shows how many were dropped
   and why.
6. **Comment.** One note per commit SHA, and only when there is something to say: at least
   one finding, or the AI review was unavailable. A clean MR gets no comment.

### Code quality checks (`quality/run_quality.py`)

These are plain scripts with no LLM, run in job `code_quality`. **All thresholds are in
`quality/quality.yml`.**

| Check | Tool | Default threshold |
|---|---|---|
| Python cyclomatic complexity | radon | 15 per function |
| Python function size | `ast` | 80 lines |
| Python file size | — | 500 lines |
| Python import cycles | import-linter (`acyclic_siblings`) | none allowed |
| JS/React complexity, function and file size | eslint 9 (generated config) | 15 / 150 / 400 lines |
| JS/React circular imports | madge | none allowed |
| Leaked secrets | `agents/redact.py` patterns + detect-secrets | none allowed |

The job writes `quality-report.json` and a GitLab Code Quality report, so violations appear
in the MR widget. It is **report-only** for now (`allow_failure: true`). Run against the
current GitLab code, it reports 9 Python, 16 frontend and 43 secret findings. Remove
`allow_failure` once those are fixed or the thresholds are agreed. The secret scan never
prints the secret value. To accept a known false positive, add
`# pragma: allowlist secret` on that line.

## How output is validated

1. **Schema.** `agents/schemas.py` defines strict Pydantic models: unknown fields are
   rejected, categories and severities are enums, and confidence must be in 0–1. The JSON
   Schema is generated from these models and embedded in the prompt, so prompt and validator
   cannot drift apart.
2. **Parse.** `agents/structured.py` extracts the JSON object (tolerating ```json fences)
   and validates it.
3. **Retry once.** If validation fails, the model is asked again with the exact validation
   errors (`AGENT_VALIDATION_RETRIES=1`).
4. **Fallback.** If it fails again, the result is `invalid_output` and the agent falls back
   (below).
5. **Grounding checks.** The investigator's evidence must appear in the log and its file
   must appear in the log or diff. The reviewer's findings must point at added lines. The
   model cannot mark its own findings as OPA findings.

## How failures are handled

| What goes wrong | Investigator | Reviewer | Pipeline |
|---|---|---|---|
| `ANTHROPIC_API_KEY` missing | "unavailable" comment + rule-based guess | OPA findings only + "AI reviewer unavailable" | unchanged |
| LLM timeout / 429 / 5xx | SDK retries with exponential backoff (`AGENT_LLM_MAX_RETRIES`), then "unavailable" comment | same, OPA findings still shown | unchanged |
| Model refuses | server-side fallback model (`AGENT_REFUSAL_FALLBACK`), else "unavailable" | same | unchanged |
| Output hits `max_tokens` / is empty | "unavailable" (tokens still counted) | same | unchanged |
| Invalid JSON twice | "unavailable" | same | unchanged |
| Evidence not found in log | "unavailable" (not posted as a diagnosis) | — | unchanged |
| `opa` missing / policy compile error | — | AI review continues; comment notes which checks did not run | unchanged |
| GitLab API down | logged; comment skipped | logged; comment skipped | unchanged |
| Pushgateway down | metrics kept in `agent-metrics.prom` artifact | same | unchanged |
| Bug / crash in agent code | logged; exit 0 | logged; exit 0 | unchanged |

The "unavailable" comment names the reason (for example "the LLM request timed out"). For
the investigator, it also includes the rule-based guess from `agents/heuristics.py`, so the
engineer still gets a pointer.

## Redaction

`agents/redact.py` runs on exactly the text that will be sent, after truncation. It replaces:

- private key blocks; GitLab (`glpat-` etc.), GitHub, Anthropic, OpenAI-style, AWS, Slack
  tokens; JWTs; `Bearer`/`token` headers; credentials in URLs (`user:pass@host`);
- `password=`, `secret:`, `api_key`, `token`, `client_secret`… assignments (the key is
  kept, the value replaced). Code references (`settings.token`, `var.x`, `${VAR}`,
  `{{ vault }}`) and file paths are left alone so the model can still read the code;
- the **values of any environment variable whose name looks secret** (all masked CI
  variables like `AGENT_GITLAB_TOKEN`, `SMTP_PASSWORD`…), wherever they appear;
- IPv4 addresses → `[PRIVATE_IP]` / `[IP]`, keeping the prefix length. `0.0.0.0/0` and
  `127.0.0.1` are kept because they matter for review (`AGENT_REDACT_IPS=false` disables
  IP redaction).

The prompts tell the model that `[REDACTED]` is expected, and that log/diff content is
untrusted data whose instructions must be ignored (prompt-injection defence). Untrusted
content is wrapped in tags that it cannot close from inside.

## Cost and limits

Every setting is an environment variable (CI/CD variable); defaults are in `agents/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `AGENT_MODEL` | `claude-opus-5-5` | Claude model. `claude-sonnet-5-5` is about half the price |
| `AGENT_EFFORT` | `medium` | Reasoning effort (`low`/`medium`/`high`) |
| `AGENT_MAX_OUTPUT_TOKENS` | 8000 | Max output per call |
| `AGENT_LLM_TIMEOUT_SECONDS` | 60 | Per request |
| `AGENT_LLM_MAX_RETRIES` | 2 | Transport retries, exponential backoff |
| `AGENT_VALIDATION_RETRIES` | 1 | Retries on invalid JSON |
| `AGENT_LOG_TAIL_LINES` / `AGENT_MAX_LOG_CHARS` | 300 / 30000 | Per job log |
| `AGENT_MAX_DIFF_CHARS` | 60000 | Diff sent to the model |
| `AGENT_MAX_FAILED_JOBS` | 3 | Logs per investigation |
| `AGENT_SEVERITY_THRESHOLD` | `high` | Reviewer findings shown |
| `AGENT_MIN_CONFIDENCE` | 0.7 | Reviewer AI findings shown |
| `AGENT_MAX_FINDINGS` | 10 | Reviewer findings per comment |
| `AGENT_REFUSAL_FALLBACK` | `true` | Server-side refusal fallback (beta) |
| `AGENT_PUSHGATEWAY_URL` | — | Where to push metrics |

Worst case per run is bounded: at most `1 + AGENT_VALIDATION_RETRIES` calls, about 25k
input tokens and 8k output tokens each. Every run logs its tokens and estimated cost
(`run report: {...}` in the job log and `agent-report.json`), shows them in the MR comment,
and exports them as metrics. Use a dedicated Anthropic API key with a monthly spend limit.

## Metrics and dashboard

Each run pushes to the Pushgateway under its own group (`agent`, `pipeline`, `ci_job`), so
`sum(...)` gives totals across runs. Exported metrics:

| Metric | Labels |
|---|---|
| `infraforge_agent_runs_total` | agent, outcome (`ok`/`fallback`/`skipped`) |
| `infraforge_agent_failures_total` | agent, reason |
| `infraforge_agent_fallbacks_total` | agent, reason |
| `infraforge_agent_latency_seconds` / `_llm_latency_seconds` | agent (histograms) |
| `infraforge_agent_tokens_total` | agent, direction |
| `infraforge_agent_cost_usd_total`, `infraforge_agent_llm_calls_total` | agent |
| `infraforge_agent_findings_total` | agent, severity, source (`llm`/`opa`) |
| `infraforge_agent_findings_filtered_total` | agent, reason |
| `infraforge_agent_comments_posted_total` | agent |
| `infraforge_agent_last_run_timestamp_seconds` | agent |
| `infraforge_agent_suggestions` | agent, verdict (`accepted`/`rejected`/`ignored`) |

**Accepted vs ignored suggestions.** Every comment asks for 👍/👎. The scheduled job
`ai_agents_feedback` (`python -m agents feedback --days 30`) counts reactions on agent
comments: more 👍 means accepted, more 👎 means rejected, no reaction means ignored.

The Grafana dashboard **InfraForge — AI Agents**
(`observability/grafana/dashboards/ai-agents.json`) is provisioned automatically by the
existing Grafana file provider. It shows runs, runs in the last 24h, fallback rate, latency,
cost, acceptance rate, failures/fallbacks by reason, tokens and cost per run, findings by
severity/source, and findings filtered as noise.

## Evaluation

`tests/agent_eval/` holds 22 realistic cases built from the InfraForge code: 14 failed
pipelines (every category, including misleading ones) and 8 MR diffs (3 of them clean, to
measure noise). See `tests/agent_eval/README.md` for the format.

```bash
make -f agents.mk agents-eval       # heuristic baseline: offline, free
make -f agents.mk agents-eval-llm   # real Claude calls (needs ANTHROPIC_API_KEY)
```

The report gives:
- **investigator:** category accuracy, file accuracy, fix-keyword hit rate, fallback rate;
- **reviewer:** recall, precision, false-positive rate (noise), clean diffs with any finding;
- **both:** LLM calls, tokens, **cost per run**, latency.

Full per-case JSON goes to `tests/agent_eval/results/`. The committed offline baseline is
in `tests/agent_eval/BASELINE_HEURISTIC.md`. Re-run the LLM eval before changing a prompt
version, the model, or the thresholds, and compare.

## Setup

These steps assume the files here are merged into the GitLab repository at the same paths.

1. **CI include.** Add to `.gitlab-ci.yml`:
   ```yaml
   include:
     - local: ci/agents.gitlab-ci.yml
   ```
2. **Agent token.** In *Settings → Access tokens*, create a **project access token**
   named `infraforge-ai-agent`, with role **Reporter**, scope **`api`**, and an expiry date.
   GitLab has no narrower scope that can post MR notes; the agent code compensates with its
   endpoint allow-list. Add it as the CI/CD variable `AGENT_GITLAB_TOKEN`: masked, not protected.
3. **Claude key.** Add `ANTHROPIC_API_KEY` as a masked CI/CD variable. Use a dedicated key
   with a spend limit.
   > MR pipelines on feature branches cannot read *protected* variables, so both variables
   > must be unprotected. Anyone who can push a branch can therefore read them through a
   > modified job. That is why the token is limited to Reporter + comment-only usage and the
   > API key has a spend limit. Pipelines for MRs from forks don't get these variables.
4. **Metrics (optional).** Start the Pushgateway, add its URL as `AGENT_PUSHGATEWAY_URL`
   (reachable from your runners), and add to `observability/prometheus/prometheus.yml`:
   ```yaml
   scrape_config_files:
     - /etc/prometheus/scrape_configs/*.yml
   ```
5. **Feedback (optional).** In *CI/CD → Schedules*, add a daily schedule with the variable
   `AGENT_FEEDBACK=true`.
6. **Makefile.** Add `include agents.mk` to the main `Makefile`.

## Running locally

```bash
python3 -m venv .venv && . .venv/bin/activate
make -f agents.mk agents-install         # agent + quality dependencies
make -f agents.mk agents-test            # unit tests
make -f agents.mk agents-lint            # ruff / black / isort
make -f agents.mk quality                # code quality checks

# Investigate a saved job log / review a diff — prints the comment, posts nothing
make -f agents.mk agents-investigate-local LOG=tests/agent_eval/cases/inv-04-ansible-package-typo/job.log
git diff main... > /tmp/mr.diff && make -f agents.mk agents-review-local DIFF=/tmp/mr.diff

# Metrics stack: Pushgateway + Prometheus + Grafana (dashboard "InfraForge — AI Agents")
make -f agents.mk agents-up
AGENT_PUSHGATEWAY_URL=http://localhost:9091 python -m agents investigate --log-file job.log --dry-run
```

Without `ANTHROPIC_API_KEY` the local commands show the fallback path, which is useful to
check what engineers will see when the LLM is down.

## Changing prompts

Prompts live in `agents/prompts/<agent>.<version>.md`. Each file has a `<<<SYSTEM>>>` and a
`<<<USER>>>` section with `$placeholders`. To change one:

1. copy it to the next version,
2. bump `PROMPT_VERSION` in the agent module,
3. run `make -f agents.mk agents-eval-llm` and compare with the previous run.

The prompt ID appears in every comment and run report, so results can be traced to a prompt.

## Known limitations

- **No LLM eval numbers yet.** The eval set and runner are done, but the committed baseline
  is the no-LLM heuristic. Run `agents-eval-llm` to get Claude's accuracy, noise and cost
  on your account. The heuristic's 100% category accuracy is optimistic because two of its
  rules were fixed while the set was built.
- **The eval set is small** (22 cases) and synthetic-but-realistic. Add real failures as
  they happen; a single case changes accuracy by about 7 points.
- **Feedback is a proxy.** 👍/👎 measure perceived usefulness. A fix that was applied without
  a reaction counts as "ignored".
- **The reviewer sees only the diff,** not the whole repository. It can miss issues that need
  context from unchanged files, and the tfvars policy check sees only the lines visible in the
  diff hunks.
- **The investigator sees only the log tail.** An early root cause in a very long log may be
  cut off (raise `AGENT_LOG_TAIL_LINES`).
- **Redaction is pattern-based.** It is deliberately aggressive, but an unusual secret format
  could slip through. The secret scan uses the same patterns, so a miss in one is a miss in both.
- **Token scope.** GitLab's `api` scope is broader than "comment only". The allow-list is
  enforced in our code, not by GitLab.
- **Pushgateway groups accumulate** (one per run). That keeps `sum()` correct, but nothing
  expires them. Wipe the Pushgateway volume, or delete old groups through its API, to reset.
- **Existing pipeline.** Most current jobs end in `|| true` / `|| echo`, so they never fail
  and the investigator never triggers. It becomes useful as those jobs are made blocking.
  The `python_tests` job looks in `orchestrator/tests/`, but there are no tests there yet.
- **OPA versions.** The existing policies use pre-1.0 Rego syntax, and docker-compose uses
  `openpolicyagent/opa:latest` (1.x). The agents pass `--v0-compatible`. Also,
  `infrastructure.rego` declares `default violations = []` next to a partial set rule
  `violations[...]`, which OPA may reject at compile time. If so, the reviewer notes that the
  request policies did not run, and `opa_policy_tests` shows the error.
