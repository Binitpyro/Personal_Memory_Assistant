"""Write tests/eval/routing_set.json - the frozen routing labels for QueryPlanner (A3-08, A3-11).

    .venv/Scripts/python.exe scripts/build_routing_set.py

Labels are the outcome the user needs, decided from the class definitions in
MEASUREMENT_PREREG.md (M7), not from what the planner currently does. The
`realistic` stratum is sampled by seed from the existing eval query files so
its content was not chosen by the person writing the labels; every query there
is a content question (gold FULL_RAG). Re-running this script must reproduce
the file byte-for-byte; scripts/eval_routing.py refuses a file whose sha256
differs from the one recorded at freeze time.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "tests" / "eval"

# (query, intent, stratum). Gold mode follows from intent: see GOLD.
HAND: list[tuple[str, str, str]] = [
    # inventory: size / count / type breakdown of the whole index
    ("How many files have I indexed?", "inventory", "clear"),
    ("What's the total size of my index?", "inventory", "clear"),
    ("How much disk space does the index use?", "inventory", "clear"),
    ("Show index stats", "inventory", "clear"),
    ("What types of files are indexed?", "inventory", "clear"),
    ("How many documents are in my library?", "inventory", "clear"),
    ("Give me a file count", "inventory", "clear"),
    ("List all indexed folders", "inventory", "clear"),
    ("How much stuff have I put into PMA so far?", "inventory", "paraphrase"),
    ("Roughly how many notes and PDFs does it know about?", "inventory", "paraphrase"),
    ("Is my library getting huge?", "inventory", "paraphrase"),
    ("What's the breakdown of my files by format?", "inventory", "paraphrase"),
    ("Tell me the size of everything you've indexed", "inventory", "paraphrase"),
    ("How many PDFs do I have indexed?", "inventory", "paraphrase"),
    ("Count the documents you have", "inventory", "paraphrase"),
    ("What kinds of documents have I added?", "inventory", "paraphrase"),
    # project: one-line profile of each indexed folder / project
    ("Give me a project summary", "project", "clear"),
    ("Show project overview", "project", "clear"),
    ("What projects do I have indexed?", "project", "clear"),
    ("Summary of projects in my folders", "project", "clear"),
    ("Summarize my indexed projects", "project", "paraphrase"),
    ("What am I working on, based on my folders?", "project", "paraphrase"),
    ("Which codebases has PMA picked up?", "project", "paraphrase"),
    ("Describe each of my repos in one line", "project", "paraphrase"),
    ("What kind of projects are in my workspace?", "project", "paraphrase"),
    ("Tell me what each indexed folder is for", "project", "paraphrase"),
    # recency / size of individual files: metadata lines into an LLM answer
    ("What did I add most recently?", "recency", "clear"),
    ("Show my latest files", "recency", "clear"),
    ("Which files were modified last?", "recency", "clear"),
    ("What are my largest documents?", "recency", "clear"),
    ("Biggest files in the index?", "recency", "clear"),
    ("What's new since last week?", "recency", "paraphrase"),
    ("Which notes did I change today?", "recency", "paraphrase"),
    ("Which PDFs are the heaviest?", "recency", "paraphrase"),
    ("What have I been adding lately?", "recency", "paraphrase"),
    ("Which file did I touch most recently?", "recency", "paraphrase"),
    # code relationships: callers, dependencies, usage
    ("What calls embed_query?", "graph", "clear"),
    ("Who calls hybrid_retrieve?", "graph", "clear"),
    ("What depends on the LanceDB client?", "graph", "clear"),
    ("Dependencies of the indexing service", "graph", "clear"),
    ("Where is _sanitize_fts_query called?", "graph", "clear"),
    ("Which functions invoke rerank?", "graph", "paraphrase"),
    ("Show the callers of index_folders", "graph", "paraphrase"),
    ("What modules import app.config?", "graph", "paraphrase"),
    ("Which code relies on settings.rrf_k?", "graph", "paraphrase"),
    ("What breaks if I change the chunker's signature?", "graph", "paraphrase"),
    # content: ordinary questions about what the documents say
    ("What were the main causes of the French Revolution?", "content", "clear"),
    ("Explain how photosynthesis converts light into chemical energy", "content", "clear"),
    ("What did my lecture notes say about Bayes' theorem?", "content", "clear"),
    ("Summarise the argument of the chapter on game theory", "content", "clear"),
    ("What is the difference between TCP and UDP?", "content", "clear"),
    ("Which treatment worked best in the trial?", "content", "clear"),
    ("Where do I describe the database schema?", "content", "clear"),
    ("What does the README say about installation?", "content", "clear"),
    # content traps: content questions carrying routing trigger words
    ("How many files does the build script copy into dist?", "content", "trap"),
    ("Summary of project timeline risks in my PM notes", "content", "trap"),
    ("What is the impact of caffeine on sleep according to my notes?", "content", "trap"),
    ("How is entropy used in the thermodynamics lecture?", "content", "trap"),
    ("What does the paper say depends on temperature?", "content", "trap"),
    ("How much storage does a Raft log need per entry?", "content", "trap"),
    ("What's the total size limit for uploads in the API spec?", "content", "trap"),
    ("Give me an overview of the Krebs cycle", "content", "trap"),
    ("What is the connection between inflation and interest rates?", "content", "trap"),
    ("How large is the dataset used in the transformer paper?", "content", "trap"),
    ("List all the side effects mentioned in the drug study", "content", "trap"),
    ("Show my notes on index funds", "content", "trap"),
    ("How many items does the recipe call for?", "content", "trap"),
    ("How big is the moon compared to Earth?", "content", "trap"),
    ("What's the latest thinking on dark matter in my physics notes?", "content", "trap"),
    ("Summarize the project proposal for the solar car", "content", "trap"),
    ("What relates to photosynthesis in my biology notes?", "content", "trap"),
    ("How full is the reservoir in the hydrology report?", "content", "trap"),
    ("What file formats does the export feature support?", "content", "trap"),
    ("What does the textbook say about the largest prime gaps?", "content", "trap"),
]

GOLD = {
    "inventory": "FAST_METADATA",
    "project": "FAST_PROJECT",
    "recency": "FULL_RAG",
    "graph": "GRAPH_SEARCH",
    "content": "FULL_RAG",
}
REALISTIC_SOURCES = (
    "queries.json",
    "queries_squad.json",
    "queries_scifact.json",
    "queries_qtype.json",
)
REALISTIC_PER_SOURCE = 6
SEED = 20261013


def _realistic() -> list[tuple[str, str, str]]:
    out = []
    for name in REALISTIC_SOURCES:
        data = json.loads((EVAL / name).read_text(encoding="utf-8"))
        queries = sorted(q["query"] for q in (data["queries"] if isinstance(data, dict) else data))
        rng = random.Random(f"{SEED}:{name}")  # noqa: S311 # nosec B311 - reproducible sample, not security
        for q in rng.sample(queries, REALISTIC_PER_SOURCE):
            out.append((q, "content", "realistic"))
    return out


def build() -> list[dict]:
    rows = HAND + _realistic()
    queries = [q for q, _, _ in rows]
    assert len(set(queries)) == len(queries), "duplicate query"
    return [
        {
            "id": f"r{i:03d}",
            "query": q,
            "intent": intent,
            "stratum": stratum,
            "gold_mode": GOLD[intent],
        }
        for i, (q, intent, stratum) in enumerate(rows, 1)
    ]


if __name__ == "__main__":
    out = EVAL / "routing_set.json"
    # newline="\n": the sha256 is frozen, so the bytes must not depend on the OS.
    out.write_text(
        json.dumps({"version": 1, "items": build()}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {out}")
