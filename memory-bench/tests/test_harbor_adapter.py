import pytest

from membench.dataset import load_sequence
from membench.harbor.adapter import REWARD_TEXT_PATH, SequenceAdapter
from membench.schemas.conditions import Condition
from tests.paths import FIXTURE


def test_emits_real_harbor_task_shape(tmp_path):
    seq = load_sequence(FIXTURE)
    created = SequenceAdapter(seq, tmp_path).run()
    # 3 steps x 3 conditions.
    assert len(created) == 9
    for task_dir in created:
        assert (task_dir / "task.toml").is_file()
        assert (task_dir / "instruction.md").is_file()
        assert (task_dir / "environment" / "Dockerfile").is_file()
        assert (task_dir / "tests" / "test.sh").is_file()
        assert (task_dir / "solution" / "solve.sh").is_file()


def test_verifier_writes_canonical_reward_path(tmp_path):
    seq = load_sequence(FIXTURE)
    created = SequenceAdapter(seq, tmp_path, conditions=[Condition.NO_MEMORY]).run()
    test_sh = (created[0] / "tests" / "test.sh").read_text()
    assert REWARD_TEXT_PATH in test_sh
    assert REWARD_TEXT_PATH == "/logs/verifier/reward.txt"


def test_oracle_condition_injects_memory_into_instruction(tmp_path):
    seq = load_sequence(FIXTURE)
    created = SequenceAdapter(seq, tmp_path, conditions=[Condition.ORACLE_MEMORY]).run()
    s3 = next(d for d in created if d.name.endswith("s3-add-endpoint"))
    instruction = (s3 / "instruction.md").read_text()
    assert "Provided context (oracle memory)" in instruction
    assert "127.0.0.1" in instruction  # the injected binding convention


def test_existing_dir_without_overwrite_raises(tmp_path):
    seq = load_sequence(FIXTURE)
    SequenceAdapter(seq, tmp_path, conditions=[Condition.NO_MEMORY]).run()
    with pytest.raises(FileExistsError):
        SequenceAdapter(seq, tmp_path, conditions=[Condition.NO_MEMORY]).run()


def test_generated_verifier_scores_reward_correctly(tmp_path):
    """Execute the emitted test.sh against present/absent answers and assert the
    reward written to the canonical path is correct (catches reward-math bugs)."""
    import subprocess

    seq = load_sequence(FIXTURE)
    created = SequenceAdapter(seq, tmp_path / "tasks", conditions=[Condition.MEMORY_ENABLED]).run()
    s3 = next(d for d in created if d.name.endswith("s3-add-endpoint"))

    reward_file = tmp_path / "reward.txt"
    answer_file = tmp_path / "answer.txt"
    script = (
        (s3 / "tests" / "test.sh")
        .read_text()
        .replace(REWARD_TEXT_PATH, str(reward_file))
        .replace("/app/answer.txt", str(answer_file))
    )
    runnable = tmp_path / "run_test.sh"
    runnable.write_text(script)

    # No answer → reward 0.
    subprocess.run(["bash", str(runnable)], check=True)
    assert float(reward_file.read_text()) == 0.0

    # One of three markers present → partial reward 1/3 (exercises awk division).
    answer_file.write_text("endpoint-created\n")
    subprocess.run(["bash", str(runnable)], check=True)
    assert abs(float(reward_file.read_text()) - 1 / 3) < 1e-3

    # All three check markers present → reward 1.
    answer_file.write_text("endpoint-created\napplies-loopback-binding\nuses-shared-types\n")
    subprocess.run(["bash", str(runnable)], check=True)
    assert float(reward_file.read_text()) == 1.0


def test_task_toml_parses_with_harbor_if_available(tmp_path):
    harbor_config = pytest.importorskip("harbor.models.task.config")
    seq = load_sequence(FIXTURE)
    created = SequenceAdapter(seq, tmp_path, conditions=[Condition.MEMORY_ENABLED]).run()
    toml_text = (created[0] / "task.toml").read_text()
    cfg = harbor_config.TaskConfig.model_validate_toml(toml_text)
    assert cfg.task.name.startswith("membench/")
