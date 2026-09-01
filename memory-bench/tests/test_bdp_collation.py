"""Gates for the BDP collation family (mem-w0q6q).

The seven generated graph shapes cannot ask which comparison rule an authority
uses. Their ids are ASCII, lowercase and fixed-width, so numeric,
case-insensitive, percent-decoding and codepoint readings of "ascending
canonical URI" all return the same sequence there. `collation-edge-identifiers`
is authored so those readings diverge, and this module is what keeps it
diverging.

The load-bearing gate is `test_the_reference_order_separates_the_identifier_
pairs_by_hand`. It states facts about four specific identifiers and the
codepoints that decide between them, rather than re-running the emitter's own
comparison rules, so it still fails if those rules were wrong from the start.
Everything else here catches a fixture that drifted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from membench.bdp_fixtures.collation import (
    COLLATION_FAMILY,
    COLLATION_GROUPS,
    COLLATION_LIMITS,
    COLLATION_ORDER_ID,
    RIVALS,
    RIVALS_BY_NAME,
    CollationError,
    CollationGroup,
    RivalComparator,
    _defeated_rivals,
    _tie_groups,
    collation_edges,
    validate_groups,
)
from membench.bdp_fixtures.mapping import (
    BEAD_TYPE_ID,
    CITES_TYPE_ID,
    PUBLISHED_PROPERTY_KEYS,
    ScopeUrls,
    bead_properties,
)
from tests.bdp_support import FIXTURES, load_bundle
from tests.bdp_support import chunks as _chunks
from tests.bdp_support import families as _families
from tests.bdp_support import local as _local
from tests.bdp_support import manifest as _manifest
from tests.bdp_support import read as _read
from tests.bdp_support import readme as _readme
from tests.bdp_support import validator as _validator


@pytest.fixture(scope="module")
def bundle() -> dict[str, Any]:
    return load_bundle()


def _collation(*parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(COLLATION_FAMILY, *parts).read_text(encoding="utf-8"))


def _collation_manifest() -> dict[str, Any]:
    entry: dict[str, Any] = _manifest()["collation_family"][COLLATION_FAMILY]
    return entry


def _collation_ids(collection: str) -> list[str]:
    return [str(record["id"]) for record in _collation("dataset", collection)["items"]]


def _collation_scope() -> ScopeUrls:
    return ScopeUrls.for_family(COLLATION_FAMILY)


def test_the_collation_family_is_recorded_apart_from_the_seven_graph_shapes() -> None:
    """It ships in the same tree but not in the density table.

    `families` is what every density figure in the manifest and the README
    quantifies over. A family authored around identifier spellings has no hub, no
    fan-in and no degree distribution worth comparing, and folding it in would
    make those figures answer a question nobody asked.
    """

    manifest = _manifest()
    assert COLLATION_FAMILY not in manifest["families"]
    assert sorted(manifest["families"]) == _families()
    assert manifest["family_count"] == len(_families())
    assert sorted(manifest["collation_family"]) == [COLLATION_FAMILY]
    assert (FIXTURES / COLLATION_FAMILY / "ordering.json").is_file()


def test_collation_documents_validate_against_the_pinned_bundle(bundle: dict[str, Any]) -> None:
    for collection, definition in (
        ("beads.json", "beadCollection"),
        ("links.json", "linkCollection"),
        ("types.json", "typesInventory"),
    ):
        _validator(bundle, definition).validate(_collation("dataset", collection))
    _validator(bundle, "readDiscovery").validate(_collation("discovery.json"))


def test_the_collation_family_shares_the_types_of_the_seven() -> None:
    """A consumer loads the whole tree in one run, so the Types have to be the same two."""

    assert sorted(_collation_ids("types.json")) == sorted([BEAD_TYPE_ID, CITES_TYPE_ID])
    for record in _collation("dataset", "beads.json")["items"]:
        assert record["type"] == BEAD_TYPE_ID
    for record in _collation("dataset", "links.json")["items"]:
        assert record["type"] == CITES_TYPE_ID


def test_every_collation_identifier_is_ascii() -> None:
    """Non-ASCII belongs inside percent-encoding, which is where a URI may carry it.

    A raw non-ASCII id is an IRI, not a URI, so it would ship invalid against
    `format: uri` while passing a validator run without a format checker. It
    would also make the reference order depend on whether the comparison is over
    codepoints or over UTF-8 bytes, which is an axis this family does not claim.
    """

    for collection in ("beads.json", "links.json", "types.json"):
        for identifier in _collation_ids(collection):
            assert identifier.isascii(), identifier
    assert any("%C3%A9" in identifier for identifier in _collation_ids("beads.json"))


def test_the_recorded_collation_order_is_the_codepoint_order_of_what_shipped() -> None:
    ordering = _collation("ordering.json")
    assert ordering["reference_order"] == COLLATION_ORDER_ID
    assert ordering["reference_order"] != _read(_families()[0], "ordering.json")["reference_order"]
    for collection in ("beads/", "links/", "types/"):
        shipped = _collation_ids(collection.rstrip("/") + ".json")
        recorded = ordering["collections"][collection]["selected_set"]
        assert recorded == sorted(recorded), collection
        assert sorted(shipped) == recorded, collection


def test_the_shipped_collation_arrays_are_not_in_reference_order() -> None:
    serialized = _collation_manifest()["serialized_in_reference_order"]
    for collection in ("beads.json", "links.json"):
        shipped = _collation_ids(collection)
        assert shipped != sorted(shipped), collection
        assert serialized[collection] is False, collection


def test_the_reference_order_separates_the_identifier_pairs_by_hand() -> None:
    """The evidence, written as facts about specific ids rather than re-derived.

    Every other gate below runs the same comparison rules the emitter runs, so
    together they catch a fixture that drifted and nothing else. These four pairs
    are the axes spelled out: each names two ids, the codepoints that decide
    between them, and the rule that would swap or tie them.
    """

    beads = _collation("ordering.json")["collections"]["beads/"]["selected_set"]
    position = {_local(identifier): index for index, identifier in enumerate(beads)}

    # Unpadded, so "1" is a prefix of "10" and codepoint order puts `10` before
    # `2`. Numeric-aware comparison is the rule that reverses this.
    assert position["10"] < position["2"]
    # ASCII case: `Z` is 0x5A and `a` is 0x61, so every uppercase letter precedes
    # every lowercase one. Case folding reverses exactly this pair.
    assert position["Zeta"] < position["alpha"]
    # `-` is 0x2D and `_` is 0x5F, so the separator decides. A collator that
    # weights punctuation as ignorable compares `alphatwo` against `alphaone`
    # and reverses them.
    assert position["alpha-two"] < position["alpha_one"]
    # `%` is 0x25 and `e` is 0x65, so the NFC spelling `caf%C3%A9` precedes the
    # unaccented `cafe`, which precedes the NFD spelling `cafe%CC%81`.
    # Percent-decoding reorders all three; normalizing to NFC ties two of them.
    assert position["caf%C3%A9"] < position["cafe"] < position["cafe%CC%81"]


@pytest.mark.parametrize("rival", RIVALS, ids=lambda rival: rival.name)
@pytest.mark.parametrize("collection", ["beads.json", "links.json"])
def test_every_rival_comparison_rule_disagrees_with_the_shipped_order(
    collection: str, rival: RivalComparator
) -> None:
    """The gate this family exists for (mem-w0q6q).

    A comparison rule that agrees with the recorded order over these ids is a
    rule this tree cannot detect, and an authority implementing it would pass
    while ordering by something else. Both id spaces have to separate every rule:
    Link ids are zero-padded ordinals in the seven families, which is the padding
    this family exists to do without, so inheriting the claim from the Bead ids
    would leave that half untested.
    """

    reference = sorted(_collation_ids(collection))
    reordered = sorted(reference, key=rival.key)
    ties = _tie_groups(reference, rival)
    assert reordered != reference or ties, rival.name


@pytest.mark.parametrize("collection", ["beads/", "links/"])
def test_case_folding_and_normalization_tie_ids_the_recorded_order_separates(
    collection: str,
) -> None:
    """A rule that ties two distinct ids is not a total order at all.

    Recorded apart from the rules that merely return a different order, because
    the two findings differ in kind. A different total order is conformant under
    bdp#8 as proposed, which leaves the choice to the implementation; a tie fails
    the first clause whatever order the authority documents.
    """

    report = {
        entry["comparison"]: entry
        for entry in _collation("ordering.json")["collation"]["comparisons"][collection]
    }
    assert sorted(report) == sorted(rival.name for rival in RIVALS)
    for name in ("casefold", "punctuation-ignoring", "nfc-normalizing"):
        entry = report[name]
        assert entry["is_a_total_order_over_this_collection"] is False, name
        assert entry["ties"], name
        assert "not a total order" in entry["bdp8_reading"], name
    for name in ("numeric-aware", "percent-decoding"):
        entry = report[name]
        assert entry["is_a_total_order_over_this_collection"] is True, name
        assert entry["ties"] == [], name
        assert entry["differs_from_the_reference_order"] is True, name
        assert entry["sequence"] != sorted(entry["sequence"]), name


def test_a_tied_rule_records_no_sequence_because_the_sequence_would_be_an_artifact() -> None:
    """Under a tie, what a sort returns depends on the order the items went in.

    Recording it would publish this emitter's input order as though it were the
    rule's answer, the same class of mistake as shipping the load set in the
    reference order.
    """

    for entries in _collation("ordering.json")["collation"]["comparisons"].values():
        for entry in entries:
            assert ("sequence" in entry) is entry["is_a_total_order_over_this_collection"]


def test_the_manifest_records_which_rules_are_not_a_total_order() -> None:
    entry = _collation_manifest()
    considered = [rival.name for rival in RIVALS]
    assert entry["reference_order"] == COLLATION_ORDER_ID
    assert entry["comparison_rules_considered"] == considered
    assert entry["page_limits"] == list(COLLATION_LIMITS)
    for collection in ("beads/", "links/"):
        assert sorted(entry["comparison_rules_separated"][collection]) == sorted(considered)
        assert entry["comparison_rules_that_are_not_a_total_order"][collection] == [
            "casefold",
            "nfc-normalizing",
            "punctuation-ignoring",
        ], collection


def test_each_group_separates_exactly_the_rules_it_declares() -> None:
    scope = _collation_scope()
    recorded = {
        group["name"]: set(group["defeats"])
        for group in _collation("ordering.json")["collation"]["groups"]
    }
    for group in COLLATION_GROUPS:
        urls = [scope.bead(local_id) for local_id in group.bead_ids]
        assert set(_defeated_rivals(urls)) == set(group.defeats), group.name
        assert recorded[group.name] == set(group.defeats), group.name
    covered = {name for group in COLLATION_GROUPS for name in group.defeats}
    assert covered == {rival.name for rival in RIVALS}


def test_a_group_that_no_longer_separates_its_rule_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Padded, lowercase, punctuation-free ASCII ids separate nothing.

    They are also exactly what the seven graph families ship, so this is the
    drift the family is one careless edit away from. A family that drifted back
    would still emit a tree full of confident prose about collation.
    """

    flattened = CollationGroup(
        name="unpadded-ordinals",
        exercises="nothing at all, now that the ids are padded again",
        defeats=(),
        bead_ids=("001", "002", "010"),
        link_ids=("001", "002", "010"),
    )
    monkeypatch.setattr(
        "membench.bdp_fixtures.collation.COLLATION_GROUPS",
        (flattened, *COLLATION_GROUPS[1:]),
    )
    with pytest.raises(CollationError, match="separates no comparison rule"):
        validate_groups(_collation_scope())


