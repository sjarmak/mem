Real ftp shapes catalogued: 6 (2 memory-dependent; [x] = a synthetic blueprint reproduces it)
  [x] aggregation-projection: Aggregate/roll up values produced across earlier steps into a final projection (token-count rollups, score-result projection, raw counts in JSON); emit nothing when the inputs are unavailable.
  [x] exclusion-filter: Exclude a flagged subset (quota-errored tasks) from a downstream aggregate so paired scores and per-config means are not contaminated.

# mem-bxhh.5 — synthetic↔real-ftp calibration (Gate 0)
verdict      : NO-GO (flat anchor)
reason       : real anchor does not rank memory configs non-flatly: no arm's lift CI clears 0 at N=8 (span=0.125). Gate 0 is uncomputable — the PRD's honest-null exit. Source: mem-1fl8/mem-58rp wanz.9 sound-oracle eval (branch mem-58rp-wanz9-run @ adff941).
spearman rho   : n/a  (threshold 0.6)
anchor flat  : True  (span 0.125)
shared arms  : — (baseline only)

arm             synthetic lift  real-anchor lift
------------------------------------------------
builtin                      —             0.125
filesystem               0.250                 —
lexical                  0.250                 —
none                     0.000             0.000
oracle                   0.250                 —
ours                         —             0.000
