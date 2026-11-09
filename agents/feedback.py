"""Count how engineers reacted to agent comments.

Run on a schedule (see ci/agents.gitlab-ci.yml, job ``ai_agents_feedback``). For each
MR updated in the last N days, find comments carrying an agent marker and classify:

- accepted: more 👍 than 👎
- rejected: more 👎 than 👍 (wrong or noise)
- ignored:  no reactions (or a tie)

Reactions are a proxy: an engineer may apply a fix without reacting. The docs list
this under known limitations.
"""

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from loguru import logger

from agents.comment import MARKER_PREFIX
from agents.gitlab_client import GitLabClient

_AGENT = re.compile(re.escape(MARKER_PREFIX) + r"(\w+) ")
UP, DOWN = {"thumbsup", "+1"}, {"thumbsdown", "-1"}


def verdict(emoji: list[dict]) -> str:
    names = [e.get("name") for e in emoji]
    up = sum(n in UP for n in names)
    down = sum(n in DOWN for n in names)
    if up > down:
        return "accepted"
    if down > up:
        return "rejected"
    return "ignored"


def collect(gitlab: GitLabClient, days: int) -> Counter:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    counts: Counter = Counter()
    for mr in gitlab.merge_requests(updated_after=since):
        for note in gitlab.notes(mr["iid"]):
            match = _AGENT.search(note.get("body") or "")
            if not match:
                continue
            emoji = gitlab.note_award_emoji(mr["iid"], note["id"])
            counts[(match.group(1), verdict(emoji))] += 1
    logger.info("feedback over {} days: {}", days, dict(counts))
    return counts
