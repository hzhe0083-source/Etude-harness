"""Pinned HF references and ordinary run folders. No local artifact store."""
import json
import re
from pathlib import Path, PurePosixPath

from . import store
from .errors import ResearchError

HF_RE = re.compile(r"^(model|dataset)=([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)@([0-9a-f]{40})(?::(.+))?$")
TARGET_RE = re.compile(r"^(model|dataset)=([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)$")


def safe_path(path):
    parts = PurePosixPath(path)
    if not path or parts.is_absolute() or ".." in parts.parts or "\\" in path:
        raise ResearchError("HF path must be a relative path without '..'")
    return path


def parse_hf(text):
    match = HF_RE.fullmatch(text)
    if not match:
        raise ResearchError("expected model|dataset=OWNER/NAME@<40-hex revision>[:path]")
    ref = dict(zip(("type", "repo", "revision"), match.groups()[:3]))
    if match[4]:
        ref["path"] = safe_path(match[4])
    return ref


def parse_target(text):
    match = TARGET_RE.fullmatch(text)
    if not match:
        raise ResearchError("archive target must be model|dataset=OWNER/NAME")
    return dict(zip(("type", "repo"), match.groups()))


def verify_ref(ref):
    from huggingface_hub import HfApi
    api = HfApi()
    if "path" in ref:
        if not api.file_exists(repo_id=ref["repo"], repo_type=ref["type"], revision=ref["revision"], filename=ref["path"]):
            raise ResearchError("HF evidence file does not exist at the pinned revision")
    else:
        api.repo_info(repo_id=ref["repo"], repo_type=ref["type"], revision=ref["revision"])
    return ref


def upload_folder(folder, target, path):
    """Upload closed files, then read back and hash every file before recording the revision."""
    from huggingface_hub import HfApi, hf_hub_download
    folder = Path(folder)
    safe_path(path)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if not files or any(p.is_symlink() for p in folder.rglob("*")):
        raise ResearchError("upload requires a nonempty folder without symlinks")
    if any(p.suffix in (".safetensors", ".pt", ".pth", ".parquet", ".h5") for p in files):
        raise ResearchError("put checkpoints/datasets outside RESEARCH_RUN_DIR; archive their pinned HF reference")
    manifest = {p.relative_to(folder).as_posix(): store.sha256_file(p) for p in files}
    api = HfApi()
    info = api.upload_folder(repo_id=target["repo"], repo_type=target["type"],
                             folder_path=folder, path_in_repo=path,
                             commit_message=f"Archive execution records: {path}")
    for name, digest in manifest.items():
        downloaded = hf_hub_download(repo_id=target["repo"], repo_type=target["type"],
                                     revision=info.oid, filename=path + "/" + name)
        if store.sha256_file(downloaded) != digest:
            raise ResearchError("HF readback checksum mismatch: " + name)
    return dict(target, revision=info.oid, path=path + "/execution.json")
