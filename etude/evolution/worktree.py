"""Git worktrees for candidates (design §3.1 回滚 row, §3.3 ``git.worktree(incumbent, patch)``).

Each candidate lives on branch ``cand/<id>`` in ``evolution/worktrees/<id>``
(git-ignored). Not merging is the rollback before acceptance; after
acceptance the receipt's ``rollback`` is ``git revert -m 1 <merge-sha>``.

Borrowed from Research OS ``capture.git_capture``: merges refuse a dirty
(tracked) main checkout, and every operation returns the exact commit SHA so
receipts never hold floating refs. Research OS Runs execute only commits
pushed to origin; :meth:`WorktreeManager.push` exists for that but is never
called automatically (pushing is the user's call).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from etude import REPO_ROOT


class GitError(RuntimeError):
    """A git command failed (stderr in the message)."""


@dataclass
class Candidate:
    """A candidate checkout: id, branch, worktree path, base and head commits."""

    cand_id: str
    branch: str
    path: Path
    base: str
    commit: str | None = None


class WorktreeManager:
    """git worktree add/remove, apply patch, commit to ``cand/<id>``, merge, revert (design §3.1, §3.3)."""

    def __init__(self, repo: str | Path = REPO_ROOT, wt_dir: str | Path | None = None, branch_prefix: str = "cand/"):
        self.repo = Path(repo).resolve()
        self.wt_dir = Path(wt_dir) if wt_dir is not None else self.repo / "evolution" / "worktrees"
        self.prefix = branch_prefix

    # -- plumbing -------------------------------------------------------------
    def git(self, *args: str, cwd: str | Path | None = None, input: str | None = None) -> str:
        p = subprocess.run(["git", *args], cwd=cwd or self.repo, capture_output=True, text=True, input=input)
        if p.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
        return p.stdout.strip()

    def rev(self, ref: str = "HEAD", cwd: str | Path | None = None) -> str:
        """Full SHA of ``ref``."""
        return self.git("rev-parse", ref, cwd=cwd)

    def current_branch(self, cwd: str | Path | None = None) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)

    #: Research OS bookkeeping that may be dirty in the main checkout (same pathspec
    #: excludes as research_os.capture.git_capture).
    LEDGER_EXCLUDES: tuple[str, ...] = (":(exclude)research/ledger.json", ":(exclude)research/WORK",
                                        ":(exclude)research/WORKSTATE")

    def is_clean(self, cwd: str | Path | None = None, allow_ledger: bool = True) -> bool:
        """No tracked modifications (untracked files are ignored). With ``allow_ledger``
        the Research OS ledger files are ignored as well (a bridge Run started with
        ``--root <main>/research`` rewrites the tracked ``research/ledger.json``)."""
        spec = ["--", ".", *self.LEDGER_EXCLUDES] if allow_ledger else []
        return self.git("status", "--porcelain", "--untracked-files=no", *spec, cwd=cwd) == ""

    def preflight(self, base_branch: str) -> list[str]:
        """Reasons the main checkout cannot take a merge later (design §3.3 merge(wt)):
        not on ``base_branch``, tracked changes outside the ledger, or a merge in progress."""
        out: list[str] = []
        try:
            br = self.current_branch()
            if br != base_branch:
                out.append(f"main checkout is on {br!r}, not {base_branch!r}")
            if not self.is_clean():
                out.append("main checkout has tracked changes outside research/ledger.json, WORK, WORKSTATE")
            if self.merge_in_progress():
                out.append("a merge is in progress in the main checkout")
        except GitError as e:
            out.append(str(e))
        return out

    def merge_in_progress(self) -> bool:
        """``MERGE_HEAD`` exists in the main checkout."""
        p = Path(self.git("rev-parse", "--git-path", "MERGE_HEAD"))
        return (p if p.is_absolute() else self.repo / p).exists()

    def abort_merge(self) -> bool:
        """``git merge --abort`` if a merge is in progress; returns whether it aborted."""
        if self.merge_in_progress():
            self.git("merge", "--abort")
            return True
        return False

    def branch(self, cand_id: str) -> str:
        return f"{self.prefix}{cand_id}"

    # -- candidate lifecycle ----------------------------------------------------
    def add(self, cand_id: str, base: str = "main") -> Candidate:
        """``git worktree add -b cand/<id> evolution/worktrees/<id> <base>``."""
        path = self.wt_dir / cand_id
        if path.exists():
            raise GitError(f"worktree path exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        base_sha = self.rev(base)
        self.git("worktree", "add", "-q", "-b", self.branch(cand_id), str(path), base_sha)
        return Candidate(cand_id, self.branch(cand_id), path, base_sha)

    def apply_patch(self, cand: Candidate, diff: str, check_only: bool = False) -> None:
        """``git apply --unidiff-zero`` the unified diff inside the candidate worktree."""
        args = ["apply", "--unidiff-zero", "--whitespace=nowarn", "--recount"] + (["--check"] if check_only else []) + ["-"]
        self.git(*args, cwd=cand.path, input=diff if diff.endswith("\n") else diff + "\n")

    def commit(self, cand: Candidate, message: str, paths: list[str] | None = None) -> str:
        """Stage (``paths`` or everything) and commit on ``cand/<id>``; returns the SHA."""
        self.git("add", *(["--", *paths] if paths else ["-A"]), cwd=cand.path)
        self.git("commit", "-q", "-m", message, cwd=cand.path)
        cand.commit = self.rev("HEAD", cwd=cand.path)
        return cand.commit

    def merge(self, cand: Candidate, into: str = "main", message: str | None = None) -> str:
        """``git merge --no-ff cand/<id>`` into ``into`` in the main checkout; returns the merge SHA.

        Refuses when the main checkout is not on ``into`` or has tracked changes.
        """
        if self.current_branch() != into:
            raise GitError(f"main checkout is on {self.current_branch()!r}, not {into!r}")
        if not self.is_clean():
            raise GitError("main checkout has uncommitted tracked changes outside the research ledger; "
                           "commit or stash before merging")
        try:
            self.git("merge", "--no-ff", "-q", "-m", message or f"evolution: accept {cand.cand_id}", cand.branch)
        except GitError:
            self.abort_merge()  # never leave main half-merged
            raise
        return self.rev("HEAD")

    def revert(self, merge_sha: str, mainline: int = 1) -> str:
        """``git revert -m 1 <merge-sha>`` (receipt ``rollback``); returns the revert commit SHA."""
        if not self.is_clean():
            raise GitError("main checkout has uncommitted tracked changes")
        self.git("revert", "--no-edit", "-m", str(mainline), merge_sha)
        return self.rev("HEAD")

    def remove(self, cand: Candidate, delete_branch: bool = False) -> None:
        """``git worktree remove --force``; the branch is kept unless ``delete_branch`` (audit trail)."""
        if cand.path.exists():
            self.git("worktree", "remove", "--force", str(cand.path))
        self.git("worktree", "prune")
        if delete_branch:
            self.git("branch", "-D", cand.branch)

    def push(self, cand: Candidate, remote: str = "origin") -> str:
        """Push ``cand/<id>`` so a Research OS Run may execute it (user-initiated only)."""
        return self.git("push", remote, f"{cand.branch}:{cand.branch}")

    def rollback_cmd(self, merge_sha: str | None, cand: Candidate | None = None) -> str:
        """Receipt ``rollback`` text."""
        if merge_sha:
            return f"git revert -m 1 {merge_sha}"
        if cand is not None:
            return f"not merged (branch {cand.branch} kept at {cand.commit or cand.base}); nothing to revert"
        return "no worktree created; nothing to revert"


__all__ = ["Candidate", "GitError", "WorktreeManager"]
