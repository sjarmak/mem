"""Gates for the seven generated BDP graph shapes (mem-vn4ek).

The eighth directory in the same tree, `collation-edge-identifiers`, is gated in
`test_bdp_collation.py`: it varies identifier spellings rather than graph shape,
so none of the density, degree or hub-predicate reasoning below applies to it.
Readers shared by both modules live in `tests/bdp_support.py`.

Five things have to hold or the fixtures are not usable by anyone else:
regeneration is byte-identical, every document validates against the pinned
upstream schema bundle in the shape it actually ships, the ordering expectations
describe the data that shipped rather than the data the emitter intended to
ship, each graph shape still exercises the conformance property it exists for,
and nothing in the package comes from the benchmark corpus.

That last one is structural rather than a scan. An earlier revision projected the
frozen ordering corpus and neutralized what it published; the degree distribution
of that corpus is the benchmark's answer key, and no neutralization reaches it.
The emitter now reads no corpus at all, and `test_the_package_imports_nothing_
from_the_benchmark_corpus` is what keeps it that way.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from membench.bdp_fixtures.emit import (
    DEFAULT_OUT,
    MANIFEST_SCHEMA_VERSION,
    SCHEMA_BUNDLE_SHA256,
    _duplicate_endpoint_tuples,
    _is_emitter_owned,
    emit_all,
    emit_family,
)
from membench.bdp_fixtures.mapping import (
    ARCHIVED_RATE,
    BEAD_TYPE_ID,
    CITES_TYPE_ID,
    PUBLISHED_PROPERTY_KEYS,
    TYPES_BASE,
    BdpMappingError,
    ScopeUrls,
    bead_local_id,
    bead_properties,
    bead_records,
    descriptor_filename,
    discovery_document,
    is_canonical_order,
    link_records,
    node_key,
    ordering_expectations,
    serialization_order,
    type_descriptors,
    type_summaries,
)
from membench.bdp_fixtures.topologies import (
    BEADS_PER_FAMILY,
    TOPOLOGIES,
    TOPOLOGIES_BY_NAME,
    Topology,
    TopologyError,
    build_edges,
)
from tests.bdp_support import (
    FIXTURES,
    LOCAL_PATH,
    PACKAGE,
    SCHEMA,
    SOURCE_PACKAGE,
    load_bundle,
)
from tests.bdp_support import chunks as _chunks
from tests.bdp_support import families as _families
from tests.bdp_support import local as _local
from tests.bdp_support import manifest as _manifest
from tests.bdp_support import read as _read
from tests.bdp_support import readme as _readme
from tests.bdp_support import validator as _validator

# The family whose shape exists to carry Links that share a (type, source,
# target) tuple, and the one whose shape exists to carry Beads in no Link.
REPEATED_TUPLE_FAMILY = "release-engineering-branching-playbooks"
ISOLATED_BEAD_FAMILY = "distributed-system-clustered-components"
OVERSIZED_SELECTION_FAMILY = "platform-documentation-hub-spoke"


@pytest.fixture(scope="module")
def bundle() -> dict[str, Any]:
    return load_bundle()


def _degrees(family: str) -> tuple[dict[str, int], dict[str, int]]:
    outdegree: dict[str, int] = {}
    indegree: dict[str, int] = {}
    for link in _read(family, "dataset", "links.json")["items"]:
        outdegree[link["source"]] = outdegree.get(link["source"], 0) + 1
        indegree[link["target"]] = indegree.get(link["target"], 0) + 1
    return outdegree, indegree


# --- the tree on disk -------------------------------------------------------


def test_every_topology_is_emitted() -> None:
    manifest = _manifest()
    assert sorted(manifest["families"]) == _families()
    assert manifest["family_count"] == len(TOPOLOGIES)
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION


def test_the_committed_tree_regenerates_byte_identically(tmp_path: Path) -> None:
    emit_all(out_root=tmp_path)
    committed = sorted(path.relative_to(FIXTURES) for path in FIXTURES.rglob("*.json"))
    fresh = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*.json"))
    assert committed == fresh
    for relative in committed:
        assert (tmp_path / relative).read_bytes() == (FIXTURES / relative).read_bytes(), relative


def test_manifest_checksums_match_the_files_on_disk() -> None:
    manifest = _manifest()
    for name, entry in manifest["shared_types"].items():
        raw = (FIXTURES / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], name
        assert len(raw) == entry["bytes"], name
    recorded = dict(manifest["families"])
    recorded.update(manifest["collation_family"])
    for family, family_entry in recorded.items():
        for name, entry in family_entry["files"].items():
            raw = (FIXTURES / family / name).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"], f"{family}/{name}"
            assert len(raw) == entry["bytes"], f"{family}/{name}"


def test_nothing_in_the_package_carries_a_local_filesystem_path() -> None:
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*"))
        if path.is_file() and LOCAL_PATH.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_vendored_schema_matches_its_recorded_provenance() -> None:
    provenance = json.loads((PACKAGE / "upstream/PROVENANCE.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(SCHEMA.read_bytes()).hexdigest()
    assert digest == provenance["sha256"]
    assert digest == SCHEMA_BUNDLE_SHA256
    assert _manifest()["bdp"]["schema_bundle_commit"] == provenance["upstream_commit"]
    assert provenance["upstream_commit"] in _manifest()["bdp"]["schema_bundle"]


def test_every_document_the_emitter_writes_is_one_it_would_prune() -> None:
    """Otherwise a dropped family leaves files behind and the diff stays clean."""

    unowned = [
        str(path.relative_to(FIXTURES))
        for path in sorted(FIXTURES.rglob("*.json"))
        if not _is_emitter_owned(path.relative_to(FIXTURES))
    ]
    assert unowned == []


def test_the_package_holds_nothing_the_readme_does_not_list() -> None:
    """Everything under `fixtures/bdp/` ships, so an unlisted file ships too.

    A locked preregistration for a separate study sat here for two commits. It
    was not in the layout, it carried operational detail, and the only reason it
    was noticed is that a path scan tripped on it, whose fix silently invalidated
    that study's recorded digest. Membership is a gate now rather than a habit.
    """

    allowed = {
        Path("README.md"),
        Path("upstream/bdp-v0.schema.json"),
        Path("upstream/PROVENANCE.json"),
    }
    unlisted = []
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PACKAGE)
        if relative in allowed:
            continue
        if relative.parts[0] == DEFAULT_OUT.name and _is_emitter_owned(
            relative.relative_to(DEFAULT_OUT.name)
        ):
            continue
        unlisted.append(str(relative))
    assert unlisted == []


def test_prune_removes_a_stale_document_and_spares_a_colliding_basename(tmp_path: Path) -> None:
    """Ownership is decided on the whole path, so a shared basename is not enough.

    Every foreign file here is a `.json` at a basename the emitter does use, which
    is the case a basename-matching prune destroys. A foreign file with an
    extension we never write (`NOTES.md`) would survive even a prune that owned
    every path, so it tests nothing.
    """

    emit_all(out_root=tmp_path)
    stale = tmp_path / "retired-family" / "ordering.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")

    spared = {
        # A dataset basename at the root, where only manifest.json is ours.
        tmp_path / "links.json": "root",
        # Under types/, but not one of the descriptor filenames.
        tmp_path / "types" / "notes.json": "types",
        # A dataset basename one level deeper than dataset/ ever goes.
        tmp_path / OVERSIZED_SELECTION_FAMILY / "dataset" / "nested" / "beads.json": "deep",
        # A manifest at depth, which the basename-matching bug deleted.
        tmp_path / "vendor" / "manifest.json": "vendor",
    }
    for path, marker in spared.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"foreign": marker}), encoding="utf-8")

    emit_all(out_root=tmp_path)

    assert not stale.exists()
    assert not stale.parent.exists()
    for path, marker in spared.items():
        assert path.exists(), path
        assert json.loads(path.read_text(encoding="utf-8")) == {"foreign": marker}


# --- schema conformance -----------------------------------------------------


@pytest.mark.parametrize("family", _families())
def test_shipped_collections_validate_against_the_pinned_bundle(
    family: str, bundle: dict[str, Any]
) -> None:
    for name, definition in (
        ("beads.json", "beadCollection"),
        ("links.json", "linkCollection"),
        ("types.json", "typesInventory"),
    ):
        document = _read(family, "dataset", name)
        # The document as shipped, `next` included. Reshaping it here, or
        # substituting a literal for a field the file actually carries, would
        # validate something no consumer ever receives.
        _validator(bundle, definition).validate(document)
        assert document["next"] is None


@pytest.mark.parametrize("family", _families())
def test_shipped_discovery_validates(family: str, bundle: dict[str, Any]) -> None:
    document = _read(family, "discovery.json")
    _validator(bundle, "readDiscovery").validate(document)
    scope = ScopeUrls.for_family(family)
    assert document["scope"] == scope.scope_url
    assert document["types"] == scope.types_collection


def test_shared_type_descriptor_documents_validate(bundle: dict[str, Any]) -> None:
    validator = _validator(bundle, "typeDescriptor")
    served = set()
    for name, entry in _manifest()["shared_types"].items():
        document = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        validator.validate(document)
        assert document["id"] == entry["serves"]
        assert Path(name).name == descriptor_filename(document["id"])
        served.add(document["id"])
    assert served == {BEAD_TYPE_ID, CITES_TYPE_ID}


@pytest.mark.parametrize("family", _families())
def test_the_inventory_lists_summaries_and_the_descriptors_live_elsewhere(family: str) -> None:
    items = _read(family, "dataset", "types.json")["items"]
    assert all(set(item) == {"id", "name", "describes"} for item in items)
    assert {item["id"] for item in items} == {BEAD_TYPE_ID, CITES_TYPE_ID}
    for item in items:
        assert (FIXTURES / "types" / descriptor_filename(item["id"])).exists()


def test_a_full_descriptor_in_the_inventory_is_rejected(bundle: dict[str, Any]) -> None:
    """The defect this shape replaced: `typeSummary` closes additionalProperties."""

    document = {"items": list(type_descriptors()), "next": None}
    with pytest.raises(ValidationError):
        _validator(bundle, "typesInventory").validate(document)


def test_the_validators_reject_broken_documents(bundle: dict[str, Any]) -> None:
    bead = json.loads(json.dumps(_read(_families()[0], "dataset", "beads.json")))
    del bead["items"][0]["revision"]
    with pytest.raises(ValidationError):
        _validator(bundle, "beadCollection").validate(bead)

    discovery = discovery_document(ScopeUrls.for_family(_families()[0]))
    discovery["extra"] = "no"
    with pytest.raises(ValidationError):
        _validator(bundle, "readDiscovery").validate(discovery)

    descriptor = dict(type_descriptors()[0])
    descriptor["describes"] = "neither"
    with pytest.raises(ValidationError):
        _validator(bundle, "typeDescriptor").validate(descriptor)


def test_a_bead_descriptor_may_not_carry_endpoint_constraints(bundle: dict[str, Any]) -> None:
    descriptor = next(
        dict(candidate) for candidate in type_descriptors() if candidate["describes"] == "bead"
    )
    descriptor["source"] = {"conformsTo": []}
    with pytest.raises(ValidationError):
        _validator(bundle, "typeDescriptor").validate(descriptor)


# --- nothing here came from the benchmark corpus ----------------------------


def test_the_package_imports_nothing_from_the_benchmark_corpus() -> None:
    """The structural version of the leak gate.

    Neutralizing published values cannot close a channel that lives in the
    degree distribution, so the emitter reads no corpus rather than reading one
    carefully. A single import back into `membench.beads_ordering` would restore
    the premise this whole rewrite removed.
    """

    offenders = [
        str(path.relative_to(SOURCE_PACKAGE))
        for path in sorted(SOURCE_PACKAGE.rglob("*.py"))
        if re.search(r"^\s*(from|import)\s+membench\.beads_ordering", path.read_text("utf-8"), re.M)
    ]
    assert offenders == []


@pytest.mark.parametrize("family", _families())
def test_every_published_property_is_reproducible_from_the_bead_id_alone(family: str) -> None:
    """No published value can carry information the Bead id does not already carry.

    Recomputing from the id and comparing is stronger than checking that known
    source strings are absent: it leaves no room for a value that came from
    anywhere else, whatever it happens to spell.
    """

    for record in _read(family, "dataset", "beads.json")["items"]:
        assert record["properties"] == bead_properties(_local(record["id"]))


@pytest.mark.parametrize("family", _families())
def test_published_properties_are_exactly_the_allowlist(family: str) -> None:
    for record in _read(family, "dataset", "beads.json")["items"]:
        assert tuple(record["properties"]) == PUBLISHED_PROPERTY_KEYS


def test_no_rank_signal_appears_anywhere_in_the_tree() -> None:
    """Broader than a snake_case substring: the leak may be spelled either way.

    `navigation_rank` reaches JSON as `navigationRank`, so a guard matching only
    the source spelling would pass a tree that publishes it.
    """

    pattern = re.compile(r"rank", re.IGNORECASE)
    offenders = [
        str(path.relative_to(FIXTURES))
        for path in sorted(FIXTURES.rglob("*.json"))
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_a_property_outside_the_allowlist_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "membench.bdp_fixtures.mapping.PUBLISHED_PROPERTY_KEYS", ("title", "aliases")
    )
    with pytest.raises(BdpMappingError, match="outside the allowlist"):
        bead_records(_families()[0], ScopeUrls.for_family(_families()[0]), 4)


@pytest.mark.parametrize("family", _families())
def test_the_narrow_selector_predicate_stays_narrow(family: str) -> None:
    """`lifecycle == archived` is the small selected set, at a published rate.

    A rate that drifted to zero would ship a fixture whose narrow Selector case
    is the empty selection twice over, and one that drifted wide would stop being
    the narrow case at all.
    """

    beads = _read(family, "dataset", "beads.json")["items"]
    archived = [x for x in beads if x["properties"]["lifecycle"] == "archived"]
    assert 0 < len(archived) < len(beads) // (ARCHIVED_RATE // 4)


# --- the generated shapes still exercise what they exist for ----------------


def test_every_family_declares_a_distinct_conformance_property() -> None:
    exercises = [topology.exercises for topology in TOPOLOGIES]
    assert len(set(exercises)) == len(exercises)
    manifest = _manifest()
    assert manifest["generator"]["exercises"] == {
        topology.name: topology.exercises for topology in TOPOLOGIES
    }
    for family, entry in manifest["families"].items():
        assert entry["exercises"] == TOPOLOGIES_BY_NAME[family].exercises


@pytest.mark.parametrize("family", _families())
def test_build_edges_is_a_pure_function_of_the_family_and_the_count(family: str) -> None:
    assert build_edges(family, BEADS_PER_FAMILY) == build_edges(family, BEADS_PER_FAMILY)


def test_build_edges_refuses_an_unknown_topology() -> None:
    with pytest.raises(TopologyError, match="unknown topology"):
        build_edges("not-a-topology", 10)


def test_build_edges_refuses_an_out_of_range_endpoint() -> None:
    bad = Topology("bad-bounds", "an endpoint past the end", lambda count: ((0, count),))
    with pytest.raises(TopologyError, match="outside"):
        _assert_contract(bad)


def test_build_edges_refuses_a_self_link() -> None:
    bad = Topology("bad-self", "a Link from a Bead to itself", lambda count: ((3, 3),))
    with pytest.raises(TopologyError, match="self"):
        _assert_contract(bad)


def test_repeated_endpoint_tuples_are_refused_unless_declared() -> None:
    undeclared = Topology("bad-repeat", "undeclared repeats", lambda count: ((0, 1), (0, 1)))
    with pytest.raises(TopologyError, match="repeat"):
        _assert_contract(undeclared)


def test_a_family_declaring_repeats_that_generates_none_is_refused() -> None:
    """The direction that would otherwise fail silently.

    If the branching builder stops emitting its repeats, the only family covering
    the shared-tuple rule quietly stops covering it while every other gate stays
    green. Declaring the property has to oblige the shape to have it.
    """

    hollow = Topology(
        "hollow-repeat",
        "declares repeats it does not generate",
        lambda count: ((0, 1), (1, 2)),
        allows_repeated_endpoint_tuples=True,
    )
    with pytest.raises(TopologyError, match="would not cover it"):
        _assert_contract(hollow)


def _assert_contract(topology: Topology, count: int = 10) -> None:
    """Run `build_edges`'s contract over a topology that is not in the table."""

    TOPOLOGIES_BY_NAME[topology.name] = topology
    try:
        build_edges(topology.name, count)
    finally:
        del TOPOLOGIES_BY_NAME[topology.name]


