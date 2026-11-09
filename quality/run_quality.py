#!/usr/bin/env python3
"""Code quality checks for InfraForge (no LLM).

Checks (thresholds in quality/quality.yml):
  python-complexity    radon cyclomatic complexity per function
  python-function-size lines per function (ast)
  python-file-size     lines per file
  python-import-cycles import-linter acyclic_siblings contract
  frontend-eslint      complexity / max-lines-per-function / max-lines
  frontend-cycles      madge --circular
  secrets              agents/redact.py patterns + detect-secrets

Exit code 1 if any violation is found (CI runs it report-only with allow_failure).
Writes a JSON report and a GitLab Code Quality report for the MR widget.
"""

import argparse
import ast
import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Violation:
    check: str
    path: str
    line: int
    message: str
    severity: str = "major"  # GitLab codequality: info|minor|major|critical|blocker


@dataclass
class CheckResult:
    name: str
    status: str = "ok"  # ok | failed | skipped | error
    detail: str = ""
    violations: list[Violation] = field(default_factory=list)

    def add(self, *args, **kwargs) -> None:
        self.violations.append(Violation(self.name, *args, **kwargs))
        self.status = "failed"


def _excluded(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(f"./{path}", p) for p in patterns)


def _rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def python_files(cfg: dict) -> list[Path]:
    files = []
    for base in cfg["paths"]:
        root = ROOT / base
        if root.is_dir():
            files += [p for p in root.rglob("*.py") if not _excluded(_rel(p), cfg["exclude"])]
    return sorted(files)


# --- Python -------------------------------------------------------------------


