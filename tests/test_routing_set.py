"""The routing set is frozen (MEASUREMENT_PREREG.md M7): its builder must reproduce it exactly.

Its `realistic` stratum is sampled from the other eval query files, so editing any
of them would silently change the set the baseline was measured on.
"""

import hashlib
import json

from scripts.build_routing_set import EVAL, build
from scripts.eval_routing import SETS


def test_routing_sets_match_their_frozen_hashes():
    for path, frozen in SETS.values():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == frozen, path.name


def test_builder_reproduces_the_frozen_set():
    built = json.dumps({"version": 1, "items": build()}, indent=1, ensure_ascii=False) + "\n"
    assert built.encode("utf-8") == (EVAL / "routing_set.json").read_bytes()
