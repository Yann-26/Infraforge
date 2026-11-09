# Run with:  opa test policies/opa -v
package infraforge.terraform_security_test

import rego.v1

import data.infraforge.terraform_security

file(kind, path, text) := {"path": path, "kind": kind, "added": [{"line": 1, "text": text}]}

rules_hit(f) := {d.rule | some d in terraform_security.deny with input as {"files": [f]}}

test_hardcoded_tf_secret if {
	rules_hit(file("terraform", "terraform/terraform.tfvars", `proxmox_token_secret = "abc123"`)) == {"TF_HARDCODED_SECRET"} # pragma: allowlist secret
}

test_tf_variable_reference_is_fine if {
	count(rules_hit(file("terraform", "main.tf", `password = var.db_password`))) == 0
}

test_tf_interpolation_is_fine if {
	count(rules_hit(file("terraform", "main.tf", `token = "${var.token}"`))) == 0
}

test_open_cidr if {
	rules_hit(file("terraform", "main.tf", `  cidr_blocks = ["0.0.0.0/0"]`)) == {"TF_OPEN_CIDR"}
}

test_auto_apply_in_ci if {
	rules_hit(file("ci", ".gitlab-ci.yml", `    - terraform apply plan.tfplan -auto-approve`)) == {"CI_AUTO_APPLY"}
}

test_ansible_curl_pipe if {
	rules_hit(file("ansible", "ansible/playbook.yml", `      shell: curl -fsSL https://get.example.sh | sudo bash`)) == {"ANSIBLE_CURL_PIPE_SHELL"}
}

test_python_verify_false if {
	rules_hit(file("python", "app/services/netbox.py", `r = httpx.get(url, verify=False)`)) == {"PY_TLS_VERIFY_OFF"}
}

test_safe_yaml_is_fine if {
	count(rules_hit(file("python", "x.py", `yaml.load(f, Loader=yaml.SafeLoader)`))) == 0
}

test_allowlist_comment if {
	count(rules_hit(file("terraform", "main.tf", `cidr = "0.0.0.0/0" # opa:allow public LB`))) == 0
}

test_unknown_kind_is_ignored if {
	count(rules_hit(file("other", "README.md", `password = "hunter22"`))) == 0 # pragma: allowlist secret
}
