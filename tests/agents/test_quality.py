"""Code quality script: thresholds, secret scan and reports (Python checks only).

The frontend checks need Node.js (npx eslint/madge) and are exercised in CI.
"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location("run_quality", "quality/run_quality.py")
rq = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rq)

COMPLEX = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(20))
LONG = "def g():\n" + "    x = 1\n" * 100


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "ROOT", tmp_path)
    cfg = yaml.safe_load(Path("quality/quality.yml").read_text())
    cfg["python"]["paths"] = ["pkg"]
    cfg["python"]["import_cycles"] = [{"root": ".", "package": "pkg"}]
    cfg["secrets"]["detect_secrets"] = False
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    config = tmp_path / "quality.yml"
    config.write_text(yaml.safe_dump(cfg))
    return tmp_path, config


def checks(results):
    return {r.name: r for r in results}


def test_clean_code_passes(repo):
    root, config = repo
    (root / "pkg" / "ok.py").write_text("def ok():\n    return 1\n")
    results = checks(rq.run(config, {"python"}))
    assert results["python-complexity"].status == "ok"
    assert results["python-function-size"].status == "ok"


def test_complexity_function_and_file_size_violations(repo):
    root, config = repo
    (root / "pkg" / "bad.py").write_text(COMPLEX + "\n" + LONG + "\n" * 600)
    results = checks(rq.run(config, {"python"}))
    assert "complexity 21" in results["python-complexity"].violations[0].message
    assert "function g has 101 lines" in results["python-function-size"].violations[0].message
    assert results["python-file-size"].violations[0].path == "pkg/bad.py"


@pytest.mark.skipif(shutil.which("lint-imports") is None, reason="import-linter not installed")
def test_import_cycle_is_detected(repo, monkeypatch):
    root, config = repo
    (root / "pkg" / "a.py").write_text("from pkg import b\n")
    (root / "pkg" / "b.py").write_text("from pkg import a\n")
    monkeypatch.syspath_prepend(str(root))
    result = checks(rq.run(config, {"python"}))["python-import-cycles"]
    assert result.status == "failed"


def test_secret_scan_respects_excludes_and_allowlist(repo):
    root, config = repo
    fake = "glpat" + "-" + "Zz9" * 8
    (root / "app.py").write_text(f'TOKEN = "{fake}"\nOK = "{fake}"  # pragma: allowlist secret\n')
    (root / "tests" / "agents").mkdir(parents=True)
    (root / "tests" / "agents" / "fixture.py").write_text(f'T = "{fake}"\n')
    result = checks(rq.run(config, {"secrets"}))["secrets"]
    assert [(v.path, v.line) for v in result.violations] == [("app.py", 1)]
    assert fake not in result.violations[0].message  # never echo the secret


def test_reports_are_written_in_gitlab_codequality_format(repo):
    root, config = repo
    (root / "pkg" / "bad.py").write_text(COMPLEX)
    rq.run(config, {"python"})
    report = json.loads((root / "gl-code-quality-report.json").read_text())
    entry = report[0]
    assert set(entry) == {"description", "check_name", "fingerprint", "severity", "location"}
    assert entry["location"] == {"path": "pkg/bad.py", "lines": {"begin": 1}}
    summary = json.loads((root / "quality-report.json").read_text())
    assert any(c["name"] == "python-complexity" and c["violations"] == 1 for c in summary["checks"])


def test_missing_paths_are_skipped_not_failed(repo):
    _, config = repo
    results = checks(rq.run(config, {"frontend"}))
    assert results["frontend-eslint"].status == "skipped"


def test_exit_code(repo, monkeypatch):
    root, config = repo
    (root / "pkg" / "bad.py").write_text(COMPLEX)
    assert rq.main(["--config", str(config), "--only", "python"]) == 1
    (root / "pkg" / "bad.py").write_text("x = 1\n")
    assert rq.main(["--config", str(config), "--only", "python"]) == 0