def test_a_group_declaring_a_rule_its_ids_do_not_separate_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overclaimed = CollationGroup(
        name=COLLATION_GROUPS[0].name,
        exercises=COLLATION_GROUPS[0].exercises,
        defeats=(*COLLATION_GROUPS[0].defeats, "casefold"),
        bead_ids=COLLATION_GROUPS[0].bead_ids,
        link_ids=COLLATION_GROUPS[0].link_ids,
    )
    monkeypatch.setattr(
        "membench.bdp_fixtures.collation.COLLATION_GROUPS",
        (overclaimed, *COLLATION_GROUPS[1:]),
    )
    with pytest.raises(CollationError, match="the declaration and the identifiers disagree"):
        validate_groups(_collation_scope())


def test_a_declaration_naming_an_unknown_comparison_rule_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invented = CollationGroup(
        name=COLLATION_GROUPS[0].name,
        exercises=COLLATION_GROUPS[0].exercises,
        defeats=("icu-root-locale",),
        bead_ids=COLLATION_GROUPS[0].bead_ids,
        link_ids=COLLATION_GROUPS[0].link_ids,
    )
    monkeypatch.setattr(
        "membench.bdp_fixtures.collation.COLLATION_GROUPS",
        (invented, *COLLATION_GROUPS[1:]),
    )
    with pytest.raises(CollationError, match="unknown comparison rules"):
        validate_groups(_collation_scope())


