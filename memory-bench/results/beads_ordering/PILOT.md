# Protocol pilot — not the full experiment

`pilot-navigation/` and `pilot-search-only/` contain six real headless-agent
runs for one 50-Memory task, page size 5, and the key/PageRank/BM25F arms.
`pilot-combined/` analyzes those six rows together.

These traces validate fixed-query search, wrapper-held continuation, recall
policy enforcement, graph-hop logging, provenance, and report generation. They
do not cover all tasks, corpus sizes, structural priors, or page sizes and must
not be used as a retrieval-policy conclusion. The complete grid commands are in
`docs/beads-memory-ordering-experiment.md`.
