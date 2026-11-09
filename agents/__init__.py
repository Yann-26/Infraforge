"""InfraForge AI agents.

Small, comment-only helpers that run in GitLab CI:

- ``investigator``: explains why a CI job failed and suggests a fix.
- ``reviewer``: reviews an MR diff and reports high-severity findings.

Agents never push, merge or apply anything. See docs/AI_AGENTS.md.
"""

__version__ = "0.1.0"