def test_a_rule_no_group_covers_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping the group that carries an axis must not quietly drop the axis."""

    monkeypatch.setattr(
        "membench.bdp_fixtures.collation.COLLATION_GROUPS",
        tuple(group for group in COLLATION_GROUPS if group.name != "normalization"),
    )
    with pytest.raises(CollationError, match="no group separates"):
        validate_groups(_collation_scope())


def test_a_repeated_local_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "membench.bdp_fixtures.collation.COLLATION_GROUPS",
        (*COLLATION_GROUPS, COLLATION_GROUPS[0]),
    )
    with pytest.raises(CollationError, match="local ids repeat"):
        validate_groups(_collation_scope())


def test_the_link_id_space_carries_the_axes_rather_than_inheriting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Bead ids passing is not evidence about Links, so the check is separate.

    Padding the Link ids back to a fixed width leaves the Bead ids untouched and
    every group still declaring truthfully. Only the Link-space check catches it.
    """

    ordinals = iter(range(1, 100))
    padded = tuple(
        CollationGroup(
            name=group.name,
            exercises=group.exercises,
            defeats=group.defeats,
            bead_ids=group.bead_ids,
            link_ids=tuple(f"{next(ordinals):04d}" for _ in group.link_ids),
        )
        for group in COLLATION_GROUPS
    )
    monkeypatch.setattr("membench.bdp_fixtures.collation.COLLATION_GROUPS", padded)
    with pytest.raises(CollationError, match="the Link id space is untested against"):
        validate_groups(_collation_scope())


