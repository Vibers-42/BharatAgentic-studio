"""Project knowledge: list, add (file upload, docs link, GitHub repo), view, re-index, replace, download and
delete sources. Viewers and above can read; contributors and above can add; the person who added a source and
the workspace owners/admins can change or delete it. Files are only downloadable through this api, after the
same check (they are not in any public folder)."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import knowledge
from ..access import require_project, require_source
from ..auth import User, current_user
from ..config import settings
from ..rag import extract

router = APIRouter()


class LinkRequest(BaseModel):
    url: str


def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    name = knowledge.safe_file_name(file.filename)
    data = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {settings.max_upload_mb} MB.")
    try:
        extract.kind_for(name, data)
    except extract.ExtractError as exc:
        raise HTTPException(400, str(exc))
    return name, data


@router.get("/projects/{project_id}/sources")
def list_sources(project_id: int, user: User = Depends(current_user)):
    require_project(user, project_id)
    return knowledge.list_sources(project_id)


@router.post("/projects/{project_id}/sources/file", status_code=201)
def add_file(project_id: int, file: UploadFile = File(...), user: User = Depends(current_user)):
    require_project(user, project_id, "contribute")
    name, data = _read_upload(file)
    sid = knowledge.create_source(project_id, user.id, extract.kind_for(name, data), name, original_name=name)
    return knowledge.ingest_file(sid, project_id, name, data)


@router.post("/projects/{project_id}/sources/url", status_code=201)
def add_url(project_id: int, req: LinkRequest, user: User = Depends(current_user)):
    require_project(user, project_id, "contribute")
    url = req.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "Enter a web address starting with http:// or https://.")
    sid = knowledge.create_source(project_id, user.id, "url", url, url=url)
    return knowledge.ingest_link(sid, project_id, "url", url)


@router.post("/projects/{project_id}/sources/repo", status_code=201)
def add_repo(project_id: int, req: LinkRequest, user: User = Depends(current_user)):
    require_project(user, project_id, "contribute")
    url = req.url.strip()
    if not url.lower().startswith("https://github.com/"):
        raise HTTPException(400, "Enter a public GitHub repository, like https://github.com/owner/repo.")
    sid = knowledge.create_source(project_id, user.id, "repo", url, url=url)
    return knowledge.ingest_link(sid, project_id, "repo", url)


@router.get("/sources/{source_id}")
def get_source(source_id: int, user: User = Depends(current_user)):
    require_source(user, source_id)
    return knowledge.get_source(source_id)


@router.post("/sources/{source_id}/reindex")
def reindex(source_id: int, user: User = Depends(current_user)):
    require_source(user, source_id, "manage")
    return knowledge.reindex(knowledge.get_source(source_id))


@router.post("/sources/{source_id}/file")
def replace_file(source_id: int, file: UploadFile = File(...), user: User = Depends(current_user)):
    src, _ = require_source(user, source_id, "manage")
    current = knowledge.get_source(source_id)
    if current["kind"] in ("url", "repo"):
        raise HTTPException(400, "This source is a link; use re-index instead.")
    name, data = _read_upload(file)
    return knowledge.ingest_file(source_id, src["project_id"], name, data)


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, user: User = Depends(current_user)):
    require_source(user, source_id, "manage")
    used = knowledge.get_source(source_id)["agents"]
    knowledge.delete_source(source_id)
    return {"deleted": source_id, "unlinked_agents": [a["name"] for a in used]}


@router.get("/sources/{source_id}/file")
def download(source_id: int, user: User = Depends(current_user)):
    require_source(user, source_id)
    src = knowledge.get_source(source_id)
    path = knowledge.file_path(src)
    return FileResponse(path, filename=src.get("original_name") or path.name,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
