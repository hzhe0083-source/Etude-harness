"""Mechanical capture for `research run` (AGENTS.md R20-R22). Never guesses: gaps go to capture_gaps."""

import codecs
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import signal
import contextlib
import tempfile
from pathlib import Path

from . import store
from .errors import ResearchError

LOCKFILES = ("uv.lock", "poetry.lock", "requirements.txt")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout


def _toplevel(cwd):
    probe = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
    if probe.returncode != 0:
        raise ResearchError(f"R20: {cwd} is not inside a git repository; such a run cannot be evidence")
    return Path(probe.stdout.strip()).resolve()


def git_capture(root, cwd):
    top = _toplevel(cwd)
    # Ledger/coordination edits do not change executed code. research/tools stays checked.
    spec = [".", ":(exclude)research/ledger.json", ":(exclude)research/WORK",
            ":(exclude)research/WORKSTATE"]
    status = _git(top, "status", "--porcelain", "--untracked-files=all", "--", *spec).decode()
    if status.strip():
        raise ResearchError("commit code/config changes before running:\n" + status)
    commit = _git(top, "rev-parse", "HEAD").decode().strip()
    repo = _git(top, "remote", "get-url", "origin").decode().strip()
    subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=top, check=True)
    branches = _git(top, "branch", "-r", "--contains", commit).decode().splitlines()
    if not any(line.strip().startswith("origin/") for line in branches):
        raise ResearchError("push the code commit to origin before running")
    return {"repo": repo, "commit": commit}, [], top


@contextlib.contextmanager
def frozen_checkout(top, commit, command, cwd, inputs):
    """Execute the committed tree in an isolated checkout, with no inherited source path."""
    tracked = set(_git(top, "ls-files", "-z").decode().split("\0"))
    rewritten = []
    data_inputs = {Path(p).resolve() for p in inputs}
    directory = Path(tempfile.mkdtemp(prefix="research-source-"))
    subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(directory), commit], cwd=top, check=True)
    try:
        for token in command:
            prefix, separator, value = token.partition("=") if token.startswith("--") else ("", "", token)
            if token in ("-c", "-lc", "-e"):
                raise ResearchError("inline scripts are not versioned; commit an entrypoint instead")
            candidate = Path(value)
            absolute = (Path(cwd) / candidate).resolve()
            if absolute.is_file() and candidate.suffix in (".py", ".sh", ".json", ".yaml", ".yml", ".toml"):
                if absolute.is_relative_to(top) and absolute.relative_to(top).as_posix() in tracked:
                    value = str(directory / absolute.relative_to(top))
                elif absolute in data_inputs and candidate.suffix not in (".py", ".sh"):
                    # Explicit data manifests are checksummed inputs, not source files.
                    value = str(absolute)
                else:
                    raise ResearchError("commit the executable/config file before running: " + str(absolute))
            token = prefix + separator + value
            rewritten.append(token)
        work = directory / Path(cwd).resolve().relative_to(top)
        if not work.is_dir():
            raise ResearchError("working directory is not part of the committed source tree")
        yield rewritten, work, directory
    finally:
        if checkout_changed(directory):
            print(f"research: retained modified checkout and unexpected outputs at {directory}", file=sys.stderr)
        else:
            subprocess.run(["git", "worktree", "remove", str(directory)], cwd=top, check=True)


def checkout_changed(directory):
    # Ignored relative output directories must not be silently deleted with the checkout.
    return bool(_git(directory, "status", "--porcelain", "--untracked-files=all", "--ignored=matching").strip())


def environment_capture(top):
    env = {"python": platform.python_version()}
    found = [top / name for name in LOCKFILES if (top / name).is_file()]
    if not found:
        return env, ["no_lockfile"]
    env["lockfile_sha256"] = store.sha256_file(found[0])
    return env, []


def host_capture():
    host = {"hostname": socket.gethostname()}
    if shutil.which("nvidia-smi") is None:
        return host, []
    query = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True)
    if query.returncode != 0:
        return host, ["gpu_query_failed"]
    names = [line.strip() for line in query.stdout.splitlines() if line.strip()]
    host["gpu_count"] = len(names)
    if names:
        host["gpu"] = ", ".join(dict.fromkeys(names))
    return host, []


def input_capture(paths):
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        raise ResearchError(f"input files not found: {', '.join(missing)}")
    return [{"path": str(Path(p).resolve()), "sha256": store.sha256_file(p)} for p in paths]


def ensure_executable(command, cwd):
    if not command:
        raise ResearchError("no command given; use `research run --experiment E-n -- CMD ARGS...`")
    program = command[0]
    found = (Path(cwd) / program).is_file() if os.sep in program else shutil.which(program)
    if not found:
        raise ResearchError(f"command not found: {program}")


def capture(root, command, cwd, seed, inputs):
    """RunCapture plus the gaps found before execution."""
    ensure_executable(command, cwd)
    git, gaps, top = git_capture(root, cwd)
    environment, env_gaps = environment_capture(top)
    host, host_gaps = host_capture()
    record = {"command": shlex.join(command), "cwd": str(cwd), "seed": seed, "git": git,
              "environment": environment, "host": host, "inputs": input_capture(inputs)}
    return record, gaps + env_gaps + host_gaps


# ---------------------------------------------------------------- execution

def _tee(pipe, sink, logfile, proc):
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        for chunk in iter(lambda: pipe.read1(65536), b""):
            logfile.write(chunk)
            logfile.flush()
            sink.write(decoder.decode(chunk))
            sink.flush()
        sink.write(decoder.decode(b"", final=True))
    except OSError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    finally:
        pipe.close()


def execute(command, cwd, env, stdout, stderr, directory):
    """Stream logs to durable ignored files, using bounded memory."""
    with (directory / "stdout.log").open("wb") as out, (directory / "stderr.log").open("wb") as err:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            readers = [pool.submit(_tee, proc.stdout, stdout, out, proc),
                       pool.submit(_tee, proc.stderr, stderr, err, proc)]
            try:
                code = proc.wait()
            except KeyboardInterrupt:
                os.killpg(proc.pid, signal.SIGINT)
                code = proc.wait()
            for reader in readers:
                reader.result()
        os.fsync(out.fileno())
        os.fsync(err.fileno())
    return code


def compact(record):
    return {key: record[key] for key in ("command", "seed", "git", "inputs")}


def read_metrics(path):
    """(numeric metrics, gaps). bool is not a metric."""
    if not path.is_file():
        return {}, ["no_metrics"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, ["metrics_not_json"]
    if not isinstance(data, dict):
        return {}, ["metrics_not_object"]
    return data, []