def test_collation_page_partition_follows_from_the_selected_set() -> None:
    ordering = _collation("ordering.json")
    expectations = list(ordering["collections"].values()) + list(ordering["selections"])
    for expectation in expectations:
        selected = expectation["selected_set"]
        assert expectation["total"] == len(selected)
        for limit, pages in expectation["pages"].items():
            chunked = _chunks(selected, int(limit)) or [[]]
            assert pages["page_count"] == len(chunked)
            assert pages["page_item_counts"] == [len(page) for page in chunked]
            assert pages["spans_multiple_pages"] is (len(chunked) > 1)


def test_the_collation_selections_match_what_their_predicates_select() -> None:
    """Both selections have to cross a page boundary at the smaller limit.

    Inside one page, an authority can sort the page and be right by accident. The
    order only binds on a continuation, which is where a tie costs a record.
    """

    ordering = _collation("ordering.json")
    beads = _collation("dataset", "beads.json")["items"]
    links = _collation("dataset", "links.json")["items"]
    assert len(ordering["selections"]) == 2
    for selection in ordering["selections"]:
        parameters = selection["parameters"]
        if "source" in parameters:
            expected = [x["id"] for x in links if x["source"] == parameters["source"]]
        else:
            expected = [x["id"] for x in beads if x["properties"]["provenance"] == "agent"]
        assert sorted(expected) == selection["selected_set"]
        assert selection["pages"][str(min(COLLATION_LIMITS))]["spans_multiple_pages"] is True