def test_the_branching_family_ships_links_that_share_an_endpoint_tuple() -> None:
    """BDP permits several Links over one (type, source, target) and no fixture had any.

    A consumer that keys Links on their endpoints collapses these and is
    non-conformant. It can only be caught by data that has them, so the count is
    asserted against the shipped Links rather than against the manifest that
    reports it.
    """

    links = _read(REPEATED_TUPLE_FAMILY, "dataset", "links.json")["items"]
    tuples: dict[tuple[str, str, str], list[str]] = {}
    for link in links:
        tuples.setdefault((link["type"], link["source"], link["target"]), []).append(link["id"])
    shared = {key: ids for key, ids in tuples.items() if len(ids) > 1}
    assert shared, "the only family covering the shared-tuple rule stopped covering it"
    assert sum(len(ids) - 1 for ids in shared.values()) == _duplicate_endpoint_tuples(links)
    for ids in shared.values():
        assert len(set(ids)) == len(ids), "Links sharing endpoints still need distinct ids"

    counted = _manifest()["families"][REPEATED_TUPLE_FAMILY]["duplicate_endpoint_tuple_links"]
    assert counted == _duplicate_endpoint_tuples(links)
    for family in _families():
        if family == REPEATED_TUPLE_FAMILY:
            continue
        others = _read(family, "dataset", "links.json")["items"]
        assert _duplicate_endpoint_tuples(others) == 0, family


