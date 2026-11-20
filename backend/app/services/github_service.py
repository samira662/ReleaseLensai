"""GitHub ingestion: refs, commits, PRs, linked issues, file diffs and file contents."""
import re
from dataclasses import dataclass, field
from itertools import islice

from github import Auth, Github, GithubException

from ..config import settings

FIX_RE = re.compile(r"\b(fix(es|ed)?|bug|hotfix|regression|patch)\b", re.I)
CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
PR_REF_RE = re.compile(r"\(#(\d+)\)|#(\d+)")


class GitHubError(Exception):
    """Raised with a message that is safe to show to the user."""


@dataclass
class FileChange:
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_filename: str | None = None


@dataclass
class ChangeData:
    sha: str
    title: str
    message: str
    author: str
    url: str
    pr_number: int | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    issues: list[dict] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)


def parse_repo_url(url: str) -> str:
    cleaned = re.sub(r"^https?://(www\.)?", "", url.strip())
    m = re.match(r"^(?:github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$", cleaned)
    if not m:
        raise GitHubError("Enter a GitHub repository URL like https://github.com/owner/repo.")
    return f"{m.group(1)}/{m.group(2)}"


def _friendly(exc: GithubException) -> GitHubError:
    if exc.status == 404:
        return GitHubError("Repository, tag or commit not found. If the repo is private, add an access token.")
    if exc.status in (401, 403):
        msg = (exc.data or {}).get("message", "") if isinstance(exc.data, dict) else ""
        if "rate limit" in msg.lower():
            return GitHubError("GitHub API rate limit reached. Add an access token or try again later.")
        return GitHubError("GitHub rejected the request. Check that your access token is valid.")
    return GitHubError(f"GitHub API error ({exc.status}).")


class GitHubService:
    def __init__(self, token: str | None = None):
        token = token or settings.github_token or None
        self.gh = Github(auth=Auth.Token(token)) if token else Github()
        self._repos: dict = {}

    def _repo(self, full_name: str):
        if full_name not in self._repos:
            try:
                self._repos[full_name] = self.gh.get_repo(full_name)
            except GithubException as exc:
                raise _friendly(exc) from exc
        return self._repos[full_name]

    # ---- refs ----
    def list_refs(self, full_name: str) -> dict:
        try:
            repo = self._repo(full_name)
            tags = [t.name for t in islice(repo.get_tags(), 40)]
            branches = [b.name for b in islice(repo.get_branches(), 40)]
            return {"repo": full_name, "default_branch": repo.default_branch, "tags": tags, "branches": branches}
        except GithubException as exc:
            raise _friendly(exc) from exc

    # ---- release data ----
    def fetch_changes(self, full_name: str, base: str, head: str, max_commits: int | None = None):
        """Return (changes, release_files) for the comparison base...head."""
        max_commits = max_commits or settings.max_commits
        repo = self._repo(full_name)
        try:
            cmp = repo.compare(base, head)
            commits = list(cmp.commits)[-max_commits:]
            release_files = [
                FileChange(
                    f.filename, f.status, f.additions, f.deletions, None, getattr(f, "previous_filename", None)
                )
                for f in islice(cmp.files, 300)
            ]
            pr_cache: dict[int, tuple] = {}
            issue_cache: dict[int, str | None] = {}
            changes: list[ChangeData] = []
            for c in commits:
                if len(c.parents) > 1:  # skip merge commits; the merged PR commits are listed separately
                    continue
                message = c.commit.message or ""
                change = ChangeData(
                    sha=c.sha,
                    title=message.splitlines()[0][:300] if message else c.sha[:7],
                    message=message,
                    author=(c.commit.author.name if c.commit.author else "") or "",
                    url=c.html_url,
                )
                change.files = [
                    FileChange(f.filename, f.status, f.additions, f.deletions, f.patch, getattr(f, "previous_filename", None))
                    for f in c.files
                ]
                self._attach_pr_and_issues(repo, change, pr_cache, issue_cache)
                changes.append(change)
            return changes, release_files
        except GithubException as exc:
            raise _friendly(exc) from exc

    def _attach_pr_and_issues(self, repo, change: ChangeData, pr_cache, issue_cache) -> None:
        m = PR_REF_RE.search(change.title)
        if not m:
            return
        number = int(m.group(1) or m.group(2))
        if number not in pr_cache:
            try:
                pr = repo.get_pull(number)
                pr_cache[number] = (pr.title, pr.body or "")
            except GithubException:
                pr_cache[number] = (None, "")
        title, body = pr_cache[number]
        if title is None:
            return
        change.pr_number, change.pr_title, change.pr_body = number, title, body
        for issue_no in list(dict.fromkeys(int(x) for x in CLOSES_RE.findall(body)))[:3]:
            if issue_no not in issue_cache:
                try:
                    issue_cache[issue_no] = repo.get_issue(issue_no).title
                except GithubException:
                    issue_cache[issue_no] = None
            if issue_cache[issue_no]:
                change.issues.append({"number": issue_no, "title": issue_cache[issue_no]})

    def get_file_content(self, full_name: str, path: str, ref: str, max_bytes: int = 300_000) -> str | None:
        try:
            item = self._repo(full_name).get_contents(path, ref=ref)
            if isinstance(item, list) or item.size > max_bytes:
                return None
            return item.decoded_content.decode("utf-8", errors="replace")
        except (GithubException, GitHubError):
            return None

    def file_fix_history(self, full_name: str, path: str, until_ref: str, limit: int = 30) -> tuple[int, int]:
        """(bug-fix commits, total commits) among the last `limit` commits touching `path` before `until_ref`."""
        try:
            commits = list(islice(self._repo(full_name).get_commits(sha=until_ref, path=path), limit))
        except (GithubException, GitHubError):
            return 0, 0
        fixes = sum(1 for c in commits if FIX_RE.search(c.commit.message or ""))
        return fixes, len(commits)
