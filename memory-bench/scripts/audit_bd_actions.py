#!/usr/bin/env python3
"""Post hoc mechanical audit of saved sessions; no agent calls.

Two jobs, both from the saved transcripts and bd receipts. The strict Write audit checks the
acknowledged config.json Write events of every goal session. The rescore runs today's scorer
over every saved session and, for a four-leg trial, derives the trial verdicts again, so a
scorer fix reaches paid sessions without re-buying them and the saved verdicts can be compared
with the rescored ones side by side.

This supplements frozen scores. It does not replay later filesystem mutations or judge whether
all requested fields retain their meaning. Paths are lexical, not resolved through the vanished
sandbox's symlinks. Run with EXPERIMENT --out AUDIT_DIRECTORY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from membench.runner.bd_actions import valid_write as valid_write
from membench.runner.bd_actions import write_reason
from membench.runner.bd_experiment import _pair_dir, source_fingerprint
from membench.runner.e1_grid import corpus_fingerprint
from membench.runner.e1_reliability import (
    TRIAL_CATEGORICAL,
    TRIAL_VERDICTS,
    BdLegEvidence,
    score_bd_leg,
    trial_outcomes,
)
from membench.runner.headless_agent import tool_calls_from_stream
from membench.runner.leg_plans import GOAL_ROLE, PAIR_ROLES, TRIAL_ROLES, plan_name
from membench.runner.resume_cache import digest
from membench.runner.toolreq_corpus import load_twin_corpus
from membench.runner.toolreq_realagent import DEFAULT_CORPUS, ToolReqRealAgentTask

AUDIT_VERSION = 4


def leg_plan(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """The manifest's leg plan; a manifest frozen before plans existed ran the pair."""
    plan = tuple(str(role) for role in manifest.get("leg_plan") or PAIR_ROLES)
    plan_name(plan)
    return plan


def stream_cwd(stream: str) -> str | None:
    values = set()
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("type") == "system"
            and event.get("subtype") == "init"
        ):
            cwd = event.get("cwd")
            if isinstance(cwd, str) and cwd.startswith("/") and "\x00" not in cwd:
                values.add(posixpath.normpath(cwd))
    return next(iter(values)) if len(values) == 1 else None


def evidence_value(leg: Mapping[str, Any] | None, key: str) -> bool | None:
    if leg is None or leg.get("status") != "ok":
        return None
    evidence = leg.get("bd_evidence") or {}
    value = evidence.get(key)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"Nonboolean evidence {key}")
    return (
        None
        if value is False and key != "goal_action_success" and evidence.get("bd_evidence_unknown")
        else value
    )


def rescore_leg(
    task: ToolReqRealAgentTask, row: Mapping[str, Any] | None, *, leg: int, role: str
) -> BdLegEvidence | None:
    """The saved session scored again by today's scorer, from its transcript and receipts.
    ``None`` when the session did not finish, names no single working directory, or lacks the
    receipt identity that receipt scoring needs; the caller reads ``None`` as unknown."""
    if row is None or row.get("status") != "ok":
        return None
    stream = str(row.get("stream", ""))
    cwd = stream_cwd(stream)
    receipts, leg_id = row.get("bd_receipts"), row.get("bd_receipt_leg_id")
    if cwd is None or not isinstance(receipts, list) or not isinstance(leg_id, str) or not leg_id:
        return None
    return score_bd_leg(
        task,
        tool_calls_from_stream(stream),
        leg=leg,
        role=role,
        status="ok",
        config_dir=None,
        cwd=cwd,
        receipts=receipts,
        expected_leg_id=leg_id,
    )


