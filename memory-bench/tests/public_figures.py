"""The load-bearing published figures, recomputed from the released corpus.

Every number the public tree states about its own leak-closure is a measurement over
``public/data``, and the release ships those numbers in five places: ``public/README.md``,
``public/schema/bench-record.v3.schema.json``, ``public/validator/membench_validate.py``,
``membench/public_export.py`` and ``scripts/run_public_baseline.py``. Four of the five
ship to a downloader. A number that drifts from the corpus is not a typo there: the
schema and the validator are the documents that tell a third party what the order is
evidence of, so a stale figure is a false claim about how hard the benchmark is.

It has drifted. The round-3 figures ("49 records of 160 (0.3063) ... p = 0.0067", "35 in
160 (0.2188) at p = 0.5374") outlived the round-4 corpus that replaced them and were
still shipping in all five files; the README's first-6 chance-pass rate was the naive
all-gold-in-the-head count, which ignores that stating a forbidden value zeroes the
answer, and so overstated chance by roughly a factor of two.

So the figures live HERE, computed once, and the prose quotes this module's rendering
verbatim. ``test_published_figures_match_the_corpus.py`` asserts each rendered claim
appears in each file that is supposed to carry it, which fails the build on the day the
corpus is re-drawn and the prose is not rewritten. ``test_public_corpus_contract.py``
asserts the same measurements clear the release's acceptance bounds. One measurement,
two consumers: a fix that edits only the prose cannot pass, and a re-draw that moves a
figure cannot ship silently.
"""

from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from statistics import mean, median
from typing import Any, NamedTuple

from membench.metrics.scorers import states_value

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = REPO_ROOT.parent / "public"
RELEASE_DIR = PUBLIC_ROOT / "data"
BASELINE_SCRIPT = REPO_ROOT / "scripts" / "run_public_baseline.py"
VALIDATOR_PATH = PUBLIC_ROOT / "validator" / "membench_validate.py"
CORPUS = REPO_ROOT / "fixtures" / "worlds-public-v1"
NECESSITY = CORPUS / "necessity.json"

# The verifier's settings. The seed fixes the draw; the p-value is a property of the
# release, not of the machine that reads it.
PERMUTATION_DRAWS = 20_000
PERMUTATION_SEED = 20250922


