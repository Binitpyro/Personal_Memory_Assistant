"""Nothing derived from a personal corpus may enter git.

`.gitignore` is the only thing standing between a personal document library and a
public MIT repository, and a single deleted line removes it silently. This makes
that line load-bearing in a way a reviewer cannot miss: delete it and a test goes
red rather than a corpus going public.

Two independent assertions per path, because they fail differently:

  ignored    `git check-ignore` - the rule exists and matches. Catches a deleted
             or mistyped pattern before anything is written.
  untracked  `git ls-files` - nothing is in the index. Catches the case
             `.gitignore` cannot: a file added before the rule existed, or one
             forced in with `git add -f`, which stays tracked forever after.

Scope, and why each entry is here:

  corpus_college    text materialised from the user's real coursework and notes
                    by scripts/materialize_corpus.py
  corpus_squad      derived from a public dataset, but large and regenerable
  corpus_scifact    same, 5,183 files
  research/journal  sweep results. Aggregate metrics today, but each row records
                    the corpus it ran against, so a future sweep over
                    corpus_college would put a personal library's structure here

Deterministic, no network (CLAUDE.md section 11).
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - fixed argv, no shell, no user input on the path
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Paths that must never be committed.
#
# Directories are probed via a file INSIDE them, not by their own name, and the
# distinction is not cosmetic. A `foo/` pattern matches directories only, and
# `git check-ignore` cannot tell that a path is a directory when it does not
# exist yet - so a bare `tests/eval/corpus_college` reports NOT ignored on a
# clean checkout while `tests/eval/corpus_college/notes.txt` reports ignored.
# Files are what get committed, so files are what this checks.
PRIVATE_PATHS = [
    "tests/eval/corpus_college/notes.txt",
    "tests/eval/queries_college.json",
    "tests/eval/corpus_squad/squad/Article.txt",
    "tests/eval/queries_squad.json",
    "tests/eval/corpus_scifact/doc.txt",
    "tests/eval/queries_scifact.json",
    "research/journal.jsonl",
    "research/journal_squad.jsonl",
]

# Directory roots, for the tracked-files check - `git ls-files` takes a prefix
# and does not care whether the path exists.
PRIVATE_ROOTS = [
    "tests/eval/corpus_college",
    "tests/eval/queries_college.json",
    "tests/eval/corpus_squad",
    "tests/eval/queries_squad.json",
    "tests/eval/corpus_scifact",
    "tests/eval/queries_scifact.json",
    "research/journal.jsonl",
    "research/journal_squad.jsonl",
]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed argv, shell=False
        [shutil.which("git") or "git", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(),
    reason="not a git checkout, so there is nothing to protect",
)


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_the_ignore_rule_exists_and_matches(path: str):
    """`git check-ignore` exits 0 only when a rule actually matches the path.

    Asserted per path rather than by reading .gitignore, because a rule can be
    present and still not match - a leading slash, a missing trailing slash, or
    a later negation all produce a pattern that looks right and does nothing.
    """
    result = _git("check-ignore", "-q", "--no-index", path)
    assert result.returncode == 0, (
        f"{path} is NOT ignored by .gitignore. If this path was renamed, update "
        f"PRIVATE_PATHS. If the rule was removed, restore it - this is the only "
        f"thing keeping derived personal data out of a public repo."
    )


@pytest.mark.parametrize("path", PRIVATE_ROOTS)
def test_nothing_under_it_is_tracked(path: str):
    """The half .gitignore cannot enforce.

    Ignoring a path does nothing about files already in the index: a file added
    before the rule existed, or one forced in with `git add -f`, stays tracked
    and keeps being committed. That is exactly how the journals reached GitHub -
    they were committed first and ignored afterwards.
    """
    result = _git("ls-files", "--error-unmatch", path)
    tracked = _git("ls-files", "--", path).stdout.strip().splitlines()
    assert not tracked, (
        f"{path} is TRACKED by git ({len(tracked)} file(s)), so .gitignore will "
        f"not stop it being committed. Untrack with: git rm --cached -r {path}"
    )
    assert result.returncode != 0


def test_the_journals_are_present_on_disk_but_unversioned():
    """The point is untracked, not deleted.

    `scripts/autoresearch.py --resume` reads the journal to skip work already
    done, and it holds the per-build numbers behind the measured claims in
    CLAUDE.md. A future refactor that "cleans up" by deleting them instead of
    ignoring them would lose the evidence, so this pins the distinction.
    """
    journal = REPO / "research" / "journal.jsonl"
    if not journal.exists():
        pytest.skip("no sweep has been run in this checkout")
    assert journal.stat().st_size > 0
    assert not _git("ls-files", "--", "research/journal.jsonl").stdout.strip()


def test_the_tracked_research_files_are_documents_not_output():
    """PROGRAM.md and EDITABLE.md stay tracked; nothing generated joins them."""
    tracked = _git("ls-files", "--", "research/").stdout.strip().splitlines()
    assert sorted(tracked) == ["research/EDITABLE.md", "research/PROGRAM.md"], (
        f"unexpected tracked files under research/: {tracked}. Generated output "
        f"belongs on disk and in .gitignore, not in the repo."
    )
