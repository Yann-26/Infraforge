"""Versioned prompt files.

Each prompt lives in ``agents/prompts/<name>.<version>.md`` with two sections::

    <<<SYSTEM>>>
    ...system prompt...
    <<<USER>>>
    ...user template with $placeholders...

Templates use ``string.Template`` (``$name``) so JSON braces need no escaping.
Changing a prompt means adding a new version file and bumping the version in the
agent, so eval results can always be tied to an exact prompt.
"""

from dataclasses import dataclass
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    id: str  # e.g. "investigator.v1"
    system: str
    user_template: Template

    def render(self, **values: str) -> str:
        return self.user_template.substitute(**values)


def load_prompt(name: str, version: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.{version}.md"
    text = path.read_text(encoding="utf-8")
    if "<<<SYSTEM>>>" not in text or "<<<USER>>>" not in text:
        raise ValueError(f"{path} must contain <<<SYSTEM>>> and <<<USER>>> sections")
    _, rest = text.split("<<<SYSTEM>>>", 1)
    system, user = rest.split("<<<USER>>>", 1)
    return Prompt(
        id=f"{name}.{version}", system=system.strip(), user_template=Template(user.strip())
    )