def test_collation_beads_carry_the_same_properties_as_every_other_family() -> None:
    for record in _collation("dataset", "beads.json")["items"]:
        assert tuple(record["properties"]) == PUBLISHED_PROPERTY_KEYS
        assert record["properties"] == bead_properties(_local(record["id"]))


def test_every_collation_link_endpoint_resolves_to_a_bead_in_the_same_scope() -> None:
    bead_ids = set(_collation_ids("beads.json"))
    for link in _collation("dataset", "links.json")["items"]:
        assert link["source"] in bead_ids
        assert link["target"] in bead_ids
        assert link["source"] != link["target"]


def test_collation_edges_refuse_a_shape_with_nothing_to_order() -> None:
    with pytest.raises(CollationError, match="not a selection worth recording"):
        collation_edges(23, 2)
    with pytest.raises(CollationError, match="cannot avoid a self-Link"):
        collation_edges(4, 14)


def test_no_rank_signal_appears_in_the_collation_family() -> None:
    """Same bar as the seven: nothing here may carry the benchmark's answer key."""

    for path in sorted((FIXTURES / COLLATION_FAMILY).rglob("*.json")):
        assert "rank" not in path.read_text(encoding="utf-8").lower(), path.name


def test_readme_states_what_the_collation_family_is_for() -> None:
    # Whitespace-normalized: the claims matter, the line wrapping does not.
    text = " ".join(_readme().split())
    entry = _collation_manifest()
    assert f"`{COLLATION_FAMILY}`" in text
    assert f"`{COLLATION_ORDER_ID}`" in text
    for claim in (
        "not one of the seven",
        "zero-padded, lowercase and ASCII",
        "a tie is not a total order",
    ):
        assert claim in text, claim
    for rival in RIVALS:
        assert f"`{rival.name}`" in text, rival.name
    assert f"{entry['bead_count']} Beads and {entry['link_count']} Links" in text


# --- the gate, run against an authority rather than argued -------------------
#
# Everything above compares sequences. The bead this family answers asks for
# something stronger: the family has to go red against an authority that sorts
# with locale collation or casefolding. So these gates build one and serve the
# recorded selections from it.
#
# The authority is a keyset paginator, which is the construction an
# implementation with a real index reaches for: the cursor is the sort key of the
# last item served, and the next page is everything strictly greater. It is also
# where a comparison rule that ties two ids loses one. Both members of a tied
# pair compare equal to the cursor, so neither is strictly greater, and a pair
# straddling a page boundary is served once and then skipped.

# What the small limit actually costs each rule, per recorded collection. A
# hand-written table rather than a computation, because the interesting property
# is placement: a tie only costs a record when it straddles a page boundary, so
# an edit that leaves every tie group sitting inside one page would keep every
# other gate here green while making the family stop biting.
KEYSET_LOSSES_AT_THE_SMALL_LIMIT: dict[tuple[str, str], list[str]] = {
    ("beads/", "casefold"): ["Gamma", "gamma"],
    ("beads/", "punctuation-ignoring"): ["Gamma", "gamma"],
    ("beads/", "nfc-normalizing"): ["cafe%CC%81", "re%CC%81sume"],
    ("links/", "casefold"): ["Edge", "edge"],
    ("links/", "punctuation-ignoring"): ["Edge", "edge"],
}


def _keyset_pages(selected: list[str], key: Callable[[str], Any], limit: int) -> list[list[str]]:
    """Serve a selected set as pages, continuing on the comparison key alone.

    Deliberately not offset pagination, which cannot lose a record whatever the
    comparison does, and deliberately not a tiebreak on the id, which is the fix
    rather than the fixture.
    """

    ordered = sorted(selected, key=key)
    pages: list[list[str]] = []
    cursor: Any = None
    while True:
        available = [
            identifier for identifier in ordered if cursor is None or key(identifier) > cursor
        ]
        if not available:
            return pages
        page = available[:limit]
        pages.append(page)
        cursor = key(page[-1])