def test_the_clusters_family_ships_beads_that_are_in_no_link() -> None:
    beads = {
        record["id"] for record in _read(ISOLATED_BEAD_FAMILY, "dataset", "beads.json")["items"]
    }
    links = _read(ISOLATED_BEAD_FAMILY, "dataset", "links.json")["items"]
    touched = {link["source"] for link in links} | {link["target"] for link in links}
    assert touched < beads
    assert len(beads - touched) > 0


def test_the_hub_family_ships_a_selection_larger_than_the_largest_page_limit() -> None:
    """The oversized-selection case, which no projected family reached."""

    limits = _manifest()["bdp"]["page_limits"]
    ordering = _read(OVERSIZED_SELECTION_FAMILY, "ordering.json")
    outbound = next(s for s in ordering["selections"] if "source" in s["parameters"])
    assert outbound["total"] > max(limits)
    for limit in limits:
        assert outbound["pages"][str(limit)]["spans_multiple_pages"] is True


@pytest.mark.parametrize("family", _families())
def test_the_manifest_degrees_match_the_shipped_links(family: str) -> None:
    outdegree, indegree = _degrees(family)
    entry = _manifest()["families"][family]
    assert entry["max_outdegree"] == max(outdegree.values(), default=0)
    assert entry["max_indegree"] == max(indegree.values(), default=0)
    assert entry["bead_count"] == BEADS_PER_FAMILY
    assert entry["type_count"] == 2
    assert entry["link_count"] == len(_read(family, "dataset", "links.json")["items"])