@lru_cache(maxsize=1)
def released() -> tuple[dict[str, Any], ...]:
    """Every published record, in file then line order — what a downloader has."""
    files = sorted(RELEASE_DIR.glob("*.jsonl"))
    assert files, f"no released JSONL under {RELEASE_DIR}"
    return tuple(
        json.loads(line)
        for path in files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


@lru_cache(maxsize=1)
def baseline_driver() -> Any:
    """``scripts/run_public_baseline.py``, imported by path.

    The driver owns ``grade`` and ``_record_from_payload``, so the pass rates below are
    scored by the same code that scores an arm rather than by a second implementation
    of the same rule.
    """
    spec = importlib.util.spec_from_file_location("run_public_baseline_figures", BASELINE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def validator_module() -> Any:
    """``public/validator/membench_validate.py``, imported by path.

    The coverage figures below are what the SHIPPED validator catches, so they are
    measured by calling it rather than by reimplementing its rules here. A rule deleted
    from that file moves a number in this one, which is the point.
    """
    spec = importlib.util.spec_from_file_location("membench_validate_figures", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bucket_merge(record: Mapping[str, Any]) -> dict[str, str]:
    """``{**gold, **distractors, **superseded}``, which is what the first release's
    wording produced when read literally: "merged into one flat id-to-text mapping"."""
    evidence = record["evidence"]
    return {**evidence["gold"], **evidence["distractors"], **evidence["superseded"]}


def published_order(record: Mapping[str, Any]) -> list[str]:
    """The normative seeding order, read off the record rather than recomputed."""
    return [str(alias) for alias in record["candidate_pool"]["ids"]]


def ascending_alias_order(record: Mapping[str, Any]) -> list[str]:
    """The order this release replaced: the pool sorted by alias."""
    return sorted(published_order(record))


# --------------------------------------------------------------------------- #
# Where the gold sits in a pool order.
# --------------------------------------------------------------------------- #


class RankStat(NamedTuple):
    """Where the gold sits in one pool order, and how surprising that is."""

    label: str
    n_records: int
    n_gold: int
    first_is_gold: int
    first_rate: float
    chance_rate: float
    mean_rank: float
    p_first_upper: float
    p_rank_two: float

    def __str__(self) -> str:
        return (
            f"{self.label} n={self.n_records}: first entry is gold "
            f"{self.first_is_gold}/{self.n_records} = {self.first_rate:.4f} against chance "
            f"{self.chance_rate:.4f}, p = {self.p_first_upper:.4f}; mean normalized gold "
            f"rank {self.mean_rank:.4f} against uniform 0.5000 over {self.n_gold} gold "
            f"entries, p = {self.p_rank_two:.4f}"
        )


Rows = list[tuple[str, list[str], frozenset[str]]]


def ordered_rows(order: Callable[[Mapping[str, Any]], list[str]]) -> Rows:
    """One (tier, pool in the order under test, gold ids) row per released record."""
    return [
        (str(record["tier"]), order(record), frozenset(record["evidence"]["gold_ids"]))
        for record in released()
    ]


@lru_cache(maxsize=4)
def _null_draws(
    shapes: tuple[tuple[str, int, int], ...], draws: int, seed: int
) -> dict[str, tuple[tuple[int, ...], tuple[float, ...]]]:
    """The null distribution of both rank statistics, per group.

    A draw assigns each record's gold to positions sampled uniformly from that record's
    own pool. Pool sizes and gold counts are therefore held fixed and only WHERE the
    gold landed varies, so a rejection is about the order and not about corpus shape.

    ``shapes`` is the only input, so every order over the same release shares one set of
    draws. That is deliberate: comparing the published order and the order it replaced
    against different noise would confound the comparison with the sampling.
    """
    groups = ["corpus", *sorted({tier for tier, _, _ in shapes})]
    rng = random.Random(seed)
    firsts: dict[str, list[int]] = {group: [] for group in groups}
    ranks: dict[str, list[float]] = {group: [] for group in groups}
    for _ in range(draws):
        drawn_first = dict.fromkeys(groups, 0)
        drawn_rank = dict.fromkeys(groups, 0.0)
        for tier, size, n_gold in shapes:
            positions = rng.sample(range(size), n_gold)
            hit = int(0 in positions)
            rank_sum = sum(positions) / (size - 1)
            for group in ("corpus", tier):
                drawn_first[group] += hit
                drawn_rank[group] += rank_sum
        for group in groups:
            firsts[group].append(drawn_first[group])
            ranks[group].append(drawn_rank[group])
    return {group: (tuple(firsts[group]), tuple(ranks[group])) for group in groups}


def rank_uniformity(
    rows: Rows, *, draws: int = PERMUTATION_DRAWS, seed: int = PERMUTATION_SEED
) -> dict[str, RankStat]:
    """Permutation test over ``rows``: is the gold's position in the pool uniform?

    Two statistics, because they fail differently. The share of records whose FIRST
    entry is gold is what a truncating runner exploits directly, and is one-sided: only
    an excess is a leak. The mean normalized rank of every gold id reads the whole list
    and catches a bias that pulls gold earlier without putting it first, and is
    two-sided, because a pool that systematically buries the gold is equally a channel.

    The corpus and both tiers are scored against one set of draws, which correlates the
    three p-values with each other. Each one on its own is still exact.
    """
    groups = ["corpus", *sorted({tier for tier, _, _ in rows})]
    shapes: list[tuple[str, int, int]] = []
    first_hits = dict.fromkeys(groups, 0)
    rank_sums = dict.fromkeys(groups, 0.0)
    n_records = dict.fromkeys(groups, 0)
    n_gold = dict.fromkeys(groups, 0)
    chance_sums = dict.fromkeys(groups, 0.0)

    for tier, pool, gold in rows:
        assert len(pool) > 1, "a pool of one entry has no order to test"
        assert gold, "a record with no gold cannot place its gold anywhere"
        assert gold <= set(pool), "a gold id sits outside the pool it is ranked in"
        hit = int(pool[0] in gold)
        rank_sum = sum(i for i, alias in enumerate(pool) if alias in gold) / (len(pool) - 1)
        for group in ("corpus", tier):
            first_hits[group] += hit
            rank_sums[group] += rank_sum
            n_records[group] += 1
            n_gold[group] += len(gold)
            chance_sums[group] += len(gold) / len(pool)
        shapes.append((tier, len(pool), len(gold)))

    null = _null_draws(tuple(shapes), draws, seed)
    stats: dict[str, RankStat] = {}
    for group in groups:
        drawn_firsts, drawn_ranks = null[group]
        observed_first = first_hits[group] / n_records[group]
        observed_rank = rank_sums[group] / n_gold[group]
        ge_first = sum(1 for value in drawn_firsts if value / n_records[group] >= observed_first)
        ge_rank = sum(1 for value in drawn_ranks if value / n_gold[group] >= observed_rank)
        le_rank = sum(1 for value in drawn_ranks if value / n_gold[group] <= observed_rank)
        stats[group] = RankStat(
            label=group,
            n_records=n_records[group],
            n_gold=n_gold[group],
            first_is_gold=first_hits[group],
            first_rate=observed_first,
            chance_rate=chance_sums[group] / n_records[group],
            mean_rank=observed_rank,
            p_first_upper=ge_first / draws,
            p_rank_two=min(1.0, 2 * min(ge_rank, le_rank) / draws),
        )
    return stats


@lru_cache(maxsize=1)
def published_ranks() -> dict[str, RankStat]:
    return rank_uniformity(ordered_rows(published_order))


@lru_cache(maxsize=1)
def ascending_alias_ranks() -> dict[str, RankStat]:
    return rank_uniformity(ordered_rows(ascending_alias_order))


# --------------------------------------------------------------------------- #
# What a first-k policy scores over a pool order.
# --------------------------------------------------------------------------- #


class FirstKStat(NamedTuple):
    """What "take the first k entries I was handed" scores, and what chance scores."""

    label: str
    k: int
    n_records: int
    recall: float
    chance_recall: float
    reward: float
    passes: int
    chance_passes: float

    def __str__(self) -> str:
        return (
            f"first-{self.k} over {self.label}: recall {self.recall:.3f} against chance "
            f"{self.chance_recall:.3f}, reward {self.reward:.3f}, pass {self.passes}/"
            f"{self.n_records} against chance {self.chance_passes:.2f}/{self.n_records}"
        )


def _chance_pass_probability(texts: Mapping[str, str], record: Any, k: int) -> float:
    """P(a uniformly drawn k-subset of this record's pool passes the grader).

    Not ``C(k, |gold|) / C(|pool|, |gold|)``. That is the probability the head happens
    to be exactly the gold set, and it is the number the README used to publish as
    "chance" — but the grader does not ask for the gold set. It asks for every expected
    value to be stated and NO forbidden value to be stated, and a head that carries all
    the gold plus one superseded candidate scores zero. Counting subsets that hold the
    gold and ignoring the ones a stale neighbour zeroes overstates chance by about a
    factor of two on this release, which flatters the published order in the one
    direction that matters: it makes the measured policy look further BELOW chance than
    it is.

    So the count is taken over the candidates that state nothing forbidden, by
    inclusion-exclusion on which expected values the head misses. Values are matched with
    the grader's own ``states_value``, so "states it" means here exactly what it means
    when an arm is scored.
    """
    pool = list(texts)
    clean = [
        alias
        for alias in pool
        if not any(states_value(texts[alias], value) for value in record.forbidden_values)
    ]
    covers = [
        {alias for alias in clean if states_value(texts[alias], value)}
        for value in record.expected_values
    ]
    total = math.comb(len(pool), k)
    assert total, f"a pool of {len(pool)} cannot be drawn {k} from"
    passing = 0
    for mask in range(1 << len(covers)):
        missed: set[str] = set()
        for index, cover in enumerate(covers):
            if mask >> index & 1:
                missed |= cover
        sign = -1 if bin(mask).count("1") % 2 else 1
        passing += sign * math.comb(len(clean) - len(missed), k)
    return passing / total


def first_k_stat(
    label: str, order: Callable[[Mapping[str, Any]], list[str]], *, k: int | None = None
) -> FirstKStat:
    """Score "take the first k" over ``order``, against chance, over the whole release."""
    driver = baseline_driver()
    top_k = driver.PUBLIC_TOP_K if k is None else k
    recalls: list[float] = []
    rewards: list[float] = []
    chance_recalls: list[float] = []
    chance_passes: list[float] = []
    passes = 0
    records = released()
    assert records, "no released record to measure"
    for payload in records:
        record = driver._record_from_payload(payload)
        texts = bucket_merge(payload)
        pool = order(payload)
        head = pool[:top_k]
        gold = set(record.gold_ids)
        recalls.append(len(set(head) & gold) / len(gold))
        reward, ok = driver.grade("\n".join(texts[alias] for alias in head), record)
        rewards.append(reward)
        passes += int(ok)
        chance_recalls.append(top_k / len(pool))
        chance_passes.append(_chance_pass_probability(texts, record, top_k))
    return FirstKStat(
        label=label,
        k=top_k,
        n_records=len(records),
        recall=mean(recalls),
        chance_recall=mean(chance_recalls),
        reward=mean(rewards),
        passes=passes,
        chance_passes=sum(chance_passes),
    )


@lru_cache(maxsize=1)
def published_first_k() -> FirstKStat:
    return first_k_stat("candidate_pool.ids", published_order)


@lru_cache(maxsize=1)
def bucket_merge_first_k() -> FirstKStat:
    return first_k_stat("the bucket merge", lambda payload: list(bucket_merge(payload)))


# --------------------------------------------------------------------------- #
# How much of a moved cross-session claim the validator actually catches.
# --------------------------------------------------------------------------- #


class RelabelStat(NamedTuple):
    """One direction of the sweep: every single-id move, scored by the shipped rules.

    ``provenance.cross_session_gold_ids`` is the one published field that cannot be
    checked completely, so the honest number is not "it is checked" but "this much of
    it is". A promotion moves one session-local gold into the cross-session half; a
    demotion moves one cross-session gold out of it. Each is applied to a copy of one
    released record, the rest of the release left alone, and handed to the validator.
    """

    direction: str
    n_tried: int
    n_record_level: int
    n_scope_level: int
    misses: tuple[tuple[str, str], ...]

    @property
    def n_caught(self) -> int:
        return self.n_record_level + self.n_scope_level

    @property
    def rate(self) -> float:
        return self.n_caught / self.n_tried

    def __str__(self) -> str:
        return (
            f"{self.direction}: {self.n_caught}/{self.n_tried} caught ({self.rate:.4f}) - "
            f"{self.n_record_level} by a record-level rule, {self.n_scope_level} by the "
            f"scope layer, {len(self.misses)} not caught"
        )


def _relabelled(record: Mapping[str, Any], cross_ids: Iterable[str]) -> dict[str, Any]:
    """``record`` with a different cross-session set and nothing else changed."""
    provenance = {**record["provenance"], "cross_session_gold_ids": sorted(cross_ids)}
    return {**record, "provenance": provenance}


@lru_cache(maxsize=1)
def relabel_coverage() -> dict[str, RelabelStat]:
    """Both directions of the one-id relabelling sweep over the whole release.

    Only project records are swept: the session half carries an empty set, where a
    promotion is caught outright by the tier rule and a demotion has nothing to move.
    So this measures the hard half, and the rate is not diluted by 80 free catches.
    """
    validator = validator_module()
    records = released()
    base_claims = [validator.scope_claims(record) for record in records]
    assert not validator.scope_problems(
        base_claims
    ), "the pristine release already contradicts itself across records"

    stats: dict[str, RelabelStat] = {}
    for direction in ("promotion", "demotion"):
        tried = 0
        record_level = 0
        scope_level = 0
        misses: list[tuple[str, str]] = []
        for index, record in enumerate(records):
            if record["tier"] != "project":
                continue
            cross = set(record["provenance"]["cross_session_gold_ids"])
            moved = (
                [gold for gold in record["evidence"]["gold_ids"] if gold not in cross]
                if direction == "promotion"
                else sorted(cross)
            )
            for memory_id in moved:
                tried += 1
                tampered = _relabelled(
                    record,
                    cross | {memory_id} if direction == "promotion" else cross - {memory_id},
                )
                if validator._cross_session_problems(tampered) + validator._identity_problems(
                    tampered
                ):
                    record_level += 1
                    continue
                claims = list(base_claims)
                claims[index] = validator.scope_claims(tampered)
                if validator.scope_problems(claims):
                    scope_level += 1
                else:
                    misses.append((str(record["record_id"]), memory_id))
        stats[direction] = RelabelStat(
            direction=direction,
            n_tried=tried,
            n_record_level=record_level,
            n_scope_level=scope_level,
            misses=tuple(misses),
        )
    return stats


# --------------------------------------------------------------------------- #
# What the memory-necessity gate decided, and by how much.
# --------------------------------------------------------------------------- #


class NecessityStat(NamedTuple):
    """The sweep's headline, plus the margin the headline does not show.

    ``rejection_rate`` on its own reads like a threshold that examined every candidate.
    The delta spread is what says whether it did: a corpus whose deltas all sit at the
    same value never put the threshold to a decision, whatever the rate says.
    """

    n_candidates: int
    n_accepted: int
    n_rejected: int
    rejection_rate: float
    epsilon: float
    reference_agent: str
    delta_min: float
    delta_median: float
    delta_max: float
    oracle_min: float
    oracle_max: float
    no_memory_min: float
    no_memory_max: float

    @property
    def margin(self) -> float:
        """How far the closest candidate sat from the threshold that judged it."""
        return self.delta_min - self.epsilon

    @property
    def is_degenerate(self) -> bool:
        """True when every candidate scored the same delta, so no ordering was tested."""
        return self.delta_min == self.delta_max

    def __str__(self) -> str:
        return (
            f"necessity: {self.n_accepted}/{self.n_candidates} admitted "
            f"(rejection_rate {self.rejection_rate:.4f}, agent {self.reference_agent}); "
            f"delta min {self.delta_min:.4f}, median {self.delta_median:.4f}, max "
            f"{self.delta_max:.4f} against epsilon {self.epsilon:.4f}, margin "
            f"{self.margin:+.4f}"
        )


@lru_cache(maxsize=1)
def necessity_summary() -> NecessityStat:
    """The gate's own artifact for the frozen corpus, summarised.

    Read from ``necessity.json`` rather than re-run: that file is what the gate
    actually decided on the corpus the release was cut from, and re-running the sweep
    here would report on whatever the generator does today instead.
    """
    artifact = json.loads(NECESSITY.read_text(encoding="utf-8"))
    rows = artifact["per_sequence"]
    assert rows, f"{NECESSITY} records no decision"
    assert (
        len(rows) == artifact["n_candidates"]
    ), f"{NECESSITY} claims {artifact['n_candidates']} candidates and lists {len(rows)}"
    deltas = sorted(float(row["delta"]) for row in rows)
    oracle = sorted(float(row["oracle_reward"]) for row in rows)
    no_memory = sorted(float(row["no_memory_reward"]) for row in rows)
    return NecessityStat(
        n_candidates=int(artifact["n_candidates"]),
        n_accepted=int(artifact["n_accepted"]),
        n_rejected=int(artifact["n_rejected"]),
        rejection_rate=float(artifact["rejection_rate"]),
        epsilon=float(artifact["epsilon"]),
        reference_agent=str(artifact["reference_agent"]),
        delta_min=deltas[0],
        delta_median=median(deltas),
        delta_max=deltas[-1],
        oracle_min=oracle[0],
        oracle_max=oracle[-1],
        no_memory_min=no_memory[0],
        no_memory_max=no_memory[-1],
    )


def _necessity_margin_sentence(stat: NecessityStat) -> str:
    """How the margin reads in prose, which depends on whether there is a spread.

    A degenerate corpus and a spread one are different claims, so they get different
    sentences: writing "runs from 1.0000 to 1.0000" would bury the very thing the
    margin exists to surface. The branch is on exact equality of two measured numbers,
    not on a tolerance anybody chose.
    """
    if stat.is_degenerate:
        return (
            f"all {stat.n_candidates} candidates sit at delta exactly "
            f"{stat.delta_min:.4f} (oracle {stat.oracle_min:.4f} against no-memory "
            f"{stat.no_memory_min:.4f}) with epsilon {stat.epsilon:.4f}, so the closest "
            f"candidate clears the threshold by {stat.margin:.4f}"
        )
    return (
        f"the {stat.n_candidates} candidates' delta runs from {stat.delta_min:.4f} to "
        f"{stat.delta_max:.4f} (median {stat.delta_median:.4f}) with epsilon "
        f"{stat.epsilon:.4f}, so the closest candidate clears the threshold by "
        f"{stat.margin:.4f}"
    )


# --------------------------------------------------------------------------- #
# The renderings the shipped prose quotes verbatim.
# --------------------------------------------------------------------------- #


class Claim(NamedTuple):
    """One rendered figure and every shipped file that has to state it."""

    name: str
    text: str
    files: tuple[Path, ...]


README = PUBLIC_ROOT / "README.md"
SCHEMA = PUBLIC_ROOT / "schema" / "bench-record.v3.schema.json"
VALIDATOR = PUBLIC_ROOT / "validator" / "membench_validate.py"
EXPORTER = REPO_ROOT / "membench" / "public_export.py"
BASELINE = BASELINE_SCRIPT

# Every file that derives the pool order or tells a downloader what it is evidence of.
# The exporter draws the order, the validator refuses a record that departs from it, the
# schema is the contract text, the README is the instruction, and the baseline driver is
# the worked example — each one states the measurement, so each one can go stale.
_POOL_ORDER_FILES = (SCHEMA, VALIDATOR, EXPORTER, BASELINE)


def claims() -> tuple[Claim, ...]:
    """Every published figure that is a measurement, rendered exactly as it ships."""
    replaced = ascending_alias_ranks()
    published = published_ranks()
    policy = published_first_k()
    return (
        Claim(
            "ascending-alias first entry is gold",
            f"{replaced['corpus'].first_is_gold} records of "
            f"{replaced['corpus'].n_records} ({replaced['corpus'].first_rate:.4f}) "
            f"against a per-record chance of {replaced['corpus'].chance_rate:.4f}, "
            f"p = {replaced['corpus'].p_first_upper:.4f} over "
            f"{PERMUTATION_DRAWS:,} permutation draws",
            (README, *_POOL_ORDER_FILES),
        ),
        Claim(
            "ascending-alias first entry is gold, per tier",
            f"per tier that is p = {replaced['session'].p_first_upper:.4f} in the "
            f"session half and p = {replaced['project'].p_first_upper:.4f} in the "
            "project half",
            (README,),
        ),
        Claim(
            "published first entry is gold",
            f"{published['corpus'].first_is_gold} in {published['corpus'].n_records} "
            f"({published['corpus'].first_rate:.4f}) at p = "
            f"{published['corpus'].p_first_upper:.4f}",
            (README, *_POOL_ORDER_FILES),
        ),
        Claim(
            "published first entry is gold, per tier",
            f"per tier that is p = {published['session'].p_first_upper:.4f} in the "
            f"session half and p = {published['project'].p_first_upper:.4f} in the "
            "project half",
            (README,),
        ),
        Claim(
            "published mean normalized gold rank",
            f"Across all {published['corpus'].n_gold} published gold entries, the "
            "mean normalized gold rank under the published order is "
            f"{published['corpus'].mean_rank:.4f} against a uniform 0.5000, at p = "
            f"{published['corpus'].p_rank_two:.4f}",
            (README,),
        ),
        Claim(
            "first-k policy over the published order",
            f"recalls {policy.recall:.3f} against a chance {policy.chance_recall:.3f}, "
            f"earns reward {policy.reward:.3f}, and passes {policy.passes} of "
            f"{policy.n_records} against a chance of {policy.chance_passes:.2f}",
            (README,),
        ),
        Claim(
            "cross-session relabelling coverage",
            f"caught on {relabel_coverage()['promotion'].n_caught} of the "
            f"{relabel_coverage()['promotion'].n_tried} promotions available in this "
            f"release ({relabel_coverage()['promotion'].rate:.4f}) and "
            f"{relabel_coverage()['demotion'].n_caught} of the "
            f"{relabel_coverage()['demotion'].n_tried} demotions "
            f"({relabel_coverage()['demotion'].rate:.4f})",
            (README, VALIDATOR),
        ),
        Claim(
            "necessity margin",
            _necessity_margin_sentence(necessity_summary()),
            (README,),
        ),
    )


def render() -> str:
    """Every figure, for an operator rewriting the prose by hand."""
    lines = [str(ascending_alias_ranks()[group]) for group in sorted(ascending_alias_ranks())] + [
        str(published_ranks()[group]) for group in sorted(published_ranks())
    ]
    lines.append(str(bucket_merge_first_k()))
    lines.append(str(published_first_k()))
    lines.extend(str(relabel_coverage()[direction]) for direction in ("promotion", "demotion"))
    lines.append(str(necessity_summary()))
    lines.extend(f"{claim.name}: {claim.text}" for claim in claims())
    return "\n".join(lines)


if __name__ == "__main__":
    print(render())
