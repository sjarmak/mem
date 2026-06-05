"""Harbor integration — emit benchmark sequences as Harbor tasks (§14).

Follows Harbor's REAL extension shape (verified against harbor==0.3): a task dir of
`task.toml` + `instruction.md` + `environment/Dockerfile` + `tests/test.sh`
(+ optional `solution/solve.sh`), with the verifier writing a reward in [0,1] to
`/logs/verifier/reward.txt`. (NOT the spec §14 hypothesized
task_adapter.py/dataset_adapter.py/scorer.py names — plan §A, DIV-7.)
"""

from membench.harbor.adapter import REWARD_TEXT_PATH, SequenceAdapter

__all__ = ["REWARD_TEXT_PATH", "SequenceAdapter"]
