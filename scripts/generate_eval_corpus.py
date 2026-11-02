"""Generate the multi-chunk labelled evaluation corpus.

Why this exists: `tests/eval/corpus` is 24 files of 700-1100 bytes, so at
`chunk_size = 512` the median file is one or two chunks and chunk ~= document.
A fixture like that cannot express boundary quality, overlap or chunk size - it
scores document routing and calls it retrieval (CLAUDE.md 8.7 D1/D2). This one
is built so that chunking has somewhere to go wrong.

Three properties do the work:

* **Documents are long.** Target >= 15 chunks each at the shipped 512-character
  size, so a chunk is a fraction of a document rather than the whole of it.
* **Answers are spans, not files.** Ground truth is a character range in the
  source text, so a retrieved chunk is scored on whether it actually overlaps
  the passage that answers the question. File-level labels cannot distinguish
  "found the right document" from "found the right paragraph".
* **Answer spans vary in length on purpose.** Short spans sit inside one chunk
  at any setting; long ones straddle three chunks at 512 characters and one at
  2048. That difference is the whole signal a chunk-size sweep reads.

Every topic also gets a distractor document in another domain: same vocabulary,
same subject, no answer. Without them, document routing alone solves the corpus
and the chunk-level metrics saturate.

Determinism: fixed seed, and the layout is a pure function of the topic table
below. Re-running reproduces the corpus byte for byte, which is what lets the
generated corpus be committed and diffed.

**Newlines are written LF explicitly**, and the directory is pinned
`text eol=lf` in `.gitattributes`.

This is defence in depth rather than a fix for a live break, and the distinction
is worth stating because the first version of this comment got it wrong. CRLF
does *not* currently shift the offsets: `_extract_plain_text_stream` opens in
text mode, so a CRLF checkout collapses to LF and matches the LF text these
offsets were computed against. The pin exists because that equality depends on
every reader being text mode, which is not guaranteed - rust_core handles bytes,
and a binary or `newline=""` read would desynchronise the labels with nothing
raising. Pinning makes disk and stream identical so a reader change cannot do
that. See tests/test_eval_corpus_spans.py.

Usage:
    .venv\\Scripts\\python.exe scripts/generate_eval_corpus.py
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

CORPUS_DIR = Path("tests/eval/corpus_large")
QUERIES_FILE = Path("tests/eval/queries_large.json")

SEED = 20260901

# ── Topic table ──────────────────────────────────────────────────────────────
# Hand-written, in the register of the existing corpus: working notes and
# reference pages about simulation and pipeline work, not lorem ipsum. Generated
# prose is confined to the reference bulk further down, where real technical
# documentation is genuinely templated (parameter lists, worked examples,
# troubleshooting entries) and reads that way honestly.
#
# `answer` is the passage the query is asking about. It is the only place in the
# corpus where its claim appears, and `answer_len` records the intent:
#   "short" - one or two sentences, fits inside a single 512-char chunk
#   "long"  - several hundred characters, straddles chunk boundaries at 512
TOPICS: list[dict[str, Any]] = [
    {
        "slug": "curl_noise",
        "title": "Curl Noise",
        "subject": "curl noise",
        "vocab": ["potential field", "divergence", "vorticity", "gradient", "streamline"],
        "query": "why does taking the curl guarantee zero divergence",
        "answer_len": "long",
        "answer": (
            "The reason the construction works is an identity rather than a "
            "tuning choice: the divergence of a curl is identically zero for "
            "any twice-differentiable potential field. So if the velocity is "
            "defined as the curl of some vector potential, incompressibility "
            "is not something the solver has to enforce afterwards, it is a "
            "property the field cannot violate in the first place. That is the "
            "whole appeal. A field built this way never needs a pressure "
            "projection pass to remove sources and sinks, because there were "
            "none to remove. The cost is that you give up direct control of "
            "the velocity itself: you author the potential and accept whatever "
            "velocity the curl produces from it."
        ),
        "sections": [
            (
                "What the field is for",
                "Curl noise gives a moving field that looks like fluid without "
                "running a fluid solve. It is cheap enough to evaluate per "
                "point per frame, which is what makes it usable on the counts "
                "we actually ship.",
            ),
            (
                "Authoring the potential",
                "Everything is controlled through the potential rather than the "
                "velocity. This takes some getting used to, because the shape "
                "you author and the motion you get are related but not the "
                "same thing.",
            ),
            (
                "Boundaries",
                "Near a collider the potential has to be flattened along the "
                "surface, otherwise the field pushes straight through it. "
                "Ramping the potential to a constant near the boundary is the "
                "usual fix and costs one extra lookup.",
            ),
        ],
    },
    {
        "slug": "attribute_transfer",
        "title": "Attribute Transfer",
        "subject": "attribute transfer",
        "vocab": ["source geometry", "target points", "search radius", "kernel", "match"],
        "query": "what decides which source point an attribute is copied from",
        "answer_len": "short",
        "answer": (
            "Selection is by nearest source point inside the search radius, and "
            "when several fall inside it the values are blended by distance "
            "rather than the closest one winning outright."
        ),
        "sections": [
            (
                "What it does",
                "Attribute transfer copies values from one piece of geometry "
                "onto another that does not share its topology. The two do not "
                "need matching point counts or matching order.",
            ),
            (
                "Radius",
                "Too small a radius leaves target points with nothing in range "
                "and they keep their default value, which usually reads as "
                "holes. Too large and unrelated regions bleed into each other.",
            ),
            (
                "Cost",
                "The lookup dominates. Cost scales with target point count "
                "times the average number of source points inside the radius, "
                "so widening the radius is more expensive than it looks.",
            ),
        ],
    },
    {
        "slug": "scatter_density",
        "title": "Scatter Density",
        "subject": "point scattering",
        "vocab": ["density attribute", "area", "relaxation", "seed", "distribution"],
        "query": "how do I control scatter density with an attribute",
        "answer_len": "short",
        "answer": (
            "Bind a per-point density attribute on the surface and the scatter "
            "treats it as a multiplier against the global count, so a value of "
            "zero suppresses points entirely and the total count is the "
            "integral of density over surface area rather than the number you "
            "typed."
        ),
        "sections": [
            (
                "Counts are approximate",
                "The requested count is a target, not a guarantee. Relaxation "
                "and density weighting both move the final number, and on "
                "small surfaces the discrepancy is proportionally larger.",
            ),
            (
                "Relaxation",
                "Relaxation pushes points apart to even out spacing. It costs "
                "iterations and it fights the density attribute, so heavy "
                "relaxation flattens exactly the variation you asked for.",
            ),
            (
                "Seeds",
                "Changing the seed reshuffles every point. Anything downstream "
                "that binds to point number rather than position will pop when "
                "the seed moves.",
            ),
        ],
    },
    {
        "slug": "geometry_cache",
        "title": "Geometry Cache",
        "subject": "the geometry cache",
        "vocab": ["cache key", "invalidation", "frame range", "checkpoint", "revision"],
        "query": "why was stale geometry served and how is the cache keyed",
        "answer_len": "long",
        "answer": (
            "The stale reads were a keying bug, not an eviction bug. The key "
            "was built from the file path and the frame number alone, so two "
            "different revisions of the same asset at the same frame collided "
            "and the first one written won for the rest of the session. "
            "Nothing was wrong with the eviction policy and nothing was "
            "corrupt on disk. The fix was to fold the source revision into the "
            "key, which makes a re-published asset a different entry rather "
            "than an overwrite of the old one. Anything that keys a cache on a "
            "path without a content or revision component has this bug latent "
            "in it, and it only shows up once someone re-publishes mid-session."
        ),
        "sections": [
            (
                "What is cached",
                "Evaluated geometry is held per frame so that scrubbing does "
                "not re-cook the whole chain. The cache is per session and does "
                "not survive a restart.",
            ),
            (
                "Memory",
                "The cache is bounded by total bytes rather than entry count, "
                "because one heavy frame can outweigh a hundred light ones.",
            ),
            (
                "When to clear it",
                "Clearing is cheap and almost always the right first move when "
                "something looks wrong. A stale read is much harder to "
                "recognise than a slow re-cook.",
            ),
        ],
    },
    {
        "slug": "colour_ingest",
        "title": "Colour At Ingest",
        "subject": "colour management at ingest",
        "vocab": ["scene linear", "display encoded", "transfer function", "primaries", "LUT"],
        "query": "scene linear versus display encoded textures at ingest",
        "answer_len": "long",
        "answer": (
            "The rule at ingest is that anything feeding lighting maths has to "
            "be scene linear, and anything that was authored to be looked at "
            "on a monitor is display encoded until you convert it. Albedo and "
            "emission maps are the first kind and need the transfer function "
            "removed on read. Masks, roughness and normal maps are data rather "
            "than colour and must not be converted at all, because applying a "
            "transfer function to a roughness map silently changes its "
            "midpoint. The failure is subtle in both directions: converting "
            "twice darkens midtones, and not converting at all leaves lighting "
            "maths operating on values that are not proportional to light."
        ),
        "sections": [
            (
                "Where it goes wrong",
                "Almost every problem here is a double conversion or a missing "
                "one, and neither announces itself. The image still looks "
                "plausible, it is just wrong by a gamma.",
            ),
            (
                "Naming",
                "Encoding the intended space in the filename is crude and it "
                "works. It survives being copied between machines, which "
                "sidecar metadata frequently does not.",
            ),
            (
                "Review",
                "Review has to happen through the same display transform the "
                "shot will be graded under, otherwise notes are being given on "
                "an image nobody will ever see again.",
            ),
        ],
    },
    {
        "slug": "turbulence",
        "title": "Turbulence Octaves",
        "subject": "turbulence octaves",
        "vocab": ["octave", "lacunarity", "roughness", "amplitude", "frequency"],
        "query": "turbulence octaves and how much high frequency detail to use",
        "answer_len": "short",
        "answer": (
            "Past about five octaves the added detail lands below the size of a "
            "rendered pixel and shows up as sampling noise rather than "
            "structure, so the cost is real and the benefit is not."
        ),
        "sections": [
            (
                "How octaves stack",
                "Each octave adds a copy of the noise at higher frequency and "
                "lower amplitude. Lacunarity sets how fast frequency climbs and "
                "roughness sets how fast amplitude falls.",
            ),
            (
                "Cost",
                "Cost is linear in octave count, so this is one of the few "
                "quality knobs where the price is easy to predict.",
            ),
            (
                "Animation",
                "Animating the offset rather than the frequency keeps the "
                "structure recognisable while it moves. Animating frequency "
                "makes the whole field boil.",
            ),
        ],
    },
    {
        "slug": "advection",
        "title": "Velocity Advection",
        "subject": "velocity field advection",
        "vocab": ["velocity field", "backward trace", "interpolation", "step size", "stability"],
        "query": "why is backward tracing stable at large step sizes",
        "answer_len": "long",
        "answer": (
            "Backward tracing is unconditionally stable because it never "
            "extrapolates. It looks up where a value came from and reads a "
            "value that already exists in the field, so the result is always "
            "bounded by values the field already held and cannot grow without "
            "limit no matter how large the step is. Forward stepping has no "
            "such guarantee: it writes to wherever the step lands, and once "
            "the step is large enough relative to the feature size the scheme "
            "diverges. What backward tracing pays instead is blurring. Every "
            "lookup interpolates, and interpolation is a low-pass filter, so "
            "detail is lost in proportion to how many times the field has been "
            "advected rather than to how large any one step was."
        ),
        "sections": [
            (
                "Carrying values",
                "Each element is carried along by whatever is moving around it. "
                "We look up the local direction and speed where it currently "
                "sits, then move it that far.",
            ),
            (
                "Choosing a step",
                "Larger steps are cheaper and blurrier. Smaller steps preserve "
                "structure and cost proportionally more.",
            ),
            (
                "Compounding loss",
                "Because the blurring compounds per step, a long simulation "
                "loses detail even where the motion is gentle.",
            ),
        ],
    },
    {
        "slug": "farm_batching",
        "title": "Farm Batching",
        "subject": "render farm batching",
        "vocab": ["task", "startup cost", "batch size", "scheduler", "retry"],
        "query": "batching frames into farm tasks to amortise startup cost",
        "answer_len": "short",
        "answer": (
            "Batch size should be set so that scene load is a small fraction of "
            "task runtime, which in practice means grouping frames until each "
            "task runs at least ten times the load time."
        ),
        "sections": [
            (
                "Why batch at all",
                "Every task pays scene load before it renders anything. One "
                "frame per task means paying that cost once per frame.",
            ),
            (
                "The tradeoff",
                "Large batches amortise load well and retry badly: a failure "
                "anywhere in the batch costs the whole batch.",
            ),
            (
                "Stragglers",
                "The slowest task sets the wall clock. Very large batches "
                "produce a long tail where most of the farm sits idle.",
            ),
        ],
    },
]

# Which domain holds the answer, and which holds the distractor. Spread so that
# no single domain owns every answer - otherwise a folder_tag prior solves it.
DOMAIN_CYCLE = ["docs", "research", "notes"]

# ── Generated reference bulk ─────────────────────────────────────────────────
# Real technical documentation is templated in exactly these three shapes, so
# generating them is honest rather than filler. Each is instantiated with the
# topic's own vocabulary so the text stays on-subject: a distractor has to be
# genuinely about the same thing to be a distractor at all.

_PARAM_NOTES = [
    "Raising it widens the affected region and increases evaluation cost roughly in proportion.",
    "The default is chosen for mid-scale setups and is usually too low on "
    "anything shot at close range.",
    "It interacts with the sampling rate, so changing one without the other "
    "moves the result in ways that look like a bug.",
    "Leave it at the default unless a specific artefact is pushing you off it, "
    "and write down why when you do.",
    "Values below the floor are clamped silently, which is worth knowing "
    "before spending an afternoon on it.",
    "It has no effect at all when the upstream input is uniform, which makes "
    "it look broken on a test scene.",
]

_EXAMPLE_NOTES = [
    "The run finished inside the frame budget and the result held up under "
    "review, so this is the setup that shipped.",
    "This overshot the budget by roughly a third and was dropped, but it is "
    "recorded because the look was closer.",
    "Comparable quality at noticeably lower cost, which is what made it worth "
    "keeping in the notes.",
    "Marginal on a workstation and comfortable on the farm, so it depends "
    "entirely on where the work runs.",
]

_TROUBLE_NOTES = [
    "Almost always an upstream input that is not what it is assumed to be. "
    "Check it before changing any setting here.",
    "Usually a resolution mismatch between what was authored and what is being "
    "evaluated. It disappears when they are matched.",
    "This one is a genuine limitation rather than a misconfiguration, and the "
    "workaround costs an extra evaluation.",
    "Reproducible only with a cold cache, which is why it survived review for as long as it did.",
]


def _params_section(topic: dict[str, Any], rng: random.Random, count: int) -> str:
    lines = ["## Parameters", ""]
    for i in range(count):
        term = topic["vocab"][i % len(topic["vocab"])]
        lines.append(f"### {term.title()} {i + 1}")
        lines.append("")
        lines.append(
            f"Controls how {topic['subject']} responds to the {term} at this "
            f"stage of evaluation. {rng.choice(_PARAM_NOTES)} "
            f"{rng.choice(_PARAM_NOTES)}"
        )
        lines.append("")
    return "\n".join(lines)


def _examples_section(topic: dict[str, Any], rng: random.Random, count: int) -> str:
    lines = ["## Worked examples", ""]
    for i in range(count):
        term = topic["vocab"][(i + 2) % len(topic["vocab"])]
        lines.append(f"### Setup {i + 1}")
        lines.append("")
        lines.append(
            f"Ran {topic['subject']} with the {term} at {(i + 1) * 4} and the "
            f"sample count at {(i + 1) * 32}. {rng.choice(_EXAMPLE_NOTES)}"
        )
        lines.append("")
    return "\n".join(lines)


def _trouble_section(topic: dict[str, Any], rng: random.Random, count: int) -> str:
    lines = ["## Troubleshooting", ""]
    for i in range(count):
        term = topic["vocab"][(i + 1) % len(topic["vocab"])]
        lines.append(f"### Result looks wrong around the {term}")
        lines.append("")
        lines.append(f"Seen while working on {topic['subject']}. {rng.choice(_TROUBLE_NOTES)}")
        lines.append("")
    return "\n".join(lines)


def _prose_sections(topic: dict[str, Any]) -> str:
    lines = []
    for heading, body in topic["sections"]:
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def build_answer_doc(topic: dict[str, Any], rng: random.Random) -> tuple[str, int, int]:
    """Assemble the document holding the answer.

    Returns the text plus the answer's character span. The span is located by
    searching the assembled text rather than tracked while building it: the
    search is what guarantees the recorded offsets address the bytes that were
    actually written, and it fails loudly if the passage ever stops being
    unique.

    The answer is deliberately NOT placed first. A passage at offset 0 is found
    by any configuration, because the first chunk of a document starts there
    whatever the chunk size is - which would make the fixture agree with itself
    regardless of what chunking does.
    """
    head = [f"# {topic['title']}", "", _prose_sections(topic)]
    # Bulk before the answer, so it sits well inside the document.
    head.append(_params_section(topic, rng, 11))
    head.append(_trouble_section(topic, rng, 6))
    head.append("## Why it behaves this way")
    head.append("")
    head.append(topic["answer"])
    head.append("")
    head.append(_examples_section(topic, rng, 10))
    head.append(_trouble_section(topic, rng, 8))
    head.append(_params_section(topic, rng, 11))

    text = "\n".join(head)
    if not text.endswith("\n"):
        text += "\n"

    occurrences = text.count(topic["answer"])
    if occurrences != 1:
        raise ValueError(
            f"{topic['slug']}: answer passage appears {occurrences} times, expected exactly 1"
        )
    start = text.index(topic["answer"])
    return text, start, start + len(topic["answer"])


def build_distractor_doc(topic: dict[str, Any], rng: random.Random, flavour: str) -> str:
    """Same subject and vocabulary, no answer.

    Without these the corpus is solved by document routing: any signal that
    picks the right file also picks the right passage, because the right file
    is the only one that mentions the subject at all.
    """
    lines = [f"# {topic['title']} - {flavour}", ""]
    lines.append(
        f"Working notes on {topic['subject']}. These are observations from "
        f"shot work rather than a reference page, and they deliberately do not "
        f"restate the underlying reason the setup behaves the way it does."
    )
    lines.append("")
    lines.append(_trouble_section(topic, rng, 9))
    lines.append(_examples_section(topic, rng, 11))
    lines.append(_params_section(topic, rng, 14))
    text = "\n".join(lines)
    return text if text.endswith("\n") else text + "\n"


def generate(corpus_dir: Path, queries_file: Path) -> dict[str, Any]:
    # Seeded and deterministic on purpose: the corpus is committed and diffed,
    # and the answer offsets are only valid for the exact bytes it produces.
    rng = random.Random(SEED)  # noqa: S311 # nosec B311
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for domain in DOMAIN_CYCLE:
        (corpus_dir / domain).mkdir(parents=True, exist_ok=True)

    queries: list[dict[str, Any]] = []

    for i, topic in enumerate(TOPICS):
        answer_domain = DOMAIN_CYCLE[i % len(DOMAIN_CYCLE)]
        distractor_domain = DOMAIN_CYCLE[(i + 1) % len(DOMAIN_CYCLE)]
        second_distractor = DOMAIN_CYCLE[(i + 2) % len(DOMAIN_CYCLE)]

        text, start, end = build_answer_doc(topic, rng)
        rel_answer = f"{answer_domain}/{topic['slug']}.md"
        _write(corpus_dir / rel_answer, text)

        rel_d1 = f"{distractor_domain}/{topic['slug']}_field_notes.md"
        _write(corpus_dir / rel_d1, build_distractor_doc(topic, rng, "field notes"))

        rel_d2 = f"{second_distractor}/{topic['slug']}_review.md"
        _write(corpus_dir / rel_d2, build_distractor_doc(topic, rng, "review"))

        queries.append(
            {
                "id": topic["slug"],
                "query": topic["query"],
                "type": "single",
                "answer_len": topic["answer_len"],
                "relevant_files": [rel_answer],
                "expected_domains": [answer_domain],
                "answer_spans": [{"file": rel_answer, "start": start, "end": end}],
                "distractors": [rel_d1, rel_d2],
            }
        )

    payload = {
        "_readme": [
            "Ground truth for tests/eval/corpus_large. Paths are relative to it.",
            "",
            "Generated by scripts/generate_eval_corpus.py - edit the topic table",
            "there and regenerate, never hand-edit this file or the corpus. The",
            "offsets in answer_spans are computed from the generated bytes and",
            "are wrong the moment either side is edited independently.",
            "",
            "answer_spans is what makes this fixture able to see chunking at",
            "all. relevant_files scores document routing, which the existing",
            "24-file corpus already covers; a character range scores whether the",
            "retrieved chunk contains the passage that answers the question.",
            "",
            "answer_len records intent, not measurement: 'short' spans fit in a",
            "single 512-character chunk, 'long' ones straddle boundaries. A",
            "chunk-size sweep should move the two groups differently, and if it",
            "does not, the metric is not reading what it claims to read.",
            "",
            "Every topic carries two same-subject distractor documents. Without",
            "them document routing alone solves the corpus and every chunk-level",
            "number saturates.",
        ],
        "queries": queries,
    }
    _write(queries_file, json.dumps(payload, indent=1) + "\n")
    return payload


def _write(path: Path, text: str) -> None:
    """Write LF, always. See the module docstring - CRLF shifts every offset."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    p.add_argument("--queries-file", type=Path, default=QUERIES_FILE)
    args = p.parse_args()

    payload = generate(args.corpus_dir, args.queries_file)

    files = sorted(args.corpus_dir.rglob("*.md"))
    sizes = [len(f.read_text(encoding="utf-8")) for f in files]
    approx_chunks = [s // 512 for s in sizes]
    print(f"corpus   : {len(files)} files in {args.corpus_dir}")
    print(
        f"size     : min {min(sizes)}  median {sorted(sizes)[len(sizes) // 2]}  max {max(sizes)} chars"
    )
    print(
        f"~chunks  : min {min(approx_chunks)}  median {sorted(approx_chunks)[len(approx_chunks) // 2]}"
        f"  max {max(approx_chunks)}  (at chunk_size=512)"
    )
    print(f"queries  : {len(payload['queries'])} in {args.queries_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
