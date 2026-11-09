"""The agent token must only be usable to read and to create MR notes."""

import httpx
import pytest

from agents.gitlab_client import ForbiddenEndpoint, GitLabClient, GitLabError


def make_client(handler, **kwargs) -> tuple[GitLabClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = GitLabClient(
        "https://gitlab.example.com",
        "test-token",
        "group/infraforge",
        transport=httpx.MockTransport(wrapped),
        sleep=lambda s: None,
        **kwargs,
    )
    return client, seen


@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", "/projects/x/merge_requests/1/merge"),
        ("POST", "/projects/x/merge_requests/1/approve"),
        ("POST", "/projects/x/repository/commits"),
        ("POST", "/projects/x/pipeline"),
        ("POST", "/projects/x/jobs/5/retry"),
        ("PUT", "/projects/x/merge_requests/1/notes/9"),
        ("DELETE", "/projects/x/merge_requests/1/notes/9"),
        ("POST", "/projects/x/variables"),
        ("GET", "/projects/x/variables"),
    ],
)
def test_everything_outside_the_allow_list_is_refused(method, path):
    client, seen = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ForbiddenEndpoint):
        client._request(method, path)
    assert seen == []  # refused before any network call


def test_create_note_posts_body_with_token_header():
    client, seen = make_client(lambda r: httpx.Response(201, json={"id": 1}))
    client.create_note(7, "hello")
    request = seen[0]
    assert request.method == "POST"
    assert request.url.raw_path == b"/api/v4/projects/group%2Finfraforge/merge_requests/7/notes"
    assert request.headers["PRIVATE-TOKEN"] == "test-token"
    assert b"hello" in request.content


def test_long_notes_are_truncated():
    client, seen = make_client(lambda r: httpx.Response(201, json={"id": 1}))
    client.create_note(7, "x" * 100_000)
    assert len(seen[0].content) < 70_000


def test_get_is_retried_on_5xx_but_post_is_not():
    responses = iter([httpx.Response(502), httpx.Response(200, json=[])])
    client, seen = make_client(lambda r: next(responses))
    assert client.failed_jobs(42) == []
    assert len(seen) == 2

    client, seen = make_client(lambda r: httpx.Response(502))
    with pytest.raises(GitLabError):
        client.create_note(1, "body")
    assert len(seen) == 1  # no duplicate comments


def test_pagination_follows_next_page_header():
    def handler(request):
        page = int(request.url.params["page"])
        headers = {"x-next-page": "2"} if page == 1 else {}
        return httpx.Response(200, json=[{"id": page}], headers=headers)

    client, _ = make_client(handler)
    assert [n["id"] for n in client.notes(3)] == [1, 2]


def test_marker_detection():
    client, _ = make_client(lambda r: httpx.Response(200, json=[{"body": "a <!-- m -->"}]))
    assert client.has_note_with_marker(1, "<!-- m -->")
    assert not client.has_note_with_marker(1, "<!-- other -->")