def test_families_span_a_real_density_range() -> None:
    """Recomputed from the shipped Links, not read back out of the manifest."""

    peaks = sorted(max(_degrees(family)[0].values(), default=0) for family in _families())
    assert peaks[0] <= 5
    assert peaks[-1] >= 200
    assert len(set(peaks)) >= 5


# --- the shipped order discriminates ----------------------------------------


@pytest.mark.parametrize("family", _families())
def test_shipped_arrays_are_not_in_reference_order(family: str) -> None:
    """The property that makes the fixture able to fail at all.

    Shipping the records sorted would let an authority that echoes ingestion
    order reproduce every recorded sequence without implementing an order.
    """

    manifest_entry = _manifest()["families"][family]
    for name in ("beads.json", "links.json", "types.json"):
        shipped = [record["id"] for record in _read(family, "dataset", name)["items"]]
        assert len(shipped) >= 2
        assert shipped != sorted(shipped), name
        assert manifest_entry["serialized_in_reference_order"][name] is False


@pytest.mark.parametrize("family", _families())
def test_no_published_property_reproduces_the_reference_sequence(family: str) -> None:
    """An authority may not pass by documenting an order over a published property.

    The synthetic text is drawn from a small fixed pool and never interpolates
    the id, so sorting on it cannot recover the id order. If it ever could, the
    fixtures would stop discriminating an authority that orders on content from
    one that orders on the id.
    """

    records = _read(family, "dataset", "beads.json")["items"]
    reference = sorted(record["id"] for record in records)
    for key in ("title", "body"):
        by_property = [
            record["id"] for record in sorted(records, key=lambda r: str(r["properties"][key]))
        ]
        assert by_property != reference, key


