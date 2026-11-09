# InfraForge — code-level security rules for merge requests.
#
# Evaluated by the AI MR reviewer (agents/policy_check.py) on the lines ADDED by an MR.
# Unlike infrastructure.rego (which checks a provisioning *request*), these rules
# check *code*: Terraform, Ansible, CI config, Python and React.
#
# Input:
#   {"files": [{"path": "terraform/main.tf", "kind": "terraform",
#               "added": [{"line": 12, "text": "  password = \"hunter2\""}]}]}
# Output: data.infraforge.terraform_security.deny — a set of findings.
#
# `import rego.v1` keeps this file valid on OPA 0.59+ and 1.x (incl. --v0-compatible).
package infraforge.terraform_security

import rego.v1

# kind -> rule_id -> {regex, severity, message, fix[, unless]}
# Regexes are RE2 (Go): no lookahead; use "unless" to exclude safe variants.
rules := {
	"terraform": {
		"TF_HARDCODED_SECRET": {
			"regex": `(?i)^\s*[a-z0-9_]*(password|secret|token|api_key|private_key)[a-z0-9_]*\s*=\s*"[^"$]{4,}"`,
			"severity": "high",
			"message": "Hardcoded credential in Terraform code or tfvars.",
			"fix": "Use a sensitive variable fed from a CI/CD variable (TF_VAR_...) or a secret store.",
		},
		"TF_OPEN_CIDR": {
			"regex": `0\.0\.0\.0/0|::/0`,
			"severity": "high",
			"message": "Network rule open to the whole internet.",
			"fix": "Restrict the CIDR to the campus/VLAN ranges that need access.",
		},
		"TF_PREVENT_DESTROY_OFF": {
			"regex": `prevent_destroy\s*=\s*false`,
			"severity": "medium",
			"message": "prevent_destroy disabled; a plan could delete the VM and its data.",
			"fix": "Keep prevent_destroy = true for stateful resources, or document why not.",
		},
	},
	"ansible": {
		"ANSIBLE_NO_CERT_CHECK": {
			"regex": `(?i)validate_certs\s*:\s*(no|false)`,
			"severity": "high",
			"message": "TLS certificate validation disabled.",
			"fix": "Remove validate_certs: no and trust the right CA instead.",
		},
		"ANSIBLE_CURL_PIPE_SHELL": {
			"regex": `(curl|wget)[^|]*\|\s*(sudo\s+)?(ba)?sh`,
			"severity": "high",
			"message": "Downloads and executes a remote script without verification.",
			"fix": "Use a package/apt module or download, checksum-verify, then run.",
		},
		"ANSIBLE_WORLD_WRITABLE": {
			"regex": `mode\s*:\s*['"]?0?777`,
			"severity": "high",
			"message": "World-writable file permissions.",
			"fix": "Use the least permissive mode that works (e.g. 0644 / 0755).",
		},
	},
	"ci": {
		"CI_AUTO_APPLY": {
			"regex": `terraform\s+apply.*-auto-approve`,
			"severity": "critical",
			"message": "terraform apply with -auto-approve bypasses the manual review gate.",
			"fix": "Keep apply behind `when: manual` on a reviewed plan file.",
		},
		"CI_ERRORS_SWALLOWED": {
			"regex": `(pytest|terraform (validate|plan)|ansible-lint|detect-secrets)[^#\n]*\|\|\s*(true|echo)`,
			"severity": "medium",
			"message": "Check failure is swallowed, so the job can never fail.",
			"fix": "Remove `|| true` / `|| echo`; use allow_failure: true if the job is advisory.",
		},
	},
	"python": {
		"PY_TLS_VERIFY_OFF": {
			"regex": `verify\s*=\s*False`,
			"severity": "high",
			"message": "TLS verification disabled for an HTTP client.",
			"fix": "Keep verification on; configure the CA bundle if needed.",
		},
		"PY_UNSAFE_YAML": {
			"regex": `yaml\.load\(`,
			"unless": `SafeLoader|CSafeLoader`,
			"severity": "high",
			"message": "yaml.load without SafeLoader can execute arbitrary code.",
			"fix": "Use yaml.safe_load().",
		},
		"PY_SHELL_TRUE": {
			"regex": `subprocess\.[a-z_]+\(.*shell\s*=\s*True`,
			"severity": "medium",
			"message": "subprocess with shell=True; injection risk if input is user-controlled.",
			"fix": "Pass an argument list and keep shell=False.",
		},
	},
	"react": {
		"JS_DANGEROUS_HTML": {
			"regex": `dangerouslySetInnerHTML`,
			"severity": "high",
			"message": "Raw HTML injection; XSS if the content is not sanitised.",
			"fix": "Render text, or sanitise with DOMPurify before injecting.",
		},
		"JS_TOKEN_IN_LOCALSTORAGE": {
			"regex": `(?i)localStorage\.setItem\([^)]*(token|jwt|secret)`,
			"severity": "medium",
			"message": "Auth token stored in localStorage is readable by any injected script.",
			"fix": "Prefer an httpOnly cookie set by the backend.",
		},
	},
}

# The rule set for "terraform" also applies to tfvars; policy files have no rules here.
deny contains finding if {
	some file in input.files
	some rule_id, rule in rules[file.kind]
	some added in file.added
	regex.match(rule.regex, added.text)
	not allowlisted(added.text)
	not excluded(rule, added.text)
	finding := {
		"rule": rule_id,
		"file": file.path,
		"line": added.line,
		"severity": rule.severity,
		"message": rule.message,
		"fix": rule.fix,
	}
}

allowlisted(text) if contains(text, "opa:allow")

excluded(rule, text) if regex.match(rule.unless, text)