def goal_audit(
    task: ToolReqRealAgentTask,
    goal: Mapping[str, Any] | None,
    *,
    rescored: BdLegEvidence | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "strict_artifact_success": None,
        "strict_observed_recall": None,
        "strict_recall_before_action": None,
        "qualifying_write_ids": [],
        "write_checks": [],
        "audit_unknown_reasons": [],
        "cwd": None,
    }
    if goal is None or goal.get("status") != "ok":
        return {**result, "audit_unknown_reasons": ["missing_or_unmeasured_goal"]}
    stream = str(goal.get("stream", ""))
    cwd = stream_cwd(stream)
    if cwd is None:
        return {**result, "audit_unknown_reasons": ["missing_or_ambiguous_init_cwd"]}
    calls = tool_calls_from_stream(stream)
    forbidden = tuple(
        value
        for check in task.goal_step.outcome_checks
        for action in check.requires_action
        for value in action.forbidden_values
    )
    reasons = {
        index: write_reason(call, cwd=cwd, required=task.current_opaque_values, forbidden=forbidden)
        for index, call in enumerate(calls)
        if call.name == "Write"
    }
    qualifying = [call for index, call in enumerate(calls) if reasons.get(index) == "qualifies"]
    result = {
        **result,
        "cwd": cwd,
        "strict_artifact_success": bool(qualifying),
        "strict_recall_before_action": None if qualifying else False,
        "qualifying_write_ids": [call.tool_use_id for call in qualifying],
        "write_checks": [
            {
                "tool_use_id": calls[index].tool_use_id,
                "tool_use_index": calls[index].tool_use_index,
                "reason": reason,
            }
            for index, reason in reasons.items()
        ],
    }
    if rescored is None:
        return {**result, "audit_unknown_reasons": ["missing_receipt_observation_identity"]}
    observed_leg = {"status": "ok", "bd_evidence": rescored.model_dump()}
    return {
        **result,
        "strict_observed_recall": evidence_value(observed_leg, "bd_recall_complete"),
        "strict_recall_before_action": (
            evidence_value(observed_leg, "bd_recall_before_action") if qualifying else False
        ),
        "audit_unknown_reasons": list(rescored.bd_evidence_unknown_reasons),
    }


def saved_evidence(legs: Mapping[str, Mapping[str, Any]]) -> dict[str, BdLegEvidence]:
    """The scores the runner saved beside each session, as the analyzer reads them."""
    return {
        role: BdLegEvidence.model_validate(leg["bd_evidence"])
        for role, leg in legs.items()
        if isinstance(leg.get("bd_evidence"), dict)
    }