@pytest.mark.parametrize("family", _families())
def test_the_shipped_set_equals_the_selected_set(family: str) -> None:
    ordering = _read(family, "ordering.json")
    for name, collection in (
        ("beads.json", "beads/"),
        ("links.json", "links/"),
        ("types.json", "types/"),
    ):
        shipped = [record["id"] for record in _read(family, "dataset", name)["items"]]
        expected = ordering["collections"][collection]["selected_set"]
        assert sorted(shipped) == expected
        assert len(shipped) == ordering["collections"][collection]["total"]


def test_serialization_order_is_deterministic_and_never_canonical() -> None:
    records = [{"id": f"https://x.example/beads/{index:04d}"} for index in range(2, 40)]
    first = [record["id"] for record in serialization_order(records)]
    second = [record["id"] for record in serialization_order(list(reversed(records)))]
    assert first == second
    assert sorted(first) == sorted(record["id"] for record in records)
    assert first != sorted(first)
    # Two-item collections are the case a digest ordering gets right only half
    # the time, so the reversal fallback has to make it total.
    for left, right in (("a", "b"), ("b", "a"), ("m", "z")):
        pair = [
            {"id": f"https://x.example/beads/{left}"},
            {"id": f"https://x.example/beads/{right}"},
        ]
        assert not is_canonical_order(serialization_order(pair))


def test_a_canonical_serialization_would_be_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "membench.bdp_fixtures.emit.serialization_order", lambda records: tuple(records)
    )
    with pytest.raises(BdpMappingError, match="serialized in reference order"):
        emit_family(_families()[0], tmp_path)


# --- the mapping itself -----------------------------------------------------


@pytest.mark.parametrize("family", _families())
def test_every_link_endpoint_resolves_to_a_bead_in_the_same_scope(family: str) -> None:
    beads = {record["id"] for record in _read(family, "dataset", "beads.json")["items"]}
    scope = ScopeUrls.for_family(family)
    for link in _read(family, "dataset", "links.json")["items"]:
        assert link["source"] in beads
        assert link["target"] in beads
        assert link["source"] != link["target"]
        assert link["type"] == CITES_TYPE_ID
        assert link["id"].startswith(scope.links_collection)


@pytest.mark.parametrize("family", _families())
def test_link_count_equals_the_generated_edge_count(family: str) -> None:
    expected = len(build_edges(family, BEADS_PER_FAMILY))
    assert len(_read(family, "dataset", "links.json")["items"]) == expected


def test_an_edge_endpoint_outside_the_scope_is_refused() -> None:
    scope = ScopeUrls.for_family(_families()[0])
    with pytest.raises(BdpMappingError, match="names no Bead in this Scope"):
        link_records(_families()[0], scope, ((0, 9),), 4)