def _recorded_sets() -> list[tuple[str, list[str]]]:
    ordering = _collation("ordering.json")
    named = [
        (name, expectation["selected_set"]) for name, expectation in ordering["collections"].items()
    ]
    named += [
        (f"?{next(iter(selection['parameters']))}=", selection["selected_set"])
        for selection in ordering["selections"]
    ]
    return named


@pytest.mark.parametrize("limit", COLLATION_LIMITS)
def test_an_authority_sorting_by_the_recorded_order_serves_every_set_intact(
    limit: int,
) -> None:
    """The control. Without it, a red gate could just mean the harness is broken."""

    for name, selected in _recorded_sets():
        served = [item for page in _keyset_pages(selected, str, limit) for item in page]
        assert served == selected, f"{name} at limit {limit}"


@pytest.mark.parametrize("rival", RIVALS, ids=lambda rival: rival.name)
@pytest.mark.parametrize("limit", COLLATION_LIMITS)
@pytest.mark.parametrize("collection", ["beads/", "links/"])
def test_an_authority_sorting_by_any_rival_rule_serves_the_wrong_sequence(
    collection: str, limit: int, rival: RivalComparator
) -> None:
    """The gate the bead asks for, on the two whole collections.

    An authority that casefolds, or runs a collator with punctuation weighted as
    ignorable, hands back a sequence that is not the one recorded, at both
    advertised limits. So does one that parses digit runs, percent-decodes, or
    normalizes to NFC.
    """

    selected = _collation("ordering.json")["collections"][collection]["selected_set"]
    served = [item for page in _keyset_pages(selected, rival.key, limit) for item in page]
    assert served != selected, f"{rival.name} on {collection} at limit {limit}"


@pytest.mark.parametrize("rival", RIVALS, ids=lambda rival: rival.name)
@pytest.mark.parametrize("collection", ["beads/", "links/"])
def test_the_small_limit_costs_exactly_the_records_the_table_names(
    collection: str, rival: RivalComparator
) -> None:
    """A tie is not just a different order: at the small limit it drops records.

    The three rules that tie lose a record here; the two that are total orders
    lose none, which is the difference `ordering.json` records under
    `is_a_total_order_over_this_collection` showing up as behaviour.
    """

    selected = _collation("ordering.json")["collections"][collection]["selected_set"]
    limit = min(COLLATION_LIMITS)
    served = [item for page in _keyset_pages(selected, rival.key, limit) for item in page]
    lost = sorted(_local(item) for item in set(selected) - set(served))
    assert lost == KEYSET_LOSSES_AT_THE_SMALL_LIMIT.get((collection, rival.name), [])
    assert len(served) == len(set(served)), "a keyset continuation must not repeat a record"


def test_every_record_a_tying_rule_loses_is_one_the_tree_names_as_tied() -> None:
    """The recorded ties are what explains the loss, so they have to cover it.

    Otherwise `ordering.json` sends a harness author looking at the wrong pair.
    """

    comparisons = _collation("ordering.json")["collation"]["comparisons"]
    for (collection, name), lost in KEYSET_LOSSES_AT_THE_SMALL_LIMIT.items():
        entry = next(x for x in comparisons[collection] if x["comparison"] == name)
        tied = {_local(item) for group in entry["ties"] for item in group}
        assert set(lost) <= tied, (collection, name)


def test_a_casefolding_authority_loses_a_record_from_a_recorded_selection() -> None:
    """Not only from a whole collection.

    A selection is the case a harness is most likely to exercise, and it is the
    one where an authority holds a smaller working set and is most tempted to
    continue on the comparison key alone.
    """

    selection = next(
        s for s in _collation("ordering.json")["selections"] if "source" in s["parameters"]
    )
    selected = selection["selected_set"]
    served = [
        item for page in _keyset_pages(selected, RIVALS_BY_NAME["casefold"].key, 3) for item in page
    ]
    assert sorted(_local(item) for item in set(selected) - set(served)) == ["Edge"]
