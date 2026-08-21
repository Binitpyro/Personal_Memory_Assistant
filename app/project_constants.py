import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PYPROJECT_FILE = "pyproject.toml"


def _get_app_version() -> str:
    try:
        from importlib.metadata import version

        return version("personal-memory-assistant")
    except Exception:
        logger.debug("importlib.metadata version lookup failed.", exc_info=True)
        pass

    try:
        import tomllib  # Python 3.11+

        pyproject_path = Path(__file__).parent.parent / PYPROJECT_FILE
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                return str(data.get("project", {}).get("version", "0.0.55"))
    except Exception:
        logger.debug("Fallback pyproject.toml version lookup failed.", exc_info=True)
        pass

    return "0.0.55"


# Release version (dynamically loaded from pyproject.toml)
APP_VERSION = _get_app_version()

# Cache Constants
RETRIEVAL_CACHE_MAX_SIZE = 500
RAG_CACHE_MAX_SIZE = 200

# Fusion generation. Part of the retrieval cache key so that a change to how
# results are ranked invalidates cached results even though the index itself is
# unchanged. Bump this on every change to fusion behaviour - otherwise testing a
# fusion fix reads pre-fix cached results and concludes the change did nothing.
#   1 - summary search promoted to a third RRF list (was a post-truncation no-op boost)
#   2 - per-folder_tag recall allocation (source-balanced fusion)
#   3 - FTS leg switched from implicit AND over every whitespace token to OR over
#       stop-word-stripped keywords, and sub-trigram terms dropped. The keyword
#       leg returned nothing for multi-word natural-language queries before this.
FUSION_VERSION = 3

# Regular Expressions for Query Intent
INVENTORY_RE = re.compile(
    r"\b(?:how many|count|do i have|files? do i|"
    r"files? i have|my files|all files|all my|total size|"
    r"breakdown|statistics|stats|types? of files?|extensions?|"
    r"storage|disk space|largest folders?|smallest folders?|"
    r"how big|how large|how much space|file count|indexed files?)\b",
    re.IGNORECASE,
)

LATEST_RE = re.compile(
    r"\b(?:latest|recent|newest|added lately|last updated|last modified)\b", re.IGNORECASE
)
LARGEST_RE = re.compile(
    r"\b(?:largest|biggest|huge|oversized|most space|taking up space)\b", re.IGNORECASE
)
FTS5_OPERATOR_RE = re.compile(r'["*^]|\bAND\b|\bOR\b|\bNOT\b|\bNEAR\b', re.IGNORECASE)


_GRAPH_PHRASES = (
    "what calls",
    "who calls",
    "where is",
    "depends on",
    "dependencies of",
    "what does",
    "how is",
    "relates to",
    "connection between",
    "impact of",
)

_GRAPH_RE = re.compile(
    r"\b(?:what calls|who calls|where is .* called|depends on|dependencies of|"
    r"what does .* use|how is .* used|relates to|connection between|impact of)\b",
    re.IGNORECASE,
)


def is_metadata_intent(query: str) -> bool:
    return bool(
        re.search(
            r"\b(project summary|summary of project|"
            r"show project summary|project overview)\b",
            query.lower(),
        )
    )


def determine_query_intent(query: str) -> dict[str, bool]:
    q = query.lower()
    return {
        "inventory": bool(
            LATEST_RE.search(query) or LARGEST_RE.search(query) or "how many files" in q
        ),
        "project": "project" in q or "overview" in q or "summary" in q,
        "latest": bool(LATEST_RE.search(query)),
        "largest": bool(LARGEST_RE.search(query)),
        "metadata_intent": is_metadata_intent(query),
        "graph": bool(_GRAPH_RE.search(query)),
    }


# Project Signatures for Indexing
UNITY_SCENE_EXT = ".unity"
NODE_PACKAGE_FILE = "package.json"
PYTHON_PROJECT_LABEL = "Python project"

PROJECT_SIGNATURES = [
    ("Unity", "Unity game/application project", [("dir", "Assets"), ("ext", UNITY_SCENE_EXT)]),
    ("Unity", "Unity game/application project", [("ext", UNITY_SCENE_EXT)]),
    ("Godot", "Godot engine project", [("file", "project.godot")]),
    ("React", "React web application", [("file", NODE_PACKAGE_FILE), ("dir", "src")]),
    ("Node.js", "Node.js / JavaScript project", [("file", NODE_PACKAGE_FILE)]),
    ("Python", PYTHON_PROJECT_LABEL, [("file", PYPROJECT_FILE)]),
    ("Python", PYTHON_PROJECT_LABEL, [("file", "setup.py")]),
    ("Python", PYTHON_PROJECT_LABEL, [("file", "requirements.txt")]),
    ("Rust", "Rust project", [("file", "Cargo.toml")]),
    ("Go", "Go project", [("file", "go.mod")]),
    ("Java/Maven", "Java Maven project", [("file", "pom.xml")]),
    ("Java/Gradle", "Java Gradle project", [("file", "build.gradle")]),
    (".NET/C#", ".NET / C# project", [("ext", ".csproj")]),
    ("C/C++", "C/C++ project", [("file", "CMakeLists.txt")]),
    ("C/C++", "C/C++ project", [("file", "Makefile")]),
    ("LaTeX", "LaTeX document project", [("ext", ".tex")]),
]

TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".c",
        ".cpp",
        ".rs",
        ".go",
        ".rb",
        ".html",
        ".css",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".sh",
        ".bat",
        ".log",
        "",
    }
)

KEY_NAMES = {
    "readme.md",
    "readme.txt",
    "readme",
    NODE_PACKAGE_FILE,
    PYPROJECT_FILE,
    "setup.py",
    "requirements.txt",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "cmakelists.txt",
    "makefile",
    ".gitignore",
    "dockerfile",
    "docker-compose.yml",
}
KEY_EXTS = {".sln", ".csproj", UNITY_SCENE_EXT}
