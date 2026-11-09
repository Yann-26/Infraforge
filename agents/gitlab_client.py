"""Least-privilege GitLab REST client.

The token (``AGENT_GITLAB_TOKEN``) should be a *project access token* with the
Reporter role and the ``api`` scope — GitLab has no narrower scope that can post MR
notes. To compensate, this client refuses every request that is not on the
allow-list below: it can read pipelines, job logs, MR diffs and notes, and create a
note. It cannot push, merge, approve, edit, delete, or trigger anything.
"""

import re
import time
from urllib.parse import quote

import httpx
from loguru import logger

_P = r"/projects/[^/]+"
ALLOWED_ENDPOINTS: list[tuple[str, re.Pattern[str]]] = [
    ("GET", re.compile(rf"^{_P}/pipelines/\d+/jobs$")),
    ("GET", re.compile(rf"^{_P}/jobs/\d+/trace$")),
    ("GET", re.compile(rf"^{_P}/merge_requests$")),
    ("GET", re.compile(rf"^{_P}/merge_requests/\d+$")),
    ("GET", re.compile(rf"^{_P}/merge_requests/\d+/diffs$")),
    ("GET", re.compile(rf"^{_P}/merge_requests/\d+/notes$")),
    ("GET", re.compile(rf"^{_P}/merge_requests/\d+/notes/\d+/award_emoji$")),
    ("POST", re.compile(rf"^{_P}/merge_requests/\d+/notes$")),
]

MAX_NOTE_CHARS = 60_000  # GitLab limit is ~1M; keep comments readable


class GitLabError(Exception):
    pass


class ForbiddenEndpoint(GitLabError):
    """The agent tried to call an endpoint outside its allow-list."""


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        project_id: str,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        retries: int = 2,
        sleep=None,
    ):
        self.project = quote(str(project_id), safe="")
        self.retries = retries
        self._sleep = sleep or time.sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v4",
            headers={"PRIVATE-TOKEN": token, "User-Agent": "infraforge-ai-agent"},
            timeout=timeout,
            transport=transport,
        )

    # -- core --------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not any(m == method and p.match(path) for m, p in ALLOWED_ENDPOINTS):
            raise ForbiddenEndpoint(f"{method} {path} is not allowed for the agent token")
        # Only GETs are retried: retrying a POST could create duplicate comments.
        attempts = self.retries + 1 if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise GitLabError(f"{method} {path}: {exc}") from exc
            else:
                if response.status_code < 500 or attempt == attempts:
                    if response.status_code >= 400:
                        raise GitLabError(f"{method} {path}: HTTP {response.status_code}")
                    return response
            self._sleep(2 ** (attempt - 1))
            logger.warning("retrying {} {} (attempt {})", method, path, attempt + 1)
        raise GitLabError(f"{method} {path}: exhausted retries")  # pragma: no cover

    def _get_all(self, path: str, params: dict | None = None, max_pages: int = 10) -> list[dict]:
        items: list[dict] = []
        params = dict(params or {}, per_page=100)
        for page in range(1, max_pages + 1):
            response = self._request("GET", path, params={**params, "page": page})
            batch = response.json()
            items.extend(batch)
            if not response.headers.get("x-next-page"):
                break
        return items

    # -- reads -------------------------------------------------------------
    def failed_jobs(self, pipeline_id: int) -> list[dict]:
        path = f"/projects/{self.project}/pipelines/{pipeline_id}/jobs"
        return self._get_all(path, {"scope[]": "failed"})

    def job_trace(self, job_id: int) -> str:
        return self._request("GET", f"/projects/{self.project}/jobs/{job_id}/trace").text

    def merge_request(self, mr_iid: int) -> dict:
        return self._request("GET", f"/projects/{self.project}/merge_requests/{mr_iid}").json()

    def merge_request_diffs(self, mr_iid: int) -> list[dict]:
        return self._get_all(f"/projects/{self.project}/merge_requests/{mr_iid}/diffs")

    def merge_requests(self, updated_after: str) -> list[dict]:
        params = {"state": "all", "updated_after": updated_after}
        return self._get_all(f"/projects/{self.project}/merge_requests", params)

    def notes(self, mr_iid: int) -> list[dict]:
        return self._get_all(f"/projects/{self.project}/merge_requests/{mr_iid}/notes")

    def note_award_emoji(self, mr_iid: int, note_id: int) -> list[dict]:
        path = f"/projects/{self.project}/merge_requests/{mr_iid}/notes/{note_id}/award_emoji"
        return self._request("GET", path).json()

    # -- the only write ----------------------------------------------------
    def create_note(self, mr_iid: int, body: str) -> dict:
        if len(body) > MAX_NOTE_CHARS:
            body = body[:MAX_NOTE_CHARS] + "\n\n_(comment truncated)_"
        path = f"/projects/{self.project}/merge_requests/{mr_iid}/notes"
        return self._request("POST", path, json={"body": body}).json()

    def has_note_with_marker(self, mr_iid: int, marker: str) -> bool:
        return any(marker in (n.get("body") or "") for n in self.notes(mr_iid))
