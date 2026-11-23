"""GitHub repo connector: downloads the repository as one zip (one GitHub API call, so the unauthenticated
limit of 60 calls an hour is plenty) and keeps the text files worth searching: docs and source code.
Set GITHUB_TOKEN in .env for private repos or a higher limit."""

import io
import re
import zipfile
from dataclasses import dataclass

from ..config import settings
from .fetch import FetchError, fetch

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 200 * 1024
MAX_FILES = 300
MAX_TOTAL_TEXT = 3 * 1024 * 1024

TEXT_EXTENSIONS = {".md", ".markdown", ".rst", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
                   ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".sql", ".sh",
                   ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".scss", ".vue", ".svelte"}
TEXT_NAMES = {"readme", "license", "contributing", "dockerfile", "makefile", "changelog"}
SKIP_DIRS = {".git", "node_modules", "dist", "build", "vendor", "__pycache__", ".venv", "venv", "target",
             ".next", "coverage", ".idea", ".vscode"}

GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/tree/([^/]+))?/?$")


class RepoError(FetchError):
    pass


@dataclass
class RepoFile:
    path: str
    text: str
    url: str  # link to the file on GitHub at the downloaded commit


def parse_url(url: str) -> tuple[str, str, str | None]:
    """https://github.com/<owner>/<repo>[/tree/<branch>] -> (owner, repo, branch or None)."""
    m = GITHUB_URL.match(url.strip())
    if not m:
        raise RepoError(f"'{url}' is not a GitHub repository link like https://github.com/owner/repo.")
    return m.group(1), m.group(2), m.group(3)


def _wanted(path: str, size: int) -> bool:
    parts = path.split("/")
    if any(p in SKIP_DIRS or (p.startswith(".") and p != ".github") for p in parts[:-1]):
        return False
    name = parts[-1].lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return size <= MAX_FILE_BYTES and (ext in TEXT_EXTENSIONS or name.split(".")[0] in TEXT_NAMES)


def download(url: str) -> tuple[str, list[RepoFile]]:
    """Return ("owner/repo@commit", files)."""
    owner, repo, branch = parse_url(url)
    api = f"https://api.github.com/repos/{owner}/{repo}/zipball" + (f"/{branch}" if branch else "")
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    try:
        data = fetch(api, MAX_ZIP_BYTES, headers).body
    except FetchError as exc:
        raise RepoError(f"Could not download {owner}/{repo} (is it public, and the name right?): {exc}") from exc

    files: list[RepoFile] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries = [e for e in zf.infolist() if not e.is_dir()]
        top = entries[0].filename.split("/")[0] if entries else ""
        commit = top.rsplit("-", 1)[-1] if "-" in top else (branch or "HEAD")
        for e in entries:
            path = e.filename.split("/", 1)[1] if "/" in e.filename else e.filename
            if not path or not _wanted(path, e.file_size):  # file_size is checked before reading: no zip bombs
                continue
            raw = zf.read(e)
            if b"\x00" in raw[:2048]:
                continue  # binary file with a text-like name
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            total += len(text)
            if len(files) >= MAX_FILES or total > MAX_TOTAL_TEXT:
                break
            files.append(RepoFile(path, text, f"https://github.com/{owner}/{repo}/blob/{commit}/{path}"))
    if not files:
        raise RepoError(f"{owner}/{repo} has no text or code files to search.")
    return f"{owner}/{repo}@{commit}", files
