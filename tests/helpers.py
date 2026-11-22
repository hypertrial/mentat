import shutil
import subprocess
import zlib
from contextlib import contextmanager
from pathlib import Path

from mentat import evidence

PRE_SPLIT_STATE = Path(__file__).parent / "fixtures/records/main-pre-split/state"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo, message="fixture"):
    git(repo, "add", "-A")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def cat_object(repo, kind, oid):
    """Return the raw content of a Git object."""
    return subprocess.run(
        ["git", "-C", str(repo), "cat-file", kind, oid], check=True, capture_output=True
    ).stdout


@contextmanager
def replaced_loose_object(repo, oid, kind, content):
    """Swap a loose object for a well-formed object with other content, under the same ID."""
    path = Path(repo) / ".git/objects" / oid[:2] / oid[2:]
    original, mode = path.read_bytes(), path.stat().st_mode
    path.chmod(0o644)
    path.write_bytes(zlib.compress(b"%s %d\0" % (kind.encode(), len(content)) + content))
    try:
        yield
    finally:
        path.chmod(0o644)
        path.write_bytes(original)
        path.chmod(mode)


def copy_state(source, destination):
    shutil.copytree(source, destination)
    destination.chmod(0o700)
    return destination


def reseal(state, run_id, record):
    """Rewrite a saved manifest in canonical form and refresh its receipt, as the writer does."""
    raw = evidence.json_bytes(record)
    (state / "runs" / run_id / "manifest.json").write_bytes(raw)
    (state / "receipts" / run_id).write_bytes(evidence.digest(raw).encode())
