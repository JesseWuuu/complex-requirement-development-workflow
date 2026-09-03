#!/usr/bin/env python3

"""Validate the compact cross-conversation state without replaying workflow history."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml


SHA256_LENGTH = 64
ARTIFACT_PHASES = {"spec": 3, "test": 4, "implementation": 5}
ARTIFACT_STATUSES = {"not_started", "awaiting_approval", "approved", "reopened"}
TOP_STATUSES = {"in_progress", "awaiting_approval", "blocked", "complete", "invalidated"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(encoded)


def is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != SHA256_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"state does not exist: {path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"state cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("state must be a YAML mapping")
    return value


def resolve_path(raw: Any, base: Path) -> Path | None:
    if not is_nonempty_string(raw):
        return None
    candidate = Path(str(raw).strip()).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def file_sha256(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


def add_issue(
    target: list[dict[str, Any]],
    *,
    kind: str,
    phase: int,
    reason: str,
    path: str | None = None,
    expected: Any = None,
    actual: Any = None,
) -> None:
    item: dict[str, Any] = {"kind": kind, "phase": phase, "reason": reason}
    if path is not None:
        item["path"] = path
    if expected is not None:
        item["expected"] = expected
    if actual is not None:
        item["actual"] = actual
    target.append(item)


def validate_state(state: dict[str, Any], state_path: Path) -> tuple[int, dict[str, Any]]:
    structural: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    base = state_path.parent

    if state.get("schema") != "requirement-spec/v2":
        add_issue(
            structural,
            kind="schema",
            phase=1,
            reason="state.schema must be 'requirement-spec/v2'; migrate v1 from current artifacts and approvals",
        )

    current_phase = state.get("current_phase")
    if not isinstance(current_phase, int) or current_phase not in range(1, 8):
        add_issue(structural, kind="current_phase", phase=1, reason="current_phase must be 1..7")
        current_phase = 1
    if state.get("status") not in TOP_STATUSES:
        add_issue(structural, kind="status", phase=current_phase, reason="top-level status is invalid")
    if not is_nonempty_string(state.get("feature_slug")):
        add_issue(structural, kind="feature_slug", phase=1, reason="feature_slug is required")
    if not is_nonempty_string(state.get("next_action")):
        add_issue(structural, kind="next_action", phase=current_phase, reason="next_action is required")
    if not isinstance(state.get("open_blockers"), list):
        add_issue(structural, kind="open_blockers", phase=current_phase, reason="open_blockers must be a list")

    inputs = state.get("inputs")
    if not isinstance(inputs, dict):
        add_issue(structural, kind="inputs", phase=1, reason="inputs must be a mapping")
        inputs = {}

    grounding_lines: list[str] = []
    prd = inputs.get("prd")
    if not isinstance(prd, dict):
        add_issue(structural, kind="prd", phase=1, reason="inputs.prd must be a mapping")
        prd = {}
    prd_raw = prd.get("path")
    prd_sha = prd.get("sha256")
    prd_path = resolve_path(prd_raw, base)
    if prd_path is None or not is_sha256(prd_sha):
        add_issue(structural, kind="prd", phase=1, reason="PRD needs path and sha256")
    else:
        actual = file_sha256(prd_path)
        if actual != prd_sha:
            add_issue(
                drift,
                kind="prd",
                phase=1,
                reason="PRD content no longer matches the saved input",
                path=str(prd_path),
                expected=prd_sha,
                actual=actual or "missing",
            )
        grounding_lines.append(f"prd | {str(prd_raw).strip()} |  | {prd_sha}")

    project_rules = inputs.get("project_rules", [])
    if not isinstance(project_rules, list):
        add_issue(structural, kind="project_rules", phase=2, reason="project_rules must be a list")
        project_rules = []
    for index, item in enumerate(project_rules):
        if not isinstance(item, dict):
            add_issue(structural, kind="project_rule", phase=2, reason=f"project_rules[{index}] must be a mapping")
            continue
        raw_path = item.get("path")
        stored_sha = item.get("sha256")
        role = item.get("role")
        path = resolve_path(raw_path, base)
        if path is None or not is_sha256(stored_sha) or not is_nonempty_string(role):
            add_issue(structural, kind="project_rule", phase=2, reason=f"project_rules[{index}] needs path, role, and sha256")
            continue
        actual = file_sha256(path)
        if actual != stored_sha:
            add_issue(
                drift,
                kind="project_rule",
                phase=2,
                reason="project rule content changed",
                path=str(path),
                expected=stored_sha,
                actual=actual or "missing",
            )
        grounding_lines.append(f"{role.strip()} | {str(raw_path).strip()} |  | {stored_sha}")

    if not isinstance(inputs.get("design_targets", []), list):
        add_issue(structural, kind="design_targets", phase=2, reason="design_targets must be a list")

    direction = state.get("direction")
    if not isinstance(direction, dict):
        add_issue(structural, kind="direction", phase=1, reason="direction must be a mapping")
        direction = {}
    direction_status = direction.get("status")
    if direction_status not in {"not_started", "awaiting_approval", "approved", "superseded", "reopened"}:
        add_issue(structural, kind="direction", phase=1, reason="direction status is invalid")
    if direction_status in {"awaiting_approval", "approved"} and not is_nonempty_string(direction.get("summary")):
        add_issue(structural, kind="direction", phase=1, reason="active direction needs a concise summary")
    if direction_status == "approved" and (
        not is_nonempty_string(direction.get("approved_at"))
        or not is_nonempty_string(direction.get("approval_note"))
    ):
        add_issue(structural, kind="direction", phase=1, reason="approved direction needs time and approval_note")

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        add_issue(structural, kind="artifacts", phase=3, reason="artifacts must be a mapping")
        artifacts = {}
    artifact_values: dict[str, dict[str, Any]] = {}
    for name, phase in ARTIFACT_PHASES.items():
        artifact = artifacts.get(name)
        if not isinstance(artifact, dict):
            add_issue(structural, kind=name, phase=phase, reason=f"artifacts.{name} must be a mapping")
            artifact = {}
        artifact_values[name] = artifact
        status = artifact.get("status")
        if status not in ARTIFACT_STATUSES:
            add_issue(structural, kind=name, phase=phase, reason=f"{name} status is invalid")
            continue
        if status in {"awaiting_approval", "approved"}:
            raw_path = artifact.get("path")
            stored_sha = artifact.get("sha256")
            path = resolve_path(raw_path, base)
            if path is None or not is_sha256(stored_sha):
                add_issue(structural, kind=name, phase=phase, reason=f"{name} needs path and sha256")
            else:
                actual = file_sha256(path)
                if actual != stored_sha:
                    add_issue(
                        drift,
                        kind=name,
                        phase=phase,
                        reason=f"{name} content changed",
                        path=str(path),
                        expected=stored_sha,
                        actual=actual or "missing",
                    )
            if status == "approved" and (
                not is_nonempty_string(artifact.get("approved_at"))
                or not is_nonempty_string(artifact.get("approval_note"))
            ):
                add_issue(structural, kind=name, phase=phase, reason=f"approved {name} needs time and approval_note")

    if artifact_values.get("test", {}).get("status") == "approved" and artifact_values.get("spec", {}).get("status") != "approved":
        add_issue(structural, kind="approval_order", phase=3, reason="approved test requires approved spec")
    if artifact_values.get("implementation", {}).get("status") == "approved" and artifact_values.get("test", {}).get("status") != "approved":
        add_issue(structural, kind="approval_order", phase=4, reason="approved implementation requires approved test")

    implementation_result = state.get("implementation_result")
    if not isinstance(implementation_result, dict):
        add_issue(structural, kind="implementation_result", phase=7, reason="implementation_result must be a mapping")
        implementation_result = {}
    changes = implementation_result.get("changes", [])
    if not isinstance(changes, list):
        add_issue(structural, kind="implementation_changes", phase=7, reason="implementation_result.changes must be a list")
        changes = []

    change_by_path: dict[Path, dict[str, Any]] = {}
    output_lines: list[str] = []
    for index, item in enumerate(changes):
        if not isinstance(item, dict):
            add_issue(structural, kind="implementation_change", phase=7, reason=f"changes[{index}] must be a mapping")
            continue
        raw_path = item.get("path")
        path = resolve_path(raw_path, base)
        kind = item.get("kind")
        role = item.get("role")
        stored_sha = item.get("sha256")
        if path is None or kind not in {"present", "removed"} or not is_nonempty_string(role):
            add_issue(structural, kind="implementation_change", phase=7, reason=f"changes[{index}] needs path, kind, and role")
            continue
        if path in change_by_path:
            add_issue(structural, kind="implementation_change", phase=7, reason=f"duplicate implementation change: {path}")
            continue
        if kind == "present" and not is_sha256(stored_sha):
            add_issue(structural, kind="implementation_change", phase=7, reason=f"present change needs sha256: {path}")
            continue
        if kind == "removed" and stored_sha is not None:
            add_issue(structural, kind="implementation_change", phase=7, reason=f"removed change sha256 must be null: {path}")
            continue
        change_by_path[path] = item
        output_lines.append(f"{kind} | {str(raw_path).strip()} | {role.strip()} | {stored_sha or ''}")

    authorization = state.get("implementation_authorization")
    if not isinstance(authorization, dict):
        add_issue(structural, kind="implementation_authorization", phase=7, reason="implementation_authorization must be a mapping")
        authorization = {}
    authorization_status = authorization.get("status")
    if authorization_status not in {"not_granted", "granted", "invalidated"}:
        add_issue(structural, kind="implementation_authorization", phase=7, reason="implementation authorization status is invalid")

    result_status = implementation_result.get("status")
    if result_status not in {"not_started", "in_progress", "verified", "invalidated"}:
        add_issue(structural, kind="implementation_result", phase=7, reason="implementation result status is invalid")

    grounding = state.get("repo_grounding")
    if not isinstance(grounding, dict):
        add_issue(structural, kind="repo_grounding", phase=2, reason="repo_grounding must be a mapping")
        grounding = {}
    grounding_status = grounding.get("status")
    if grounding_status not in {"not_started", "current", "invalidated"}:
        add_issue(structural, kind="repo_grounding", phase=2, reason="repo_grounding status is invalid")
    relevant_targets = grounding.get("relevant_targets", [])
    if not isinstance(relevant_targets, list):
        add_issue(structural, kind="relevant_targets", phase=2, reason="relevant_targets must be a list")
        relevant_targets = []
    for index, item in enumerate(relevant_targets):
        if not isinstance(item, dict):
            add_issue(structural, kind="relevant_target", phase=2, reason=f"relevant_targets[{index}] must be a mapping")
            continue
        raw_path = item.get("path")
        path = resolve_path(raw_path, base)
        role = item.get("role")
        symbol = item.get("symbol") or ""
        stored_sha = item.get("sha256")
        if path is None or not is_nonempty_string(role) or not is_sha256(stored_sha):
            add_issue(structural, kind="relevant_target", phase=2, reason=f"relevant_targets[{index}] needs path, role, and sha256")
            continue
        grounding_lines.append(f"{role.strip()} | {str(raw_path).strip()} | {str(symbol).strip()} | {stored_sha}")
        actual = file_sha256(path)
        expected_output = change_by_path.get(path)
        output_is_authorized = (
            authorization_status == "granted"
            and result_status in {"in_progress", "verified"}
            and authorization.get("plan_fingerprint_sha256")
            == state.get("plan_fingerprint_sha256")
            and implementation_result.get("plan_fingerprint_sha256")
            == state.get("plan_fingerprint_sha256")
            and expected_output is not None
            and (
                (expected_output.get("kind") == "present" and actual == expected_output.get("sha256"))
                or (expected_output.get("kind") == "removed" and actual is None)
            )
        )
        if actual != stored_sha and not output_is_authorized:
            add_issue(
                drift,
                kind="relevant_target",
                phase=2,
                reason="repository grounding target changed outside the registered implementation result",
                path=str(path),
                expected=stored_sha,
                actual=actual or "missing",
            )

    computed_grounding = sha256_text("\n".join(sorted(grounding_lines))) if grounding_lines else None
    stored_grounding = grounding.get("fingerprint_sha256")
    if grounding_status == "current":
        if not is_sha256(stored_grounding) or stored_grounding != computed_grounding:
            add_issue(
                errors,
                kind="grounding_fingerprint",
                phase=2,
                reason="stored grounding fingerprint does not match its recorded inputs",
                expected=computed_grounding,
                actual=stored_grounding,
            )
        evidence_pack = grounding.get("evidence_pack")
        if evidence_pack is not None and not isinstance(evidence_pack, dict):
            add_issue(structural, kind="evidence_pack", phase=2, reason="evidence_pack must be a mapping or null")
        elif isinstance(evidence_pack, dict) and is_nonempty_string(evidence_pack.get("path")):
            pack_path = resolve_path(evidence_pack.get("path"), base)
            pack_sha = evidence_pack.get("sha256")
            if pack_path is None or not is_sha256(pack_sha):
                add_issue(structural, kind="evidence_pack", phase=2, reason="evidence pack needs path and sha256")
            else:
                actual = file_sha256(pack_path)
                if actual != pack_sha:
                    add_issue(
                        drift,
                        kind="evidence_pack",
                        phase=2,
                        reason="grounding pack content changed",
                        path=str(pack_path),
                        expected=pack_sha,
                        actual=actual or "missing",
                    )
                if evidence_pack.get("input_fingerprint_sha256") != stored_grounding:
                    add_issue(errors, kind="evidence_pack_binding", phase=2, reason="grounding pack is bound to another grounding fingerprint")

    approved = all(artifact_values.get(name, {}).get("status") == "approved" for name in ARTIFACT_PHASES)
    expected_plan: str | None = None
    if approved and grounding_status == "current" and is_sha256(stored_grounding):
        expected_plan = canonical_sha256(
            {
                "grounding": stored_grounding,
                "implementation": artifact_values["implementation"].get("sha256"),
                "spec": artifact_values["spec"].get("sha256"),
                "test": artifact_values["test"].get("sha256"),
            }
        )
    stored_plan = state.get("plan_fingerprint_sha256")
    if expected_plan is not None and stored_plan != expected_plan:
        add_issue(errors, kind="plan_fingerprint", phase=6, reason="plan fingerprint does not match approved artifacts and grounding", expected=expected_plan, actual=stored_plan)
    if expected_plan is None and stored_plan is not None:
        add_issue(errors, kind="plan_fingerprint", phase=6, reason="plan fingerprint must be null until all planning inputs are approved and current")

    consistency = state.get("consistency_review")
    if not isinstance(consistency, dict):
        add_issue(structural, kind="consistency_review", phase=6, reason="consistency_review must be a mapping")
        consistency = {}
    consistency_status = consistency.get("status")
    if consistency_status not in {"not_started", "in_progress", "completed", "passed", "blocked", "invalidated"}:
        add_issue(structural, kind="consistency_review", phase=6, reason="consistency review status is invalid")
    validate_review(
        consistency,
        status=consistency_status,
        completed_statuses={"completed", "passed"},
        expected_input=expected_plan,
        input_key="plan_fingerprint_sha256",
        kind="consistency_review",
        phase=6,
        structural=structural,
        errors=errors,
    )

    revision = consistency.get("revision_decision")
    if not isinstance(revision, dict):
        add_issue(structural, kind="revision_decision", phase=6, reason="revision_decision must be a mapping")
    else:
        revision_status = revision.get("status")
        if revision_status not in {"not_required", "awaiting_decision", "authorized", "declined", "invalidated"}:
            add_issue(structural, kind="revision_decision", phase=6, reason="revision decision status is invalid")
        if revision_status in {"awaiting_decision", "authorized", "declined"}:
            if revision.get("review_conclusion_sha256") != consistency.get("conclusion_sha256"):
                add_issue(errors, kind="revision_decision", phase=6, reason="revision decision is not bound to the current review conclusion")
            if not isinstance(revision.get("artifacts"), list) or not revision.get("artifacts"):
                add_issue(structural, kind="revision_decision", phase=6, reason="revision decision needs affected artifacts")
        if revision_status in {"authorized", "declined"} and (
            not is_nonempty_string(revision.get("decided_at"))
            or not is_nonempty_string(revision.get("decision_note"))
        ):
            add_issue(structural, kind="revision_decision", phase=6, reason="decided revision needs time and decision_note")

    if authorization_status == "granted":
        if consistency_status != "passed":
            add_issue(errors, kind="implementation_authorization", phase=7, reason="implementation authorization requires a passed consistency review")
        if expected_plan is None or authorization.get("plan_fingerprint_sha256") != expected_plan:
            add_issue(errors, kind="implementation_authorization", phase=7, reason="implementation authorization is bound to another plan")
        if not is_nonempty_string(authorization.get("granted_at")) or not is_nonempty_string(authorization.get("approval_note")):
            add_issue(structural, kind="implementation_authorization", phase=7, reason="granted authorization needs time and approval_note")

    verification = implementation_result.get("verification", [])
    if not isinstance(verification, list):
        add_issue(structural, kind="verification", phase=7, reason="implementation_result.verification must be a list")
        verification = []
    for index, item in enumerate(verification):
        if not isinstance(item, dict) or not is_nonempty_string(item.get("check")) or item.get("result") not in {"passed", "failed", "blocked"} or not is_nonempty_string(item.get("evidence")):
            add_issue(structural, kind="verification", phase=7, reason=f"verification[{index}] needs check, valid result, and evidence")

    computed_output = sha256_text("\n".join(sorted(output_lines))) if output_lines else None
    stored_output = implementation_result.get("output_fingerprint_sha256")
    if result_status in {"in_progress", "verified"}:
        if authorization_status != "granted" or expected_plan is None or implementation_result.get("plan_fingerprint_sha256") != expected_plan:
            add_issue(errors, kind="implementation_result", phase=7, reason="implementation result is not bound to an active authorization and current plan")
        for path, change in change_by_path.items():
            actual = file_sha256(path)
            expected_sha = change.get("sha256")
            if change.get("kind") == "present" and actual != expected_sha:
                add_issue(drift, kind="implementation_change", phase=7, reason="registered output changed", path=str(path), expected=expected_sha, actual=actual or "missing")
            if change.get("kind") == "removed" and actual is not None:
                add_issue(drift, kind="implementation_change", phase=7, reason="registered removed output exists again", path=str(path), expected="missing", actual=actual)
    if result_status == "verified":
        if implementation_result.get("output_manifest_complete") is not True or not changes:
            add_issue(errors, kind="implementation_result", phase=7, reason="verified result needs a complete non-empty change manifest")
        if computed_output is None or stored_output != computed_output:
            add_issue(errors, kind="output_fingerprint", phase=7, reason="output fingerprint does not match registered changes", expected=computed_output, actual=stored_output)
        if not verification or any(item.get("result") != "passed" for item in verification if isinstance(item, dict)):
            add_issue(errors, kind="verification", phase=7, reason="verified result needs at least one passed verification and no failures")
        if not is_nonempty_string(implementation_result.get("completed_at")):
            add_issue(structural, kind="implementation_result", phase=7, reason="verified result needs completed_at")

    expected_post_input: str | None = None
    if result_status == "verified" and expected_plan is not None and is_sha256(stored_output):
        expected_post_input = canonical_sha256(
            {"output": stored_output, "plan": expected_plan, "verification": verification}
        )

    post_review = state.get("post_implementation_review")
    if not isinstance(post_review, dict):
        add_issue(structural, kind="post_implementation_review", phase=7, reason="post_implementation_review must be a mapping")
        post_review = {}
    post_status = post_review.get("status")
    if post_status not in {"not_started", "in_progress", "completed", "blocked", "invalidated"}:
        add_issue(structural, kind="post_implementation_review", phase=7, reason="post-implementation review status is invalid")
    validate_review(
        post_review,
        status=post_status,
        completed_statuses={"completed"},
        expected_input=expected_post_input,
        input_key="input_fingerprint_sha256",
        kind="post_implementation_review",
        phase=7,
        structural=structural,
        errors=errors,
    )

    if state.get("status") == "complete" and (result_status != "verified" or post_status != "completed"):
        add_issue(errors, kind="workflow_complete", phase=7, reason="complete workflow requires verified implementation and completed terminal review")
    if post_status == "completed" and state.get("status") != "complete":
        add_issue(errors, kind="workflow_complete", phase=7, reason="completed terminal review must close the workflow")

    all_issues = [*structural, *errors, *drift]
    earliest = min((item["phase"] for item in all_issues), default=current_phase)
    payload = {
        "ok": not all_issues,
        "schema": state.get("schema"),
        "feature_slug": state.get("feature_slug"),
        "current_phase": current_phase,
        "resume_mode": "fast_path" if not all_issues else "targeted_revalidation",
        "earliest_candidate_phase": earliest,
        "drift": drift,
        "errors": [*structural, *errors],
    }
    if structural:
        return 2, payload
    if errors or drift:
        return 1, payload
    return 0, payload


def validate_review(
    review: dict[str, Any],
    *,
    status: Any,
    completed_statuses: set[str],
    expected_input: str | None,
    input_key: str,
    kind: str,
    phase: int,
    structural: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> None:
    attempt = review.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt not in range(0, 3):
        add_issue(structural, kind=kind, phase=phase, reason="review attempt must be 0, 1, or 2")
        return
    active = status in {"in_progress", "blocked", *completed_statuses}
    if active and (expected_input is None or review.get(input_key) != expected_input):
        add_issue(errors, kind=f"{kind}_binding", phase=phase, reason="review is bound to another input fingerprint")
    if active and attempt == 0:
        add_issue(structural, kind=kind, phase=phase, reason="active review needs attempt 1 or 2")
    if status == "in_progress" and not is_nonempty_string(review.get("reviewer_ref")):
        add_issue(structural, kind=kind, phase=phase, reason="in-progress review needs reviewer_ref")
    if status in completed_statuses:
        conclusion = review.get("conclusion")
        conclusion_sha = review.get("conclusion_sha256")
        if review.get("outcome") not in {"findings", "no_findings"}:
            add_issue(structural, kind=kind, phase=phase, reason="completed review needs findings or no_findings outcome")
        if not is_nonempty_string(conclusion) or not is_sha256(conclusion_sha) or sha256_text(str(conclusion)) != conclusion_sha:
            add_issue(structural, kind=kind, phase=phase, reason="completed review needs a valid conclusion and conclusion_sha256")
        if not is_nonempty_string(review.get("completed_at")):
            add_issue(structural, kind=kind, phase=phase, reason="completed review needs completed_at")
    if status == "blocked" and attempt != 2:
        add_issue(structural, kind=kind, phase=phase, reason="blocked review requires two failed attempts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="path to requirement-spec/v2 state.yaml")
    args = parser.parse_args()
    state_path = args.state.expanduser().resolve()
    try:
        state = load_yaml(state_path)
    except ValueError as exc:
        print(json.dumps({"ok": False, "resume_mode": "targeted_revalidation", "errors": [{"kind": "state", "phase": 1, "reason": str(exc)}]}, ensure_ascii=False, indent=2))
        return 2
    code, payload = validate_state(state, state_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