def check_python_complexity(cfg: dict, files: list[Path]) -> CheckResult:
    result = CheckResult("python-complexity")
    try:
        from radon.complexity import cc_visit
    except ImportError:
        result.status, result.detail = "skipped", "radon not installed"
        return result
    limit = cfg["max_cyclomatic_complexity"]
    for path in files:
        try:
            blocks = cc_visit(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            result.add(_rel(path), exc.lineno or 1, f"syntax error: {exc.msg}", "critical")
            continue
        for block in blocks:
            if block.complexity > limit:
                name = getattr(block, "fullname", block.name)
                msg = f"{name} has cyclomatic complexity {block.complexity} (max {limit})"
                result.add(_rel(path), block.lineno, msg)
    return result


def check_python_sizes(cfg: dict, files: list[Path]) -> list[CheckResult]:
    functions, modules = CheckResult("python-function-size"), CheckResult("python-file-size")
    max_fn, max_file = cfg["max_function_lines"], cfg["max_file_lines"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = text.count("\n") + 1
        if lines > max_file:
            modules.add(_rel(path), 1, f"file has {lines} lines (max {max_file})", "minor")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue  # reported by the complexity check
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = node.end_lineno - node.lineno + 1
                if size > max_fn:
                    msg = f"function {node.name} has {size} lines (max {max_fn})"
                    functions.add(_rel(path), node.lineno, msg, "minor")
    return [functions, modules]


def check_import_cycles(entries: list[dict]) -> CheckResult:
    result = CheckResult("python-import-cycles")
    lint_imports = shutil.which("lint-imports")
    if lint_imports is None:
        result.status, result.detail = "skipped", "import-linter not installed"
        return result
    ran = []
    for entry in entries:
        root = ROOT / entry["root"]
        package = entry["package"]
        if not (root / package / "__init__.py").exists():
            continue
        ran.append(package)
        config = (
            "[importlinter]\n"
            f"root_package = {package}\n\n"
            "[importlinter:contract:no-cycles]\n"
            f"name = {package} has no import cycles between sibling modules\n"
            "type = acyclic_siblings\n"
            f"ancestors = {package}\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as fh:
            fh.write(config)
        proc = subprocess.run(
            [lint_imports, "--config", fh.name, "--no-cache"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        Path(fh.name).unlink(missing_ok=True)
        if proc.returncode != 0:
            output = (proc.stdout + proc.stderr).strip()
            detail = "\n".join(output.splitlines()[-15:])
            result.add(f"{entry['root']}/{package}", 1, f"import cycle(s):\n{detail}")
    if not ran:
        result.status, result.detail = "skipped", "no configured package found"
    else:
        result.detail = f"checked: {', '.join(ran)}"
    return result


# --- Frontend -----------------------------------------------------------------


def _eslint_config(cfg: dict) -> str:
    rules = {
        "complexity": ["error", cfg["max_complexity"]],
        "max-lines-per-function": [
            "error",
            {"max": cfg["max_function_lines"], "skipBlankLines": True, "skipComments": True},
        ],
        "max-lines": [
            "error",
            {"max": cfg["max_file_lines"], "skipBlankLines": True, "skipComments": True},
        ],
    }
    exts = ",".join(cfg["extensions"])
    return (
        "export default [{\n"
        f'  files: ["**/*.{{{exts}}}"],\n'
        "  languageOptions: {\n"
        '    ecmaVersion: "latest", sourceType: "module",\n'
        "    parserOptions: { ecmaFeatures: { jsx: true } },\n"
        "  },\n"
        f"  rules: {json.dumps(rules)},\n"
        "}];\n"
    )


def _run_eslint(npx: str, cfg: dict, src: Path, result: CheckResult) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", dir=ROOT, delete=False) as fh:
        fh.write(_eslint_config(cfg))
    try:
        proc = subprocess.run(
            [npx, "--yes", cfg["eslint_package"], "--config", fh.name, "--format", "json",
             "--no-warn-ignored", str(src)],
            capture_output=True, text=True, check=False, cwd=ROOT,
        )  # fmt: skip
    finally:
        Path(fh.name).unlink(missing_ok=True)
    try:
        for file_result in json.loads(proc.stdout or "[]"):
            for msg in file_result["messages"]:
                path = _rel(Path(file_result["filePath"]))
                result.add(path, msg.get("line", 1), f"{msg.get('ruleId')}: {msg['message']}")
    except json.JSONDecodeError:
        result.status, result.detail = "error", (proc.stderr or proc.stdout)[-500:]


def _run_madge(npx: str, cfg: dict, src: Path, result: CheckResult) -> None:
    exts = ",".join(cfg["extensions"])
    proc = subprocess.run(
        [npx, "--yes", cfg["madge_package"], "--circular", "--json", "--extensions", exts,
         str(src)],
        capture_output=True, text=True, check=False, cwd=ROOT,
    )  # fmt: skip
    try:
        for cycle in json.loads(proc.stdout or "[]"):
            path = f"{cfg['path']}/{cfg['src']}/{cycle[0]}"
            result.add(path, 1, "circular import: " + " -> ".join(cycle + cycle[:1]))
    except json.JSONDecodeError:
        result.status, result.detail = "error", (proc.stderr or proc.stdout)[-500:]


def check_frontend(cfg: dict) -> list[CheckResult]:
    eslint, madge = CheckResult("frontend-eslint"), CheckResult("frontend-cycles")
    src = ROOT / cfg["path"] / cfg["src"]
    npx = shutil.which("npx")
    skip = None
    if npx is None:
        skip = "npx (Node.js) not installed"
    elif not src.is_dir():
        skip = f"{cfg['path']}/{cfg['src']} not found"
    if skip:
        for r in (eslint, madge):
            r.status, r.detail = "skipped", skip
        return [eslint, madge]

    _run_eslint(npx, cfg, src, eslint)
    if cfg.get("circular_imports"):
        _run_madge(npx, cfg, src, madge)
    else:
        madge.status, madge.detail = "skipped", "disabled in quality.yml"
    return [eslint, madge]


# --- Secrets ------------------------------------------------------------------


def _candidate_files(cfg: dict) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if proc.returncode == 0:
        paths = [ROOT / p for p in proc.stdout.splitlines()]
    else:
        paths = [p for p in ROOT.rglob("*") if p.is_file()]
    return [
        p
        for p in paths
        if p.is_file()
        and p.stat().st_size <= cfg["max_file_bytes"]
        and not _excluded(_rel(p), cfg["exclude"])
    ]


def check_secrets(cfg: dict) -> CheckResult:
    # Same patterns the agents use for redaction; imported here so this script also
    # runs from any working directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from agents.redact import find_secrets

    result = CheckResult("secrets")
    files = _candidate_files(cfg)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary
        for name, line, _ in find_secrets(text):
            # never echo the secret itself into CI logs
            result.add(_rel(path), line, f"possible secret ({name})", "critical")

    detect = shutil.which("detect-secrets")
    if cfg.get("detect_secrets") and detect and files:
        cmd = [detect, "scan"]
        for plugin in cfg.get("detect_secrets_disabled_plugins", []):
            cmd += ["--disable-plugin", plugin]
        proc = subprocess.run(
            cmd + [_rel(p) for p in files], cwd=ROOT, capture_output=True, text=True, check=False
        )
        try:
            found = json.loads(proc.stdout).get("results", {})
        except json.JSONDecodeError:
            found = {}
        seen = {(v.path, v.line) for v in result.violations}
        for path, items in found.items():
            for item in items:
                if (path, item["line_number"]) not in seen:
                    msg = f"possible secret (detect-secrets: {item['type']})"
                    result.add(path, item["line_number"], msg, "critical")
    result.detail = f"{len(files)} files scanned"
    return result


# --- Reporting ----------------------------------------------------------------


def write_reports(results: list[CheckResult], cfg: dict) -> None:
    violations = [v for r in results for v in r.violations]
    report = {
        "checks": [
            {
                "name": r.name,
                "status": r.status,
                "detail": r.detail,
                "violations": len(r.violations),
            }
            for r in results
        ],
        "violations": [asdict(v) for v in violations],
    }
    (ROOT / cfg["json"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
    codequality = [
        {
            "description": v.message.splitlines()[0],
            "check_name": v.check,
            "fingerprint": hashlib.sha1(f"{v.check}:{v.path}:{v.message}".encode()).hexdigest(),
            "severity": v.severity,
            "location": {"path": v.path, "lines": {"begin": v.line}},
        }
        for v in violations
    ]
    (ROOT / cfg["gitlab_codequality"]).write_text(json.dumps(codequality, indent=2))


def print_summary(results: list[CheckResult]) -> None:
    icons = {"ok": "✅", "failed": "❌", "skipped": "⏭️ ", "error": "⚠️ "}
    print("\nInfraForge code quality")
    print("=" * 60)
    for r in results:
        extra = f" — {r.detail}" if r.detail else ""
        print(f"{icons[r.status]} {r.name:<22} {len(r.violations):>4} violation(s){extra}")
        for v in r.violations[:50]:
            print(f"     {v.path}:{v.line}: {v.message}")
        if len(r.violations) > 50:
            print(f"     ... {len(r.violations) - 50} more (see JSON report)")


def run(config_path: Path, only: set[str] | None = None) -> list[CheckResult]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    results: list[CheckResult] = []

    def want(group: str) -> bool:
        return only is None or group in only

    if want("python"):
        files = python_files(cfg["python"])
        results.append(check_python_complexity(cfg["python"], files))
        results += check_python_sizes(cfg["python"], files)
        results.append(check_import_cycles(cfg["python"].get("import_cycles", [])))
    if want("frontend"):
        results += check_frontend(cfg["frontend"])
    if want("secrets"):
        results.append(check_secrets(cfg["secrets"]))
    write_reports(results, cfg["report"])
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=str(ROOT / "quality" / "quality.yml"))
    parser.add_argument("--only", nargs="*", choices=["python", "frontend", "secrets"])
    args = parser.parse_args(argv)
    results = run(Path(args.config), set(args.only) if args.only else None)
    print_summary(results)
    failed = any(r.status in ("failed", "error") for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
