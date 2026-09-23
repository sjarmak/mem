# Mint files — the private inverse of the published id space

A published benchmark record names its evidence by ALIAS: `k-` followed by 16 hex
characters, minted by `membench/public_alias.py` and keyed by a mint seed. A file in
this directory is the whole inverse of one corpus's alias space.

**Never publish a file from this directory.** It maps every published id back to the
internal id that states its role, so a holder can label each candidate in a published
record as the gold fact, a distractor or a stale version. That is exactly the
capability the alias scheme removes from a solver. It does not belong in the public
tree, in a release, in an issue, or in a model's context.

## DECISION (mem-r6yzk N3): the mint is NOT tracked in git

A mint file stays on disk at `memory-bench/fixtures/mint/<corpus>.mint.json`, where the
ratified design (mem-r6yzk.8) put it and where `public_alias.mint_path` looks for it.
It is git-ignored, and the **seed** — not the file — is what gets backed up.

### Why not tracked

This repository is **public**. `origin` is `https://github.com/sjarmak/mem.git`, which
the GitHub API reports as `"private": false, "visibility": "public"`, and an outside
collaborator (beads maintainer quad341) holds push on it. "The mint lives inside the
private repo" was written against a repository that is not private. Before this
decision the file was untracked *and* un-ignored: `git check-ignore` returned 1 and
`git status` showed `?? memory-bench/fixtures/mint/`, so one `git add -A` followed by a
push would have put 2920 alias mappings and the seed literal on a public remote, and
`git push --force` does not unpublish a fetched object. The exposure was one careless
commit deep, and no amount of prose in this file stood between it and the push.

### Why nothing is lost by not tracking it

The map is a pure function of `(corpus name, mint seed, internal id)`:
`public_alias._alias_for` is `HMAC-SHA256(seed, "<corpus>/<internal_id>")` truncated to
16 hex characters, and `scripts/mint_public_ids.py` rebuilds the whole file from the
frozen corpus plus the seed. `read_alias_map` re-derives every entry on load and refuses
a file that does not reproduce, so the on-disk map is already treated as derived rather
than as a source of truth. Re-minting the same corpus under the same seed is
byte-identical, and an alias depends only on its own internal id, so adding sequences
never moves an alias already published.

So the property the ratified design actually needs — **a release's ids reproduce, and
consumers' cached results keep lining up across releases** — rests on two things, and
neither of them is this file being in git:

1. the frozen corpus, which *is* tracked (`fixtures/worlds-public-v1/`), and
2. the seed.

### Where the seed is backed up — OUTSTANDING, and who has to do it

The seed is the only non-regenerable input, so it is the only thing that has to survive.
Right now it survives in exactly one place: the mint file on this machine, which is
untracked, uncommitted and therefore unbacked. That is the live risk this decision
creates and does not itself close, and a wiped worktree loses it.

The backup is an operator action, not something this change performed, and it is written
here rather than done here because it cannot be done from inside the repository — that
is the whole point of keeping the secret out of it. What is required:

* Copy the `mint_seed` value out of `fixtures/mint/worlds-public-v1.mint.json` into a
  secret store outside this repository and outside any of its remotes, labelled with the
  corpus name (`worlds-public-v1`) so a later release knows which corpus it re-mints.
* Record nothing else: the map does not need backing up, and a second copy of it is a
  second thing that can leak.

Anyone re-minting then supplies it on argv, from the environment rather than from a file
in the tree:

```bash
PYTHONPATH=. python3 scripts/mint_public_ids.py \
    --corpus fixtures/worlds-public-v1 --mint-seed "$MEMBENCH_MINT_SEED"
```

`scripts/mint_public_ids.py` has no default seed, by design: a default is a published
secret. Losing the seed does not corrupt an existing release — the published ids are
already out and still resolve — but the next release of this corpus could not reproduce
them, so consumers comparing across releases would have to re-run everything.

**Check the restore rather than assuming it.** Re-mint from the backed-up value into a
scratch path and diff; a correct backup reproduces the live map byte for byte.

```bash
PYTHONPATH=. python3 scripts/mint_public_ids.py \
    --corpus fixtures/worlds-public-v1 --mint-seed "$MEMBENCH_MINT_SEED" \
    --out /tmp/restore-check.mint.json
diff /tmp/restore-check.mint.json fixtures/mint/worlds-public-v1.mint.json
```

### What enforces this

`.gitignore` ignores `memory-bench/fixtures/mint/*` (keeping this README tracked, since
it is the decision and not the material) and `*.mint.json` repo-wide, so a copy that
keeps the suffix is ignored wherever it is moved.

`memory-bench/tests/test_mint_is_unpublishable.py` is the mechanical half, and it checks
CONTENT rather than paths, so a copy renamed past the ignore rules is still caught. It
refuses to let the publish set — everything under `public/`, every tracked file, and
every untracked file `git add -A` would stage — carry a mint seed literal, a
`(published alias, its internal id)` pair, or a mint file's own JSON markers. Each
detector is proven live by planting real mint material in `public/` under an innocuous
name and asserting the detector fires, so a guard that has stopped detecting anything
fails instead of reporting green.

## What the secrecy rests on

The scheme's secrecy rests on the seed and the map staying off every remote, and on
nothing else. It does NOT rest on this repository being private, which it is not. While
the mint was tracked, anyone with repository access could de-anonymize every published id
and reconstruct the distractor interleave; that is why it is no longer tracked. Leaking
`memory-bench/fixtures/mint/` voids the corpus's id-anonymity, and the corpus must then
be re-minted with a seed held outside the repository — which renumbers every published id
and breaks comparability with the release already out.

Note what is *not* secret, so the boundary is not guarded in the wrong place: this is an
open-key benchmark, so the published records ship `gold_ids` and the frozen source
corpus is tracked here in the open. Validity comes from the harness seam withholding the
key at run time, not from the key being unfindable. What the mint adds on top is that a
published alias cannot be turned back into a role by *computation* from published
material alone. The earlier scheme published `opaque_memory_id(namespace, label)`
directly; the namespace is published in every record's provenance and the label
vocabulary is small and fixed, so recomputing the hash over that vocabulary recovered
the role of 1000 of 1000 published ids. The alias is an HMAC over the internal id under
the mint seed, so the same brute force recovers nothing.

## Minting a corpus

```bash
PYTHONPATH=. python3 scripts/mint_public_ids.py \
    --corpus fixtures/worlds-public-v1 --mint-seed "$MEMBENCH_MINT_SEED"
```

The seed is stored inside the file it produces. That adds no exposure — a holder of the
map already has everything the seed protects — and it makes a mint file self-describing,
so `read_alias_map` can re-derive and verify it without being told the seed again.
