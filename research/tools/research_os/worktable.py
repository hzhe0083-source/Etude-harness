"""Per-experiment claim table (AGENTS.md R45): WORK/<experiment id>.tsv, never part of the event log.

Every change happens in a throwaway worktree on top of the freshly fetched upstream branch, so the
caller's checkout, index and unpushed commits are never touched or published. A take holds only when
the single commit that adds it is pushed; a rejected push means someone moved first, so the table is
fetched again and re-checked.
"""

import contextlib
import subprocess
import tempfile
from pathlib import Path

from . import store
from .errors import ResearchError

WORK_DIR = "WORK"
ATTEMPTS = 5
HEADER = "run_id\trun\tagent\ttaken_at\n"


def table_path(root, experiment_id):
    return Path(root) / WORK_DIR / f"{experiment_id}.tsv"


def parse_table(text):
    """run id -> {run, agent, taken_at}."""
    rows = [line.split("\t") for line in text.splitlines()[1:] if line.strip()]
    return {run_id: {"run": alias, "agent": agent, "taken_at": at} for run_id, alias, agent, at in rows}


def _render_table(table):
    lines = [f"{run_id}\t{row['run']}\t{row['agent']}\t{row['taken_at']}\n" for run_id, row in sorted(table.items())]
    return HEADER + "".join(lines)


def _git(top, *args):
    return subprocess.run(["git", *args], cwd=top, capture_output=True, text=True)


def _must(result, what):
    if result.returncode != 0:
        raise ResearchError(f"{what} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _upstream(root):
    """(toplevel, remote, branch) of the checkout holding `root`."""
    top = Path(_must(_git(root, "rev-parse", "--show-toplevel"), "git rev-parse").strip())
    upstream = _git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        raise ResearchError("the claim table needs a branch with an upstream (git push -u origin <branch>)")
    remote, branch = upstream.stdout.strip().split("/", 1)
    return top, remote, branch


def _fetch(top, remote, branch):
    _must(_git(top, "fetch", "-q", remote, branch), "git fetch")
    return _must(_git(top, "rev-parse", "FETCH_HEAD"), "git rev-parse").strip()


def remote_table(root, experiment_id):
    """The claim table as it is on the upstream branch right now."""
    top, remote, branch = _upstream(root)
    commit = _fetch(top, remote, branch)
    rel = table_path(root, experiment_id).resolve().relative_to(top).as_posix()
    shown = _git(top, "show", f"{commit}:{rel}")
    return parse_table(shown.stdout) if shown.returncode == 0 else {}


@contextlib.contextmanager
def _scratch_worktree(top, commit):
    """A detached worktree at `commit`, removed afterwards whatever happens."""
    path = Path(tempfile.mkdtemp(prefix="research-claims-"))
    _must(_git(top, "worktree", "add", "-q", "--detach", str(path), commit), "git worktree add")
    try:
        yield path
    finally:
        _git(top, "worktree", "remove", "--force", str(path))


def _attempt(top, remote, branch, rel, message, change):
    """One fetch-edit-commit-push round: (pushed, table)."""
    commit = _fetch(top, remote, branch)
    with _scratch_worktree(top, commit) as tree:
        path = tree / rel
        table = change(parse_table(path.read_text(encoding="utf-8")) if path.is_file() else {})
        if table is None:
            return True, parse_table(path.read_text(encoding="utf-8")) if path.is_file() else {}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_table(table), encoding="utf-8")
        _must(_git(tree, "add", "--", rel), "git add")
        _must(_git(tree, "commit", "-q", "-m", message), "git commit")
        return _git(tree, "push", "-q", remote, f"HEAD:refs/heads/{branch}").returncode == 0, table


def _publish(root, experiment_id, message, change):
    """change(table) -> new table, or None for no change; raises to refuse."""
    top, remote, branch = _upstream(root)
    rel = table_path(root, experiment_id).resolve().relative_to(top).as_posix()
    for _ in range(ATTEMPTS):
        pushed, table = _attempt(top, remote, branch, rel, message, change)
        if pushed:
            return table
    raise ResearchError(f"could not publish the claim table after {ATTEMPTS} attempts; try again")


def take(root, experiment_id, run_id, alias, agent_id):
    def change(table):
        owner = table.get(run_id, {}).get("agent")
        if owner == agent_id:
            return None
        if owner:
            raise ResearchError(f"{alias} is already taken by {owner}")
        return dict(table, **{run_id: {"run": alias, "agent": agent_id, "taken_at": store.now_ts()}})

    return _publish(root, experiment_id, f"Take {alias} for {agent_id}", change)


def release(root, experiment_id, run_id, alias, agent_id):
    def change(table):
        owner = table.get(run_id, {}).get("agent")
        if owner is None:
            return None
        if owner != agent_id:
            raise ResearchError(f"{alias} is taken by {owner}, not {agent_id}")
        return {key: row for key, row in table.items() if key != run_id}

    return _publish(root, experiment_id, f"Release {alias} from {agent_id}", change)


def shared(root):
    """A claim table exists only for a checkout with an upstream; without one no other agent can take Runs."""
    return _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode == 0


def taker(root, experiment_id, run_id):
    return remote_table(root, experiment_id).get(run_id, {}).get("agent")