def read_object(path: Path, hashes: dict[str, str], root: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    hashes[str(path.relative_to(root))] = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def load_pair(
    root: Path,
    pair: Mapping[str, Any],
    manifest: Mapping[str, Any],
    hashes: dict[str, str],
    plan: Sequence[str],
) -> dict[str, dict[str, Any]]:
    directory = _pair_dir(root, pair)
    for name in ("started.json", "cell.json"):
        path = directory / name
        if path.exists():
            saved = read_object(path, hashes, root)
            if saved.get("pair") != pair or saved.get("manifest_digest") != digest(manifest):
                raise ValueError(f"Pair identity mismatch: {path}")
    legs: dict[str, dict[str, Any]] = {}
    for path in sorted((directory / "legs").glob("*.json")):
        leg = read_object(path, hashes, root)
        role = leg.get("role")
        if not isinstance(role, str) or role not in plan or role in legs:
            raise ValueError(f"Unexpected or duplicate leg: {path}")
        if leg.get("leg") != plan.index(role) or any(
            leg.get(key) != pair[key] for key in ("work_id", "variant")
        ):
            raise ValueError(f"Leg identity mismatch: {path}")
        legs[role] = leg
    return legs


def summarize(values: Sequence[bool | None]) -> dict[str, Any]:
    success, failure = sum(v is True for v in values), sum(v is False for v in values)
    unknown = len(values) - success - failure
    return {
        "success": success,
        "failure": failure,
        "unknown": unknown,
        "scheduled": len(values),
        "scheduled_rate_bounds": (
            [success / len(values), (success + unknown) / len(values)] if values else [None, None]
        ),
    }


def categorize(values: Sequence[str | None]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for value in values:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    return {
        **dict(sorted(counts.items())),
        "unknown": sum(value is None for value in values),
        "scheduled": len(values),
    }


def trial_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every trial verdict, saved beside rescored, over ``rows``."""
    return {
        key: {
            source: (
                categorize([row[f"trial_{source}"][key] for row in rows])
                if key in TRIAL_CATEGORICAL
                else summarize([row[f"trial_{source}"][key] for row in rows])
            )
            for source in ("saved", "rescored")
        }
        for key in TRIAL_VERDICTS
    }


def trial_disagreements(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pairs whose rescored trial verdicts differ from the saved ones, and on which verdicts."""
    out = []
    for row in rows:
        moved = {
            key: {"saved": row["trial_saved"][key], "rescored": row["trial_rescored"][key]}
            for key in TRIAL_VERDICTS
            if row["trial_saved"][key] != row["trial_rescored"][key]
        }
        if moved:
            out.append(
                {
                    **{k: row[k] for k in ("condition", "work_id", "variant", "repeat")},
                    "moved": moved,
                }
            )
    return out


def disagreements(rows: Sequence[dict[str, Any]], saved: str, strict: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row[saved] is not None and row[strict] is not None and row[saved] != row[strict]
    ]


def audit(root: Path, *, corpus_dir: Path = DEFAULT_CORPUS) -> dict[str, Any]:
    hashes: dict[str, str] = {}
    manifest = read_object(root / "manifest.json", hashes, root)
    plan = leg_plan(manifest)
    # Capture is read from the leg shown the current values: the one just before the goal.
    capture_role = plan[plan.index(GOAL_ROLE) - 1]
    schedule = manifest["schedule"]
    if len({digest(pair) for pair in schedule}) != len(schedule):
        raise ValueError("Duplicate scheduled pair")
    _, corpus = load_twin_corpus(corpus_dir)
    identities = {(p["work_id"], p["variant"]) for p in schedule}
    tasks = [task for task in corpus if (task.work_id, task.variant) in identities]
    if len(tasks) != len(identities) or corpus_fingerprint(tasks) != manifest["corpus_fingerprint"]:
        raise ValueError("Frozen task corpus identity differs from audit corpus")
    indexed = {(task.work_id, task.variant): task for task in tasks}
    rows = []
    for pair in schedule:
        legs = load_pair(root, pair, manifest, hashes, plan)
        task = indexed[(pair["work_id"], pair["variant"])]
        rescored = {
            role: evidence
            for index, role in enumerate(plan)
            if (evidence := rescore_leg(task, legs.get(role), leg=index, role=role)) is not None
        }
        goal = legs.get(GOAL_ROLE)
        observed = goal_audit(task, goal, rescored=rescored.get(GOAL_ROLE))
        capture = evidence_value(legs.get(capture_role), "bd_capture_complete")
        parts = [
            capture,
            observed["strict_artifact_success"],
            observed["strict_observed_recall"],
            observed["strict_recall_before_action"],
        ]
        saved = (goal or {}).get("bd_evidence") or {}
        saved_parts = [
            capture,
            evidence_value(goal, "goal_action_success"),
            evidence_value(goal, "bd_recall_complete"),
            evidence_value(goal, "bd_recall_before_action"),
        ]
        rows.append(
            {
                **pair,
                **observed,
                "saved_goal_action_success": saved.get("goal_action_success"),
                "saved_recall_before_action": evidence_value(goal, "bd_recall_before_action"),
                "saved_handoff": (
                    False if False in saved_parts else (True if all(saved_parts) else None)
                ),
                "capture_role": capture_role,
                "capture": capture,
                "strict_handoff": False if False in parts else (True if all(parts) else None),
                "legs_rescored": list(rescored),
                "rescored_legs": {role: ev.model_dump() for role, ev in rescored.items()},
                **(
                    {
                        "trial_saved": trial_outcomes(saved_evidence(legs)),
                        "trial_rescored": trial_outcomes(rescored),
                    }
                    if plan == TRIAL_ROLES
                    else {}
                ),
            }
        )
    trial = plan == TRIAL_ROLES
    groups = []
    for condition, variant in sorted({(row["condition"], row["variant"]) for row in rows}):
        selected = [r for r in rows if r["condition"] == condition and r["variant"] == variant]
        groups.append(
            {
                "condition": condition,
                "variant": variant,
                **{
                    key: summarize([row[key] for row in selected])
                    for key in ("strict_artifact_success", "strict_handoff")
                },
                "legs_rescored": sum(len(row["legs_rescored"]) for row in selected),
                "legs_scheduled": len(plan) * len(selected),
                **({"trial": trial_summary(selected)} if trial else {}),
            }
        )
    return {
        "audit_version": AUDIT_VERSION,
        "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scoring_source_fingerprint": source_fingerprint(),
        "manifest_digest": digest(manifest),
        "manifest": manifest,
        "leg_plan": list(plan),
        "artifact_sha256": hashes,
        "scheduled_pairs": len(rows),
        "groups": groups,
        "pairs": rows,
        "discrepancies": disagreements(
            rows, "saved_goal_action_success", "strict_artifact_success"
        ),
        "recall_before_action_discrepancies": disagreements(
            rows, "saved_recall_before_action", "strict_recall_before_action"
        ),
        "handoff_discrepancies": disagreements(rows, "saved_handoff", "strict_handoff"),
        **({"trial_discrepancies": trial_disagreements(rows)} if trial else {}),
        "limitations": [
            "Post hoc mechanical audit, not the frozen primary endpoint.",
            "Rescored evidence is today's scorer over the saved transcript and receipts; "
            "it passes no config dir, so native-memory counters are not compared.",
            "Trial verdicts: saved reads the scores written at run time, rescored reads today's "
            "scorer; a moved verdict is a scorer change, not new agent behaviour.",
            "Success witnesses a qualifying acknowledged Write, "
            "not final on-disk state after later mutations.",
            "JSON string values must contain required tokens; keys/filenames do not qualify.",
            "No semantic all-fields judge; lexical paths do not resolve sandbox symlinks.",
            "Unknown/missing goals remain in full-schedule bounds; original evidence is unchanged.",
        ],
    }


def _shown(summary: Mapping[str, Any], key: str) -> str:
    if key in TRIAL_CATEGORICAL:
        named = ", ".join(
            f"{name} {count}"
            for name, count in summary.items()
            if name not in ("unknown", "scheduled")
        )
        return f"{named or 'none'}; unknown {summary['unknown']}"
    return " / ".join(str(summary[name]) for name in ("success", "failure", "unknown"))


def _trial_lines(report: Mapping[str, Any]) -> list[str]:
    """One row per group and verdict: the verdict as saved at run time beside the rescore."""
    lines = [
        "",
        f"Trial verdicts, saved at run time beside rescored by today's scorer. "
        f"Sessions rescored: "
        f"{sum(g['legs_rescored'] for g in report['groups'])} of "
        f"{sum(g['legs_scheduled'] for g in report['groups'])}. "
        f"Pairs whose verdicts moved: {len(report['trial_discrepancies'])}.",
        "",
        "| Condition | Variant | Verdict | Saved | Rescored |",
        "|---|---|---|---|---|",
    ]
    for group in report["groups"]:
        for key in TRIAL_VERDICTS:
            saved, rescored = (_shown(group["trial"][key][s], key) for s in ("saved", "rescored"))
            lines.append(
                f"| {group['condition']} | {group['variant']} | {key} | {saved} | {rescored} |"
            )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS)
    args = parser.parse_args(argv)
    report = audit(args.experiment, corpus_dir=args.corpus_dir)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "audit.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    lines = [
        "# Mechanical action audit",
        "",
        f"Scheduled pairs: {report['scheduled_pairs']}.",
        "",
        "| Condition | Variant | Qualifying Write yes / no / unknown "
        "| Strict handoff yes / no / unknown |",
        "|---|---|---:|---:|",
    ]
    for group in report["groups"]:
        values = [
            " / ".join(str(group[key][name]) for name in ("success", "failure", "unknown"))
            for key in ("strict_artifact_success", "strict_handoff")
        ]
        lines.append(f"| {group['condition']} | {group['variant']} | {values[0]} | {values[1]} |")
    if tuple(report["leg_plan"]) == TRIAL_ROLES:
        lines += _trial_lines(report)
    (args.out / "report.md").write_text("\n".join([*lines, "", *report["limitations"], ""]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
