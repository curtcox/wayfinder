"""GitHub REST API client for the Issues bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from wayfinder.core.errors import InvalidInputError


@dataclass(frozen=True)
class GitHubComment:
    id: int
    user_login: str
    body: str


@dataclass(frozen=True)
class GitHubIssue:
    number: int
    title: str
    state: str


class GitHubClient:
    """Minimal GitHub Issues API wrapper."""

    def __init__(
        self,
        *,
        repo: str,
        token: str | None = None,
        api_base: str | None = None,
        bot_login: str = "wayfinder-bridge[bot]",
    ) -> None:
        parts = repo.split("/", maxsplit=1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            msg = "repo must be owner/name"
            raise InvalidInputError(msg)
        self._owner, self._name = parts
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        self._api_base = (api_base or os.environ.get("GITHUB_API_BASE", "https://api.github.com")).rstrip(
            "/",
        )
        self._bot_login = bot_login

    @property
    def bot_login(self) -> str:
        return self._bot_login

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self._api_base}{path}"

    def create_issue(self, *, title: str, body: str) -> GitHubIssue:
        payload = {"title": title, "body": body}
        response = httpx.post(
            self._url(f"/repos/{self._owner}/{self._name}/issues"),
            headers=self._headers(),
            json=payload,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return GitHubIssue(number=int(data["number"]), title=str(data["title"]), state=str(data["state"]))

    def close_issue(self, issue_number: int) -> None:
        response = httpx.patch(
            self._url(f"/repos/{self._owner}/{self._name}/issues/{issue_number}"),
            headers=self._headers(),
            json={"state": "closed"},
            timeout=30.0,
        )
        response.raise_for_status()

    def add_comment(self, issue_number: int, body: str) -> GitHubComment:
        response = httpx.post(
            self._url(f"/repos/{self._owner}/{self._name}/issues/{issue_number}/comments"),
            headers=self._headers(),
            json={"body": body},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        user = data.get("user", {})
        login = str(user.get("login", self._bot_login)) if isinstance(user, dict) else self._bot_login
        return GitHubComment(id=int(data["id"]), user_login=login, body=str(data.get("body", "")))

    def list_comments(self, issue_number: int) -> list[GitHubComment]:
        response = httpx.get(
            self._url(f"/repos/{self._owner}/{self._name}/issues/{issue_number}/comments"),
            headers=self._headers(),
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        comments: list[GitHubComment] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            user = item.get("user", {})
            login = str(user.get("login", "")) if isinstance(user, dict) else ""
            comments.append(
                GitHubComment(
                    id=int(item["id"]),
                    user_login=login,
                    body=str(item.get("body", "")),
                ),
            )
        return comments

    def add_label(self, issue_number: int, label: str) -> None:
        response = httpx.post(
            self._url(f"/repos/{self._owner}/{self._name}/issues/{issue_number}/labels"),
            headers=self._headers(),
            json=[label],
            timeout=30.0,
        )
        response.raise_for_status()

    def remove_label(self, issue_number: int, label: str) -> None:
        encoded = quote(label, safe="")
        response = httpx.delete(
            self._url(f"/repos/{self._owner}/{self._name}/issues/{issue_number}/labels/{encoded}"),
            headers=self._headers(),
            timeout=30.0,
        )
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()
