"""The E0a verb table and the argv-grammar rule that assigns a bucket (mem-e4fby).

This is the ONLY module in the package that names a bd subcommand token. Bucketing
reads three things and nothing else: the subcommand, how many positionals the
invocation carries, and whether an explicit key flag is present. No argument's
content is inspected, matched or scored anywhere - that is the ZFC boundary this
study is built on, and the acceptance criterion greps for it.

Buckets, and why the read class is split three ways:

``TARGETED_READ``
    A keyed fetch. This is the ONLY read bucket that can enter the read-after-write
    join, because it is the only one that names the thing it wants.
``SEARCH_READ``
    A term search. Carries a query but not a key.
``BROWSE_READ``
    A list-all with no argument at all. It carries no key, so it can never join a
    prior write, while still inflating a pooled read rate. Folding it into a single
    "read rate" is the specific error this split exists to prevent.
``MEMORY_WRITE``
    Explicit capture and its inverse. Its key is resolvable only from an explicit
    key flag; see ``classify``.
``INJECTION``
    Delivery INTO the agent, not capture. Reclassified out of the sealed profile's
    write kind; see preregistration.json.
``DEP_WRITE``
    Issue-dependency edges. `bd link` is shorthand for `bd dep add`, NOT a memory
    verb; anything that counted it as one is inflated. (`bd unlink` does not exist.)
"""

from __future__ import annotations

from dataclasses import dataclass

from cligrammar import normalize

# --- verb tables (the only place subcommand tokens appear) --------------------
TARGETED_READ_VERBS = {"recall"}
SEARCH_OR_BROWSE_VERBS = {"memories"}
MEMORY_WRITE_VERBS = {"remember", "forget"}
INJECTION_VERBS = {"prime"}
DEP_WRITE_VERBS = {"link", "dep"}
KEY_FLAGS = {"--key", "-k"}
# -----------------------------------------------------------------------------

TARGETED_READ = "targeted_read"
SEARCH_READ = "search_read"
BROWSE_READ = "browse_read"
MEMORY_WRITE = "memory_write"
INJECTION = "injection"
DEP_WRITE = "dep_write"
OTHER = "other"

MEMORY_BUCKETS = (TARGETED_READ, SEARCH_READ, BROWSE_READ, MEMORY_WRITE)
READ_BUCKETS = (TARGETED_READ, SEARCH_READ, BROWSE_READ)
ALL_BUCKETS = (*MEMORY_BUCKETS, INJECTION, DEP_WRITE, OTHER)


@dataclass(frozen=True)
class Classified:
    """One bd invocation, reduced to grammar."""

    bucket: str
    #: True when the invocation names the memory it acts on. For a write that
    #: means an explicit key flag and nothing else: the shipped CLI auto-generates
    #: the key from the content, so a positional never names the stored memory.
    #: Every write statistic therefore carries a band, not a point estimate.
    unambiguous: bool
    #: The key token, for TARGETED_READ and keyed writes. Callers digest it; it is
    #: never emitted.
    key: str | None
    #: Set when a bare subcommand landed in BROWSE_READ from the keyed verb rather
    #: than the list verb, so the two paths into that bucket stay separable.
    browse_from_bare_targeted: bool = False


def classify(argv: list[str]) -> Classified:
    """Assign one bd argv to its bucket by CLI grammar alone."""
    sub, positionals, flags = normalize(argv)
    flag_key = next((flags[f] for f in KEY_FLAGS if flags.get(f)), None)
    keyed = any(f in flags for f in KEY_FLAGS)
    n = len(positionals)

    if sub in TARGETED_READ_VERBS:
        if keyed or n >= 1:
            key = flag_key if flag_key is not None else (positionals[0] if n >= 1 else None)
            return Classified(TARGETED_READ, unambiguous=True, key=key)
        return Classified(BROWSE_READ, unambiguous=True, key=None, browse_from_bare_targeted=True)

    if sub in SEARCH_OR_BROWSE_VERBS:
        if n >= 1:
            return Classified(SEARCH_READ, unambiguous=True, key=None)
        return Classified(BROWSE_READ, unambiguous=True, key=None)

    if sub in MEMORY_WRITE_VERBS:
        if flag_key is not None:
            return Classified(MEMORY_WRITE, unambiguous=True, key=flag_key)
        # No explicit key flag: the shipped CLI auto-generates the key from the
        # content, so no positional names the stored memory and none is taken as
        # one. The invocation still counts as a write; it just supplies no key and
        # therefore lands in the ambiguity band and never enters the join.
        return Classified(MEMORY_WRITE, unambiguous=False, key=None)

    if sub in INJECTION_VERBS:
        return Classified(INJECTION, unambiguous=True, key=None)
    if sub in DEP_WRITE_VERBS:
        return Classified(DEP_WRITE, unambiguous=True, key=None)
    return Classified(OTHER, unambiguous=True, key=None)
