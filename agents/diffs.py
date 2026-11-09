"""Unified-diff parsing and compact rendering for prompts.

The reviewer may only report findings on lines *added* by the MR, so we keep a map
of ``new line number -> text`` per file and validate every finding against it.
"""

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

# Files that add noise and no review value.
SKIP_PATTERNS = (
    re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock)$"),
    re.compile(r"(^|/)\.terraform\.lock\.hcl$"),
    re.compile(r"\.(png|jpg|jpeg|gif|svg|ico|pdf|zip|gz|qcow2|iso)$"),
    re.compile(r"(^|/)(dist|build|node_modules)/"),
)

REVIEWABLE = (
    ("terraform", re.compile(r"\.(tf|tfvars)$")),
    ("ansible", re.compile(r"(^|/)ansible/.*\.ya?ml$")),
    ("python", re.compile(r"\.py$")),
    ("react", re.compile(r"\.(js|jsx|ts|tsx)$")),
    ("policy", re.compile(r"\.rego$")),
    ("ci", re.compile(r"(^|/)\.gitlab-ci\.yml$|(^|/)ci/.*\.ya?ml$")),
)


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    new_file: bool = False
    deleted: bool = False
    # new-file line number -> added line text
    added: dict[int, str] = field(default_factory=dict)
    # every new-side line visible in the diff (added + context)
    new_lines: dict[int, str] = field(default_factory=dict)
    # rendered lines for the prompt
    rendered: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str | None:
        for kind, pattern in REVIEWABLE:
            if pattern.search(self.path):
                return kind
        return None

    @property
    def skipped(self) -> bool:
        return any(p.search(self.path) for p in SKIP_PATTERNS)


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Parse ``git diff`` / GitLab diff output into ``FileDiff`` objects."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split(" b/", 1)
            current = FileDiff(path=parts[1] if len(parts) == 2 else raw[11:])
            files.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.new_file = True
        elif raw.startswith("deleted file mode"):
            current.deleted = True
        elif raw.startswith("--- "):
            old = raw[4:].strip()
            current.old_path = None if old == "/dev/null" else old.removeprefix("a/")
        elif raw.startswith("+++ "):
            new = raw[4:].strip()
            if new != "/dev/null":
                current.path = new.removeprefix("b/")
        elif match := _HUNK.match(raw):
            new_line = int(match.group(1))
            current.rendered.append(raw)
        elif raw.startswith("+"):
            current.added[new_line] = raw[1:]
            current.new_lines[new_line] = raw[1:]
            current.rendered.append(f"+{new_line:>5} | {raw[1:]}")
            new_line += 1
        elif raw.startswith("-"):
            current.rendered.append(f"-{'':>5} | {raw[1:]}")
        elif raw.startswith(" ") or raw == "":
            current.new_lines[new_line] = raw[1:]
            current.rendered.append(f" {new_line:>5} | {raw[1:]}")
            new_line += 1
        # "\ No newline at end of file" and other markers are ignored
    return files


def gitlab_diffs_to_unified(diffs: list[dict]) -> str:
    """Convert GitLab ``/merge_requests/:iid/diffs`` entries into unified diff text."""
    chunks = []
    for d in diffs:
        old, new = d.get("old_path", ""), d.get("new_path", "")
        header = [f"diff --git a/{old} b/{new}"]
        if d.get("new_file"):
            header.append("new file mode 100644")
        if d.get("deleted_file"):
            header.append("deleted file mode 100644")
        header.append("--- /dev/null" if d.get("new_file") else f"--- a/{old}")
        header.append("+++ /dev/null" if d.get("deleted_file") else f"+++ b/{new}")
        chunks.append("\n".join(header) + "\n" + (d.get("diff") or ""))
    return "\n".join(chunks)


def render_for_prompt(
    files: list[FileDiff], max_chars: int, only_reviewable: bool = False
) -> tuple[str, list[str]]:
    """Render files with line numbers, staying under ``max_chars``.

    Returns ``(text, omitted_paths)``. Whole files are dropped rather than cut in the
    middle, so the model never sees half a hunk.
    """
    out: list[str] = []
    omitted: list[str] = []
    size = 0
    for f in files:
        if f.skipped or f.deleted or (only_reviewable and f.kind is None):
            omitted.append(f.path)
            continue
        block = "\n".join([f"=== file: {f.path} ({f.kind or 'other'})", *f.rendered]) + "\n"
        if size + len(block) > max_chars:
            omitted.append(f.path)
            continue
        out.append(block)
        size += len(block)
    return "".join(out), omitted
