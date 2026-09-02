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
``ATTEMPTED_READ_VIA_WRITE_VERB``
    A write verb carrying a flag the shipped binary does not have. The capture verb
    declares exactly one non-global flag (``--key``), so the ``--show`` / ``--get``
    / ``--list`` forms are not writes at all: they are attempted READS spelled with
    a write verb, and the binary rejects them on the undeclared flag. Counting them
    as writes made 38 of 59 published "writes" read attempts.
    Membership is decided by flag NAME against a set derived mechanically from the
    shipped ``--help`` text (``shipped-cli-help/``); no argument content is read.
``REJECTED_BY_SHIPPED_GRAMMAR``
    A write verb whose POSITIONAL COUNT the shipped binary refuses. Its usage line
    declares exactly one positional for each of the two write verbs, so a
    zero-positional form - including the keyed-but-contentless ``--key k`` with no
    body - and a two-positional form are both refused before anything is stored.
    Same argument as ``ATTEMPTED_READ_VIA_WRITE_VERB``, one layer down:
    an invocation the binary rejects is not a write. Arity is argv grammar, so no
    argument content is read; the required count is derived from the captured usage
    line by ``cligrammar.help_usage_positionals``.
``BARE_KEY_AMBIGUOUS``
    The capture verb with one positional and no explicit key. The shipped help says
    the positional is the memory CONTENT, and then says that if it is a bare key
    naming an EXISTING memory it is RECALLED instead (and refused if it names
    nothing). Which of the three happened depends on store state that no transcript
    records, so grammar cannot decide it. Calling these writes - which is what the
    first three runs did with all nine of them - assumes the reading that inflates
    the write rate. They are published as their own bucket and as a band on E0.1
    and on the targeted-read rate, never silently folded into either.
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
#: Every flag the shipped bd 1.3.0-rc.1 declares for the write verbs, as the union
#: over their captured help texts (``shipped-cli-help/*.help.txt``, re-captured by
#: ``shipped-cli-help/capture.sh``). Derived by ``cligrammar.help_flag_names``; the
#: test suite re-derives it from those committed files and fails on drift. It is
#: pinned as a literal rather than parsed at run time so the analysis path never
#: depends on whichever bd is on PATH. ``-k`` is NOT in it: the shipped binary
#: declares only the long ``--key``.
WRITE_VERB_SUPPORTED_FLAGS = {
    "--actor",
    "--cpu-profile",
    "--database",
    "--db",
    "--directory",
    "--dolt-auto-commit",
    "--global",
    "--help",
    "--ignore-schema-skew",
    "--json",
    "--key",
    "--mem-profile",
    "--no-color",
    "--quiet",
    "--readonly",
    "--sandbox",
    "--verbose",
    "-C",
    "-h",
    "-q",
    "-v",
}
#: Required positional count per write verb, from each captured usage line
#: (each declares one required word after the subcommand). Derived by
#: ``cligrammar.help_usage_positionals`` and pinned as a literal for the same
#: reason the flag set is: no published number may depend on whichever bd is on
#: PATH. The test suite re-derives both from the committed help text.
WRITE_VERB_REQUIRED_POSITIONALS = {"remember": 1, "forget": 1}
#: The bare-key clause below is documented for the capture verb only; the removal
#: verb's positional is unambiguously a key it deletes.
BARE_KEY_RECALL_VERBS = {"remember"}
# -----------------------------------------------------------------------------

TARGETED_READ = "targeted_read"
SEARCH_READ = "search_read"
BROWSE_READ = "browse_read"
MEMORY_WRITE = "memory_write"
ATTEMPTED_READ_VIA_WRITE_VERB = "attempted_read_via_write_verb"
REJECTED_BY_SHIPPED_GRAMMAR = "rejected_by_shipped_grammar"
BARE_KEY_AMBIGUOUS = "bare_key_ambiguous"
INJECTION = "injection"
DEP_WRITE = "dep_write"
OTHER = "other"

MEMORY_BUCKETS = (
    TARGETED_READ,
    SEARCH_READ,
    BROWSE_READ,
    MEMORY_WRITE,
    ATTEMPTED_READ_VIA_WRITE_VERB,
    REJECTED_BY_SHIPPED_GRAMMAR,
    BARE_KEY_AMBIGUOUS,
)
#: The attempted-read, refused-grammar and ambiguous buckets all stay inside the
#: memory-verb share (E0.5): the agent reached for the memory surface, and E0.5
#: counts reaches. E0.5 is therefore unmoved by any of the three reclassifications,
#: which is the point - they redistribute WITHIN the memory verbs and change no
#: agent's behaviour. All three are kept out of the write rate's low end, because
#: none of them can be shown to have stored anything.
READ_BUCKETS = (TARGETED_READ, SEARCH_READ, BROWSE_READ, ATTEMPTED_READ_VIA_WRITE_VERB)
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
        if any(f not in WRITE_VERB_SUPPORTED_FLAGS for f in flags):
            # A flag the shipped binary does not declare. The invocation cannot
            # have stored anything - bd exits on an unknown flag - so it is not a
            # write. Its key, when it has one, is the first positional: a write's
            # positional is content (A1.1), but this shape is not a write, and the
            # shipped write verb itself RECALLS a bare positional key. Only the
            # flag NAME is inspected; the argument is never read.
            key = flag_key if flag_key is not None else (positionals[0] if n >= 1 else None)
            return Classified(ATTEMPTED_READ_VIA_WRITE_VERB, unambiguous=key is not None, key=key)
        if n != WRITE_VERB_REQUIRED_POSITIONALS[sub]:
            # The usage line the shipped binary prints declares exactly one
            # positional. A zero-positional form stores nothing (the keyed
            # keyed-but-bodiless form included: the key says where to put it, not
            # what to put), and a two-positional form is refused before it gets
            # that far. Rejecting on an undeclared FLAG and rejecting on positional
            # ARITY are the same argument; leaving these in the write bucket after
            # making the flag argument is the inconsistency that argument forbids.
            return Classified(REJECTED_BY_SHIPPED_GRAMMAR, unambiguous=False, key=None)
        if flag_key is not None:
            return Classified(MEMORY_WRITE, unambiguous=True, key=flag_key)
        if sub in BARE_KEY_RECALL_VERBS:
            # One positional, no explicit key: content to be stored under an
            # auto-generated key, OR - per the shipped help - a bare key naming an
            # existing memory, which is RECALLED instead. The transcript records the
            # argv, not the store, so nothing here can tell those apart. Its own
            # bucket, and a band on both rates it could belong to.
            return Classified(BARE_KEY_AMBIGUOUS, unambiguous=False, key=None)
        # The removal verb. Its positional is a key, but a removal can never be the
        # WRITER half of a read-after-write join, so no key is carried and it stays
        # in the write band's ambiguous end.
        return Classified(MEMORY_WRITE, unambiguous=False, key=None)

    if sub in INJECTION_VERBS:
        return Classified(INJECTION, unambiguous=True, key=None)
    if sub in DEP_WRITE_VERBS:
        return Classified(DEP_WRITE, unambiguous=True, key=None)
    return Classified(OTHER, unambiguous=True, key=None)