def test_two_nodes_projecting_onto_one_bead_id_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A width so narrow that collisions are certain, which is the only way here.

    The generator names nodes by ordinal, so two nodes can only collide if the id
    space is too small. That is a real failure mode when the width is tuned, and
    it must not silently drop a Bead.
    """

    monkeypatch.setattr("membench.bdp_fixtures.mapping.BEAD_ID_WIDTH", 1)
    with pytest.raises(BdpMappingError, match="project onto the same Bead id"):
        bead_records(_families()[0], ScopeUrls.for_family(_families()[0]), 64)


def test_a_link_ordinal_wider_than_the_pad_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    scope = ScopeUrls.for_family(_families()[0])
    edges = tuple((0, index) for index in range(1, 12))
    assert link_records(_families()[0], scope, edges, 12)  # under the width, this is fine
    monkeypatch.setattr("membench.bdp_fixtures.mapping.LINK_ID_WIDTH", 1)
    with pytest.raises(BdpMappingError, match="exceeds the zero-padded width"):
        link_records(_families()[0], scope, edges, 12)


def test_link_ids_are_assigned_after_sorting_on_the_published_endpoints() -> None:
    """So the Link id space carries nothing about the order edges were generated in."""

    scope = ScopeUrls.for_family(_families()[0])
    edges = ((0, 1), (0, 2), (0, 3), (1, 2))
    forward = link_records(_families()[0], scope, edges, 8)
    shuffled = link_records(_families()[0], scope, tuple(reversed(edges)), 8)
    assert [record["id"] for record in forward] == [record["id"] for record in shuffled]
    assert [(r["source"], r["target"]) for r in forward] == [
        (r["source"], r["target"]) for r in shuffled
    ]
    endpoints = [(record["source"], record["target"]) for record in forward]
    assert endpoints == sorted(endpoints)


def test_bead_ids_are_not_in_the_order_the_graph_was_built() -> None:
    family = _families()[0]
    built = [bead_local_id(family, node_key(index)) for index in range(BEADS_PER_FAMILY)]
    assert len(set(built)) == len(built)
    assert built != sorted(built)
    assert all(len(local) == len(built[0]) for local in built)


def test_scope_base_must_be_absolute_and_unslashed() -> None:
    with pytest.raises(BdpMappingError, match="absolute http"):
        ScopeUrls("fixtures.mem.example/ordering/x")
    with pytest.raises(BdpMappingError, match="trailing slash"):
        ScopeUrls("https://fixtures.mem.example/ordering/x/")


def test_descriptor_filename_mirrors_the_type_id() -> None:
    assert descriptor_filename(BEAD_TYPE_ID) == "memory.json"
    assert descriptor_filename(CITES_TYPE_ID) == "cites.json"
    with pytest.raises(BdpMappingError, match="not under"):
        descriptor_filename("https://elsewhere.example/types/memory")
    for bad in (f"{TYPES_BASE}/", f"{TYPES_BASE}/a/b"):
        with pytest.raises(BdpMappingError, match="single plain path segment"):
            descriptor_filename(bad)


def test_discovery_needs_a_limit_to_advertise() -> None:
    with pytest.raises(BdpMappingError, match="at least one page limit"):
        discovery_document(ScopeUrls.for_family(_families()[0]), limits=())


def test_revisions_are_stable_across_calls_but_differ_by_content() -> None:
    scope = ScopeUrls.for_family(_families()[0])
    once = bead_records(_families()[0], scope, 4)
    twice = bead_records(_families()[0], scope, 4)
    assert [record["revision"] for record in once] == [record["revision"] for record in twice]
    # Two Beads with different properties get different revisions.
    assert len({record["revision"] for record in once}) == len(once)
    other = bead_records(_families()[1], ScopeUrls.for_family(_families()[1]), 4)
    assert {record["revision"] for record in once}.isdisjoint(
        record["revision"] for record in other
    )


def test_emit_family_refuses_an_unknown_family(tmp_path: Path) -> None:
    with pytest.raises(BdpMappingError, match="unknown graph family"):
        emit_family("not-a-family", tmp_path)


def test_duplicate_endpoint_tuples_are_counted_not_assumed_zero() -> None:
    """Six of the seven families publish 0 here, so a `return 0` stub agrees."""

    def link(source: str, target: str) -> dict[str, str]:
        return {"type": CITES_TYPE_ID, "source": source, "target": target}

    assert _duplicate_endpoint_tuples([]) == 0
    assert _duplicate_endpoint_tuples([link("a", "b"), link("b", "a")]) == 0
    assert _duplicate_endpoint_tuples([link("a", "b"), link("a", "b")]) == 1
    assert _duplicate_endpoint_tuples([link("a", "b")] * 4) == 3
    assert _duplicate_endpoint_tuples([{"type": "t2", "source": "a", "target": "b"}] * 2) == 1


# --- the ordering expectations ----------------------------------------------


@pytest.mark.parametrize("family", _families())
def test_the_recorded_order_is_the_ascending_canonical_uri_of_what_shipped(family: str) -> None:
    ordering = _read(family, "ordering.json")
    assert ordering["reference_order"] == "ascending-canonical-uri"
    assert ordering["scope"] == ScopeUrls.for_family(family).scope_url
    for block in ordering["collections"].values():
        assert block["selected_set"] == sorted(block["selected_set"])
        assert block["total"] == len(block["selected_set"])
        assert len(set(block["selected_set"])) == block["total"]


@pytest.mark.parametrize("family", _families())
def test_the_page_partition_follows_from_the_selected_set(family: str) -> None:
    ordering = _read(family, "ordering.json")
    blocks = list(ordering["collections"].values()) + list(ordering["selections"])
    for block in blocks:
        for limit_text, page in block["pages"].items():
            pages = _chunks(block["selected_set"], int(limit_text))
            assert page["page_item_counts"] == [len(chunk) for chunk in pages] or (
                block["total"] == 0 and page["page_item_counts"] == [0]
            )
            assert page["page_count"] == max(1, len(pages))
            assert sum(page["page_item_counts"]) == block["total"]
            assert page["spans_multiple_pages"] is (page["page_count"] > 1)


def test_page_arithmetic_against_a_hand_written_table() -> None:
    family = _families()[0]
    scope = ScopeUrls.for_family(family)
    beads = bead_records(family, scope, 7)
    links = link_records(family, scope, ((0, 1),), 7)
    ordering = ordering_expectations(beads, links, type_summaries(), scope, limits=(3, 7, 25))
    pages = ordering["collections"]["beads/"]["pages"]
    assert pages["3"]["page_count"] == 3
    assert pages["3"]["page_item_counts"] == [3, 3, 1]
    assert pages["3"]["spans_multiple_pages"] is True
    assert pages["7"]["page_count"] == 1
    assert pages["7"]["page_item_counts"] == [7]
    assert pages["7"]["spans_multiple_pages"] is False
    assert pages["25"]["page_count"] == 1
    assert ordering["collections"]["types/"]["pages"]["3"]["page_count"] == 1


def test_an_empty_selection_is_one_empty_page_not_zero_pages() -> None:
    """A conformant authority answers an empty set with one empty page.

    Recording `page_count: 0` would make the fixture demand a response no
    authority can send, and would quietly excuse one that returns nothing at all.
    """

    empty = [
        selection
        for family in _families()
        for selection in _read(family, "ordering.json")["selections"]
        if selection["total"] == 0
    ]
    assert empty, "no family exercises the empty-selection case any more"
    for selection in empty:
        for page in selection["pages"].values():
            assert page["page_count"] == 1
            assert page["page_item_counts"] == [0]
            assert page["spans_multiple_pages"] is False


@pytest.mark.parametrize("family", _families())
def test_each_selection_matches_what_its_predicate_actually_selects(family: str) -> None:
    links = _read(family, "dataset", "links.json")["items"]
    beads = _read(family, "dataset", "beads.json")["items"]
    for selection in _read(family, "ordering.json")["selections"]:
        parameters = selection["parameters"]
        if "source" in parameters:
            matched = [x["id"] for x in links if x["source"] == parameters["source"]]
        elif "target" in parameters:
            matched = [x["id"] for x in links if x["target"] == parameters["target"]]
        elif "endpoint" in parameters:
            endpoint = parameters["endpoint"]
            matched = [x["id"] for x in links if endpoint in (x["source"], x["target"])]
        else:
            key, value = re.fullmatch(
                r'\$\[\?@\.properties\.(\w+) == "([^"]+)"\]', parameters["selector"]
            ).groups()  # type: ignore[union-attr]
            matched = [x["id"] for x in beads if x["properties"][key] == value]
        assert selection["selected_set"] == sorted(matched)
        assert selection["total"] == len(matched)


@pytest.mark.parametrize("family", _families())
def test_the_link_predicates_are_distinct_exactly_where_the_manifest_says(family: str) -> None:
    """`source`, `target` and `endpoint` are three predicates, not three spellings.

    In a family whose hub has no inbound Link the inbound set is empty and the
    incident set equals the outbound one, so a pass there does not discriminate
    `endpoint` from `source`. The manifest records which families those are, and
    this asserts the recorded flag against the data rather than trusting either.
    """

    selections = {
        next(iter(selection["parameters"])): selection
        for selection in _read(family, "ordering.json")["selections"]
        if next(iter(selection["parameters"])) in ("source", "target", "endpoint")
    }
    outbound = set(selections["source"]["selected_set"])
    inbound = set(selections["target"]["selected_set"])
    incident = set(selections["endpoint"]["selected_set"])
    assert incident == outbound | inbound
    assert not outbound & inbound
    distinct = len({frozenset(outbound), frozenset(inbound), frozenset(incident)}) == 3
    assert distinct == _manifest()["families"][family]["hub_predicates_are_pairwise_distinct"]


def test_some_family_discriminates_endpoint_from_source() -> None:
    """Otherwise the endpoint predicate is untested across the whole tree."""

    flags = [
        entry["hub_predicates_are_pairwise_distinct"] for entry in _manifest()["families"].values()
    ]
    assert any(flags)


def test_ordering_refuses_records_handed_over_out_of_order() -> None:
    family = _families()[0]
    scope = ScopeUrls.for_family(family)
    beads = bead_records(family, scope, 4)
    links = link_records(family, scope, ((0, 1),), 4)
    with pytest.raises(BdpMappingError, match="beads records were not handed over"):
        ordering_expectations(tuple(reversed(beads)), links, type_summaries(), scope)


def test_ordering_refuses_a_family_with_no_links() -> None:
    family = _families()[0]
    scope = ScopeUrls.for_family(family)
    with pytest.raises(BdpMappingError, match="no Links"):
        ordering_expectations(bead_records(family, scope, 4), (), type_summaries(), scope)


def test_a_nonpositive_page_limit_is_refused() -> None:
    family = _families()[0]
    scope = ScopeUrls.for_family(family)
    beads = bead_records(family, scope, 4)
    links = link_records(family, scope, ((0, 1),), 4)
    for limit in (0, -1):
        with pytest.raises(BdpMappingError, match="page limit must be positive"):
            ordering_expectations(beads, links, type_summaries(), scope, limits=(limit,))


def test_the_density_selection_names_the_highest_outdegree_bead() -> None:
    family = _families()[0]
    scope = ScopeUrls.for_family(family)
    beads = bead_records(family, scope, 5)
    links = link_records(family, scope, ((0, 2), (0, 3), (1, 2)), 5)
    ordering = ordering_expectations(beads, links, type_summaries(), scope)
    assert ordering["selections"][0]["parameters"]["source"] == scope.bead(
        bead_local_id(family, node_key(0))
    )

    # A tie breaks on the canonical URI, so the choice is reproducible.
    tied = link_records(family, scope, ((0, 2), (1, 2)), 5)
    tied_ordering = ordering_expectations(beads, tied, type_summaries(), scope)
    assert tied_ordering["selections"][0]["parameters"]["source"] == max(
        scope.bead(bead_local_id(family, node_key(index))) for index in (0, 1)
    )


@pytest.mark.parametrize("family", _families())
def test_the_density_selection_names_the_hub_the_shipped_links_have(family: str) -> None:
    outdegree, _ = _degrees(family)
    hub = max(outdegree, key=lambda source: (outdegree[source], source))
    selection = next(
        s for s in _read(family, "ordering.json")["selections"] if "source" in s["parameters"]
    )
    assert selection["parameters"]["source"] == hub
    assert selection["total"] == outdegree[hub]


# --- the README ------------------------------------------------------------


def test_readme_density_table_matches_the_manifest() -> None:
    text = _readme()
    for family, entry in _manifest()["families"].items():
        row = next(line for line in text.splitlines() if line.startswith(f"| `{family}`"))
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert cells[1] == str(entry["link_count"]), family
        assert cells[2] == str(entry["max_outdegree"]), family
        assert cells[3] == str(entry["max_indegree"]), family
        assert cells[4] == str(entry["duplicate_endpoint_tuple_links"]), family


def test_readme_states_the_boundaries_it_has_to_state() -> None:
    # Whitespace-normalized: the claims matter, the line wrapping does not.
    text = " ".join(_readme().split())
    for claim in (
        "generated from this package's own seed",
        "not a projection",
        "authored to exercise a consumer policy",
        "not measured field frequencies",
        "deliberately not in the reference order",
        "leaves the choice of order to the implementation",
        "does not discriminate `endpoint` from `source`",
    ):
        assert claim in text, claim


def test_readme_names_every_family_it_ships() -> None:
    text = _readme()
    for family in _families():
        assert f"`{family}`" in text, family


def test_readme_page_split_claim_holds() -> None:
    ordering = _read(OVERSIZED_SELECTION_FAMILY, "ordering.json")
    outbound = next(s for s in ordering["selections"] if "source" in s["parameters"])
    # Whitespace-normalized: the claims matter, the line wrapping does not.
    text = " ".join(_readme().split())
    assert f"{outbound['total']} outbound Links" in text
    assert f"{outbound['pages']['25']['page_count']} pages at a limit of 25" in text
    assert f"{outbound['pages']['200']['page_count']} pages at a limit of 200" in text


def test_readme_carries_no_em_dash() -> None:
    assert "—" not in _readme()
