# Peer-agent collaboration on the memory beads experiments

This is the contract for an agent seat run by someone outside this machine (a Beads
maintainer's own orchestrator, for example) that wants to work the memory beads
experiments alongside the project owner. It says which surfaces exist, what each side
may do on them without asking, and how a turn is run and reported when the peer's
runtime is not the one this rig was built on.

The general operating rules for the project live in
[`project-management.md`](project-management.md); this document only adds what a
second, external seat needs.

## The surfaces

| Surface                                                      | Who sees it                    | What it is for                                                                                                             |
| ------------------------------------------------------------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| [Project board](https://github.com/users/sjarmak/projects/4) | public                         | cross-track status, the decision queue, upstream readiness                                                                 |
| `sjarmak/mem` Issues                                         | public                         | every commitment, question, experiment, decision, and its discussion                                                       |
| `sjarmak/mem` repository                                     | public                         | preregistrations under `docs/`, the harness under `memory-bench/`, sanitized result packages under `memory-bench/results/` |
| the local bead store and its issue mirror                    | owner only                     | execution state: claims, leases, dependencies, notes                                                                       |
| raw run artifacts (transcripts, streams, receipts)           | the operator who produced them | evidence that never leaves its machine unsanitized                                                                         |

A peer reads and writes the first three. An Issue is the public projection of a bead;
the bead id is named on the Issue for the owner's benefit and resolves to nothing
outside this machine. Nothing a peer needs is only in the bead.

## What a peer may do without asking

- Comment on any Issue: a question, a counter-proposal, a review of a preregistration,
  a reading of a published package.
- Propose a candidate for a flywheel turn (a `bd` build as `(remote, sha)`, or a
  capability-text change) as a comment on the experiment Issue.
- Claim an Issue that carries the `peer-ready` label: assign themselves, say in a
  comment what they intend to do, and set the board `Status` to In Progress. The label
  marks the items that can be worked without access to the owner's bead store.
- Run a preregistered turn on their own runtime (below) and open a pull request with
  the sanitized package.
- Set board fields that describe their own measurement: `Evidence stage`,
  `Validation coverage`, `Task source`, `Contamination control`, `Precision status`.

## What stays with the project owner

- Spend. Every provider session bought on this side is preceded by a ruling on a
  `[Decision]` Issue. A peer recommends; the owner rules. The peer's own spend on its
  own runtime is its own call.
- Changes to a preregistration after its first fire. The fixed items are listed in each
  preregistration; changing one ends that series and starts a new one, under a new
  document.
- Anything posted upstream to `gastownhall/beads` on the project's behalf.
- Merges to `main`, and the `Decision` field on a `[Decision]` Issue.

## Running a turn on a different runtime

The capture protocol (`prereg-beads-capture.md`) measures `reached` and `engaged` from
bd's side, through the receipt surface, so it does not need a transcript from the
agent. The harness seam in `memory-bench/membench/runner/agent_harness.py` lets a
cell spawn any runtime that runs as a command.

A peer supplies:

1. **The command line**, with the prompt as one whole argv element spelled `{prompt}`
   and, optionally, the model as `{model}`. One whole element, not a substring: a
   prompt spliced into a shell string is a prompt the shell parses.
2. **A runtime name and version string.** Both go into the resume identity; a run on
   another version does not pool with this one.
3. **The beads build**, as `--bd-ref <remote> <sha>`. The fire fetches the commit,
   builds it with the beads Makefile, and records the commit and the binary's sha256.
4. **Conditions**, if any, as environment variables exported to the spawned agent.
   They are fingerprinted into the identity. The names the rig sets itself (`PATH`,
   `PWD`, `HOME`, `XDG_CONFIG_HOME`, the config-dir variable, and the receipt attribution
   keys) cannot be conditions.
5. **Login material**, if the runtime needs file-backed authentication, as a directory passed
   with `--harness-home-seed`. The harness copies only that directory into a fresh home for each
   cell and points both `HOME` and `XDG_CONFIG_HOME` inside it. The resume identity records the
   isolation policy and a content fingerprint, never the source path or credentials.

What is different on a runtime without a native memory of its own: the `builtin`
comparator is refused, so a turn there is the treatment against the floor. The floor is
measured once per runtime version and reused. The comparison that matters is still the
candidate against its own baseline build, within one runtime; nothing pools across
runtimes.

## Reporting a turn

Open a pull request against `main` adding one directory under `memory-bench/results/`
named for the turn. It holds:

- the plan record and resume identity (runtime name and version, build commit and
  binary sha256, conditions fingerprint, per-cell home isolation and seed fingerprint, the
  eight task ids);
- per-arm `reached` and `engaged` counts with 95% intervals, and the count of
  UNMEASURED cells with the validity condition each tripped;
- the `bd` receipt rows, which carry tokens and argv and nothing else.

It does not hold transcripts, streams, prompts with local content, home-directory
paths, account identifiers, or credentials. The scrub is the peer's before opening the
pull request and the owner's before merging; a package that fails it is returned, not
edited.

Then set `Evidence link` on the board item to the merged directory, and comment on the
experiment Issue with the one-paragraph reading.

## Cadence

There is no meeting. The `Decision queue` view is the standing agenda: an open
`[Decision]` with `Decision: Undecided` is waiting on a ruling, and a peer's comment
there is read before the ruling is made. A ruling is recorded on the Issue and the
board field is set in the same edit.
