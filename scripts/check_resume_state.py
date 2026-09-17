#!/usr/bin/env python3

"""Validate the compact cross-conversation state without replaying workflow history."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


SHA256_LENGTH = 64
ARTIFACT_PHASES = {"spec": 3, "test": 4, "implementation": 5}
ARTIFACT_STATUSES = {"not_started", "awaiting_approval", "approved", "reopened"}
TOP_STATUSES = {"in_progress", "awaiting_approval", "blocked", "complete", "invalidated"}
COMPLETED_IMPLEMENTATION_STATUSES = {"implemented", "verified"}


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


def read_state_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"state does not exist: {path}") from exc
    except OSError as exc:
        raise ValueError(f"state cannot be read: {exc}") from exc


def load_yaml(path: Path, *, source_bytes: bytes | None = None) -> dict[str, Any]:
    try:
        raw = read_state_bytes(path) if source_bytes is None else source_bytes
        value = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
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


def validate_state(
    state: dict[str, Any], state_path: Path, *, include_fingerprints: bool = False
) -> tuple[int, dict[str, Any]]:
    structural: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    base = state_path.parent
    file_digests: dict[str, str | None] = {}

    def current_sha256(path: Path) -> str | None:
        key = str(path)
        if key not in file_digests:
            file_digests[key] = file_sha256(path)
        return file_digests[key]

    legacy = state.get("schema") == "requirement-spec/v2"
    legacy_complete = legacy and state.get("status") == "complete"
    if state.get("schema") not in {"requirement-spec/v2", "requirement-spec/v3"}:
        add_issue(
            structural,
            kind="schema",
            phase=1,
            reason="state.schema must be 'requirement-spec/v3'; migrate v1 from current artifacts and approvals",
        )

    current_phase = state.get("current_phase")
    if not isinstance(current_phase, int) or current_phase not in range(1, 8):
        add_issue(structural, kind="current_phase", phase=1, reason="current_phase must be 1..7")
        current_phase = 1
    if legacy and not legacy_complete:
        add_issue(
            errors, kind="schema_migration", phase=current_phase,
            reason=("migrate v2 to v3 without losing current planning approvals; "
                    "unfinished phase seven requires reconstructing batches and verifying explicit user approvals; "
                    "old post-implementation review is not user batch approval"),
        )
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
        actual = current_sha256(prd_path)
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
        actual = current_sha256(path)
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
                actual = current_sha256(path)
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
    if result_status not in {"not_started", "in_progress", "invalidated", *COMPLETED_IMPLEMENTATION_STATUSES}:
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
        actual = current_sha256(path)
        expected_output = change_by_path.get(path)
        output_is_authorized = (
            authorization_status == "granted"
            and result_status in {"in_progress", *COMPLETED_IMPLEMENTATION_STATUSES}
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
                actual = current_sha256(pack_path)
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
        continuing_implementation = (
            current_phase == 7
            and result_status == "in_progress"
            and consistency_status == "passed"
            and authorization_status == "granted"
            and expected_plan is not None
            and stored_plan == expected_plan
            and authorization.get("plan_fingerprint_sha256") == expected_plan
            and implementation_result.get("plan_fingerprint_sha256") == expected_plan
            and any(isinstance(batch, dict) and batch.get("status") in {"in_progress", "revising"}
                    for batch in (implementation_result.get("batches")
                                  if isinstance(implementation_result.get("batches"), list) else []))
        )
        # A pending local revision pauses its dependants, not other authorized work.
        # Existing input, review and output checks still reject stale bindings.
        if revision_status == "awaiting_decision" and (
            consistency_status == "passed" or authorization_status == "granted"
        ) and not continuing_implementation:
            add_issue(errors, kind="revision_decision", phase=6, reason="pending revision decision permits only already-authorized, ongoing phase-seven work on the current plan within the current implementation batch")
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
    if result_status in {"in_progress", *COMPLETED_IMPLEMENTATION_STATUSES}:
        if authorization_status != "granted" or expected_plan is None or implementation_result.get("plan_fingerprint_sha256") != expected_plan:
            add_issue(errors, kind="implementation_result", phase=7, reason="implementation result is not bound to an active authorization and current plan")
        for path, change in change_by_path.items():
            actual = current_sha256(path)
            expected_sha = change.get("sha256")
            if change.get("kind") == "present" and actual != expected_sha:
                add_issue(drift, kind="implementation_change", phase=7, reason="registered output changed", path=str(path), expected=expected_sha, actual=actual or "missing")
            if change.get("kind") == "removed" and actual is not None:
                add_issue(drift, kind="implementation_change", phase=7, reason="registered removed output exists again", path=str(path), expected="missing", actual=actual)
    if result_status in COMPLETED_IMPLEMENTATION_STATUSES:
        if implementation_result.get("output_manifest_complete") is not True or not changes:
            add_issue(errors, kind="implementation_result", phase=7, reason="completed implementation needs a complete non-empty change manifest")
        if computed_output is None or stored_output != computed_output:
            add_issue(errors, kind="output_fingerprint", phase=7, reason="output fingerprint does not match registered changes", expected=computed_output, actual=stored_output)
        if not verification:
            add_issue(errors, kind="verification", phase=7, reason="completed implementation needs recorded verification results")
        if result_status == "verified" and any(item.get("result") != "passed" for item in verification if isinstance(item, dict)):
            add_issue(errors, kind="verification", phase=7, reason="verified result requires every verification to be passed; use implemented for failed or blocked checks")
        if not is_nonempty_string(implementation_result.get("completed_at")):
            add_issue(structural, kind="implementation_result", phase=7, reason="completed implementation needs completed_at")

    batch_fingerprints: list[dict[str, Any]] = []
    expected_post_input: str | None = None
    post_review: dict[str, Any] = {}
    if legacy_complete:
        if result_status in COMPLETED_IMPLEMENTATION_STATUSES and expected_plan is not None and is_sha256(stored_output):
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

        if state.get("status") == "complete" and (result_status not in COMPLETED_IMPLEMENTATION_STATUSES or post_status != "completed"):
            add_issue(errors, kind="workflow_complete", phase=7, reason="complete workflow requires completed implementation and completed terminal review; verification may contain failed or blocked checks")
    elif not legacy:
        batch_fingerprints = validate_batches(
            implementation_result, expected_plan=expected_plan, computed_output=computed_output,
            current_phase=current_phase, top_status=state.get("status"), base=base,
            structural=structural, errors=errors,
        )

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
    if include_fingerprints:
        # Include drafts whose digest has not been recorded yet. Aggregate values
        # above deliberately use saved inputs, never refreshed filesystem values.
        records = [
            prd, *project_rules, *artifact_values.values(), *relevant_targets,
            grounding.get("evidence_pack"), *changes,
        ]
        for record in records:
            if isinstance(record, dict):
                path = resolve_path(record.get("path"), base)
                if path is not None:
                    current_sha256(path)
        payload["fingerprints"] = {
            "files": file_digests,
            "recorded_inputs": {
                "repo_grounding.fingerprint_sha256": computed_grounding,
                "plan_fingerprint_sha256": expected_plan,
                "implementation_result.output_fingerprint_sha256": computed_output,
            },
            "review_conclusions": {
                f"{name}.conclusion_sha256": sha256_text(review["conclusion"])
                if is_nonempty_string(review.get("conclusion")) else None
                for name, review in (
                    ("consistency_review", consistency),
                )
            },
        }
        payload["fingerprints"]["implementation_batches"] = batch_fingerprints
        if legacy_complete:
            payload["fingerprints"]["recorded_inputs"]["post_implementation_review.input_fingerprint_sha256"] = expected_post_input
    if structural:
        return 2, payload
    if errors or drift:
        return 1, payload
    return 0, payload


def validate_batches(
    result: dict[str, Any], *, expected_plan: str | None, computed_output: str | None,
    current_phase: int, top_status: Any, base: Path,
    structural: list[dict[str, Any]], errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate durable user checkpoints, never rehash historical output from disk."""
    batches = result.get("batches")
    fingerprints: list[dict[str, Any]] = []

    def issue(kind: str, reason: str, *, malformed: bool = False) -> None:
        add_issue(structural if malformed else errors, kind=kind, phase=7, reason=reason)

    if not isinstance(batches, list):
        issue("implementation_batches", "implementation_result.batches must be an ordered list", malformed=True)
        return fingerprints
    result_status = result.get("status")
    active = result_status in {"in_progress", *COMPLETED_IMPLEMENTATION_STATUSES}
    if active and not batches:
        issue("implementation_batches", "started implementation needs batches from implementation.md; use one default batch without orchestration")
    names: set[str] = set()
    frontier_seen = False
    delivered: list[tuple[int, dict[str, Any], str | None]] = []
    previous_paths: set[Path] = set()
    current_changes = result.get("changes", [])
    current_paths = {resolve_path(item.get("path"), base) for item in current_changes if isinstance(item, dict)} if isinstance(current_changes, list) else set()
    statuses = {"pending", "in_progress", "awaiting_review", "revising", "approved", "completed", "invalidated"}
    for index, batch in enumerate(batches):
        label = f"batches[{index}]"
        if not isinstance(batch, dict):
            issue("implementation_batches", f"{label} must be a mapping", malformed=True)
            continue
        name, steps, status = batch.get("name"), batch.get("steps"), batch.get("status")
        is_last = index == len(batches) - 1
        if not is_nonempty_string(name) or name in names:
            issue("implementation_batches", f"{label} needs a unique implementation.md batch name", malformed=True)
        else:
            names.add(name)
        if (not isinstance(steps, list) or not steps
                or any(not isinstance(step, str) or re.fullmatch(r"I-[A-Za-z0-9_-]+", step) is None for step in steps)
                or len(set(step for step in steps if isinstance(step, str))) != len(steps)):
            issue("implementation_batches", f"{label}.steps needs unique I-* references", malformed=True)
        if status not in statuses:
            issue("implementation_batches", f"{label} has invalid status", malformed=True)
        if result_status != "invalidated":
            if frontier_seen and status not in {"pending", "invalidated"}:
                issue("batch_order", f"{label} cannot start before every preceding batch has user approval")
            if status != "approved":
                frontier_seen = True
        if status == "completed":
            if not is_last:
                issue("batch_order", f"{label} completed is reserved for the final batch; earlier batches require user approval")
            if result_status not in COMPLETED_IMPLEMENTATION_STATUSES:
                issue("batch_binding", f"{label} automatic completion requires completed implementation and recorded verification")
            if any(batch.get(field) is not None for field in ("approved_at", "approval_note", "approved_input_fingerprint_sha256")):
                issue("batch_approval", f"{label} automatic completion is not user approval; keep user approval fields null")
        if status == "invalidated":
            add_issue(errors, kind="batch_invalidated", phase=min(current_phase, 7),
                      reason=f"{label} preserves stale delivery/approval evidence; revalidate the affected scope before resuming it")
        if status in {"in_progress", "revising", "awaiting_review", "approved", "completed"} and not active:
            issue("batch_binding", f"{label} requires active implementation; mark stale batch evidence invalidated")
        snapshots = batch.get("changes", [])
        lines: list[str] = []
        paths: set[Path] = set()
        if not isinstance(snapshots, list):
            issue("batch_output", f"{label}.changes must be a list", malformed=True)
            snapshots = []
        for change in snapshots:
            if not isinstance(change, dict):
                issue("batch_output", f"{label} snapshot items must be mappings", malformed=True)
                continue
            raw_path, kind = change.get("path"), change.get("kind")
            path, role, digest = resolve_path(raw_path, base), change.get("role"), change.get("sha256")
            if (path is None or path in paths or kind not in {"present", "removed"}
                    or not is_nonempty_string(role) or (kind == "present" and not is_sha256(digest))
                    or (kind == "removed" and digest is not None)):
                issue("batch_output", f"{label} snapshot needs unique path, valid kind, role, and sha256", malformed=True)
                continue
            paths.add(path)
            lines.append(f"{kind} | {str(raw_path).strip()} | {role.strip()} | {digest or ''}")
        output = sha256_text("\n".join(sorted(lines))) if lines else None
        verification = batch.get("verification", [])
        if not isinstance(verification, list):
            issue("batch_verification", f"{label}.verification must be a list", malformed=True)
            verification = []
        for check in verification:
            if (not isinstance(check, dict) or not is_nonempty_string(check.get("check"))
                    or check.get("result") not in {"passed", "failed", "blocked"}
                    or not is_nonempty_string(check.get("evidence"))):
                issue("batch_verification", f"{label} verification needs check, valid result, and evidence", malformed=True)
        # Historical approvals retain their delivered plan. Older v3 records
        # without this field still use the current plan until explicitly backfilled.
        delivery_plan = batch.get("plan_fingerprint_sha256")
        if delivery_plan is None:
            delivery_plan = expected_plan
        elif not is_sha256(delivery_plan):
            issue("batch_binding", f"{label}.plan_fingerprint_sha256 must be a SHA-256 or null", malformed=True)
        review_input = canonical_sha256({
            "plan": delivery_plan, "name": name, "steps": steps,
            "output": output, "verification": verification,
        }) if is_sha256(delivery_plan) and output is not None else None
        fingerprints.append({
            "index": index, "name": name, "output_fingerprint_sha256": output,
            "plan_fingerprint_sha256": delivery_plan,
            "review_input_fingerprint_sha256": review_input,
        })
        if status in {"awaiting_review", "completed"} and delivery_plan != expected_plan:
            issue("batch_binding", f"{label} new delivery must be bound to the current plan")
        if status in {"awaiting_review", "approved", "completed"}:
            delivered.append((index, batch, output))
            if not previous_paths.issubset(paths) or not paths.issubset(current_paths):
                issue("batch_output", f"{label} cumulative snapshots and current changes must retain earlier output paths, including removed files")
            previous_paths = paths
            if not snapshots or output is None or batch.get("output_fingerprint_sha256") != output:
                issue("batch_output", f"{label} delivery needs a non-empty cumulative snapshot and matching output fingerprint")
            if not verification:
                issue("batch_verification", f"{label} delivery needs recorded verification")
            if status != "completed" and (review_input is None or batch.get("review_input_fingerprint_sha256") != review_input):
                issue("batch_binding", f"{label} review fingerprint does not match its plan, scope, output, and verification")
        if status == "awaiting_review" and top_status not in {"awaiting_approval", "blocked"}:
            issue("batch_approval", f"{label} awaiting user review requires workflow awaiting_approval or blocked; do not continue writing")
        if status == "approved":
            if not is_nonempty_string(batch.get("approved_at")) or not is_nonempty_string(batch.get("approval_note")):
                issue("batch_approval", f"{label} user approval needs approved_at and approval_note", malformed=True)
            if review_input is None or batch.get("approved_input_fingerprint_sha256") != review_input:
                issue("batch_approval", f"{label} user approval is bound to another delivered version; require renewed user review")
            if delivery_plan != expected_plan:
                revalidation = batch.get("plan_revalidation")
                if (not isinstance(revalidation, dict) or expected_plan is None or review_input is None
                        or revalidation.get("plan_fingerprint_sha256") != expected_plan
                        or revalidation.get("approved_input_fingerprint_sha256") != review_input
                        or not is_nonempty_string(revalidation.get("checked_at"))
                        or not is_nonempty_string(revalidation.get("note"))):
                    issue("batch_revalidation", f"{label} historical approval needs an unaffected-scope revalidation bound to the current plan and original approved input")
            if not is_last and any(isinstance(check, dict) and check.get("result") in {"failed", "blocked"} for check in verification):
                if not is_nonempty_string(batch.get("accepted_unresolved")):
                    issue("batch_approval", f"{label} approval with failed/blocked checks requires explicit user accepted_unresolved explanation")
    # A subsequent active batch can legitimately alter earlier files. Only the
    # latest delivered snapshot with no later work is the current filesystem contract.
    if delivered:
        index, batch, output = delivered[-1]
        later_work = any(isinstance(item, dict) and item.get("status") in {"in_progress", "revising", "invalidated"}
                         for item in batches[index + 1:])
        if not later_work and output != computed_output:
            issue("batch_output", "latest delivered cumulative snapshot does not match current implementation changes; do not refresh old approval fingerprints")
    if result_status in COMPLETED_IMPLEMENTATION_STATUSES:
        if not batches or any(not isinstance(batch, dict) or batch.get("status") != "approved" for batch in batches[:-1]):
            issue("implementation_batches", "completed code requires user approval of all earlier batches")
        last = batches[-1] if batches and isinstance(batches[-1], dict) else {}
        if last.get("status") not in {"completed", "awaiting_review", "approved"}:
            issue("implementation_batches", "completed code needs a completed final batch (legacy awaiting_review/approved remain readable)")
        if last.get("verification") != result.get("verification"):
            issue("batch_verification", "final batch verification must match the final implementation verification, including required integration checks")
    if top_status == "complete" and (
        current_phase != 7 or result_status not in COMPLETED_IMPLEMENTATION_STATUSES or not batches
        or any(not isinstance(batch, dict) or batch.get("status") != "approved" for batch in batches[:-1])
        or not isinstance(batches[-1], dict) or batches[-1].get("status") not in {"completed", "approved"}
    ):
        issue("workflow_complete", "complete workflow requires completed implementation, approved earlier batches, and a completed final batch (legacy approved is accepted)")
    return fingerprints


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


def resume_context(state: dict[str, Any], state_path: Path) -> dict[str, Any]:
    """Project a validated state; paths are hints, never new approval decisions."""
    phase = state["current_phase"]
    base = state_path.parent
    inputs: list[dict[str, str]] = []
    approvals: dict[str, Any] = {}
    reviews: dict[str, Any] = {}

    def reference(field: str) -> str:
        return f"{state_path}#{field}"

    def add_input(role: str, raw_path: Any) -> None:
        try:
            path = resolve_path(raw_path, base)
            if path is not None and path.is_file():
                inputs.append({"role": role, "path": str(path)})
        except (OSError, RuntimeError, ValueError):
            # Reopened drafts may have no usable current path yet.
            return

    def approval(record: dict[str, Any], field: str) -> dict[str, Any]:
        return {"status": record["status"], "record_ref": reference(field)}

    def review(name: str) -> None:
        record = state[name]
        summary = {key: record.get(key) for key in ("status", "attempt", "outcome", "reviewer_ref")}
        if is_nonempty_string(record.get("conclusion")):
            summary["conclusion_ref"] = reference(f"{name}.conclusion")
        reviews[name] = summary

    # Phase 6 checks rule provenance against the original requirement as well.
    if phase <= 3 or phase == 6:
        add_input("prd", state["inputs"]["prd"]["path"])
    if phase <= 3:
        approvals["direction"] = approval(state["direction"], "direction")

    # Earlier approved contracts plus the current document, if it already exists.
    for name, artifact_phase in ARTIFACT_PHASES.items():
        if artifact_phase > phase:
            continue
        record = state["artifacts"][name]
        approvals[name] = approval(record, f"artifacts.{name}")
        if record["status"] == "not_started":
            continue
        if artifact_phase == phase or record["status"] == "approved" or phase >= 6:
            add_input(name, record.get("path"))

    grounding = state["repo_grounding"]
    pack = grounding.get("evidence_pack")
    if phase >= 2 and grounding["status"] == "current" and isinstance(pack, dict):
        add_input("grounding_pack", pack.get("path"))

    consistency = state["consistency_review"]
    revision = consistency["revision_decision"]
    if (phase >= 6 or revision["status"] != "not_required"
            or is_nonempty_string(consistency.get("conclusion"))):
        review("consistency_review")
        approvals["revision_decision"] = {
            **approval(revision, "consistency_review.revision_decision"),
            "return_phase": revision.get("return_phase"),
            "artifacts": revision.get("artifacts", []),
        }

    context: dict[str, Any] = {
        "status": state["status"],
        "next_action": state["next_action"],
        "open_blockers": state["open_blockers"],
        "inputs": inputs,
        "approvals": approvals,
        "reviews": reviews,
    }
    if state["inputs"].get("design_targets"):
        context["design_targets_ref"] = reference("inputs.design_targets")
    if (phase <= 3 and state["direction"]["status"] != "superseded"
            and is_nonempty_string(state["direction"].get("summary"))):
        context["direction_summary"] = state["direction"]["summary"]
    workspace = state.get("workspace")
    if isinstance(workspace, dict) and any(
        is_nonempty_string(workspace.get(key)) for key in ("workspace_path", "module_slug")
    ):
        context["workspace"] = {
            key: workspace.get(key) if is_nonempty_string(workspace.get(key)) else None
            for key in ("workspace_path", "module_slug")
        }
    if phase == 7:
        approvals["implementation_authorization"] = approval(
            state["implementation_authorization"], "implementation_authorization"
        )
        if state.get("schema") == "requirement-spec/v2":
            review("post_implementation_review")
        result = state["implementation_result"]
        verification = result.get("verification", [])
        context["implementation_result"] = {
            "status": result["status"],
            "changes_ref": reference("implementation_result.changes"),
            "verification_ref": reference("implementation_result.verification"),
            "verification_counts": {
                outcome: sum(item["result"] == outcome for item in verification)
                for outcome in ("passed", "failed", "blocked")
            },
        }
        batches = result.get("batches", [])
        frontier = next((i for i, batch in enumerate(batches) if batch["status"] not in {"approved", "completed"}), len(batches))
        def batch_reference(index: int) -> dict[str, Any] | None:
            if index >= len(batches):
                return None
            batch = batches[index]
            field = f"implementation_result.batches[{index}]"
            return {"name": batch["name"], "steps": batch["steps"], "status": batch["status"],
                    "record_ref": reference(field), "review_ref": reference(f"{field}.review_input_fingerprint_sha256"),
                    "approval_ref": reference(f"{field}.approval_note")}
        context["implementation_result"]["batch_progress"] = {
            "total": len(batches), "approved": sum(batch["status"] == "approved" for batch in batches),
            "completed": sum(batch["status"] == "completed" for batch in batches),
            "current": batch_reference(frontier), "next": batch_reference(frontier + 1),
        }
        auto_finish_ready = (
            state.get("schema") == "requirement-spec/v3" and state["status"] != "complete"
            and result["status"] in COMPLETED_IMPLEMENTATION_STATUSES and bool(batches)
            and all(batch["status"] == "approved" for batch in batches[:-1])
            and batches[-1]["status"] in {"awaiting_review", "completed", "approved"}
        )
        context["implementation_result"]["auto_finish_ready"] = auto_finish_ready
        if auto_finish_ready:
            context["next_action"] = (
                "核实最后批次实施与验证记录后自动收尾：将末批 awaiting_review 改为 completed（保留已有真实 approved），"
                "将流程状态改为 complete；无需用户批准，按实报告验证失败或阻塞。"
            )
    return context


def context_payload(
    payload: dict[str, Any], state: dict[str, Any], state_path: Path
) -> dict[str, Any]:
    """Keep diagnostics useful without echoing digests or stale execution context."""
    result = dict(payload)
    if payload["ok"]:
        result["context"] = resume_context(state, state_path)
        return result

    field_by_kind = {
        "prd": "inputs.prd",
        "project_rule": "inputs.project_rules",
        "project_rules": "inputs.project_rules",
        "relevant_target": "repo_grounding.relevant_targets",
        "relevant_targets": "repo_grounding.relevant_targets",
        "grounding_fingerprint": "repo_grounding",
        "evidence_pack": "repo_grounding.evidence_pack",
        "evidence_pack_binding": "repo_grounding.evidence_pack",
        "plan_fingerprint": "plan_fingerprint_sha256",
        "implementation_change": "implementation_result.changes",
        "implementation_changes": "implementation_result.changes",
        "output_fingerprint": "implementation_result.changes",
        "verification": "implementation_result.verification",
        "revision_decision": "consistency_review.revision_decision",
        "approval_order": "artifacts",
        "workflow_complete": "status",
        "schema_migration": "schema",
        "implementation_batches": "implementation_result.batches",
        "batch_order": "implementation_result.batches",
        "batch_invalidated": "implementation_result.batches",
        "batch_binding": "implementation_result.batches",
        "batch_revalidation": "implementation_result.batches",
        "batch_approval": "implementation_result.batches",
        "batch_output": "implementation_result.batches",
        "batch_verification": "implementation_result.batches",
    }
    hints: list[dict[str, Any]] = []
    for collection in ("errors", "drift"):
        result[collection] = [
            {key: item[key] for key in ("kind", "phase", "reason", "path") if key in item}
            for item in payload.get(collection, [])
        ]
        for item in result[collection]:
            kind = item["kind"]
            field = field_by_kind.get(kind, kind.removesuffix("_binding"))
            if kind in ARTIFACT_PHASES:
                field = f"artifacts.{kind}"
            if field.split(".", 1)[0] not in state:
                field = ""
            hint = {"phase": item["phase"], "record_ref": f"{state_path}#{field}"}
            if "path" in item:
                hint["path"] = item["path"]
            if hint not in hints:
                hints.append(hint)
    result["revalidation_inputs"] = hints
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path, help="path to requirement-spec/v3 state.yaml (completed v2 records remain readable)")
    parser.add_argument(
        "--context", action="store_true",
        help="include compact current-phase context; report only targeted hints on invalid state or drift",
    )
    parser.add_argument(
        "--fingerprints", action="store_true",
        help="report current file hashes and aggregates from recorded inputs without changing state",
    )
    args = parser.parse_args()
    state_path = args.state.expanduser().resolve()
    state_sha: str | None = None
    source_bytes: bytes | None = None
    try:
        source_bytes = read_state_bytes(state_path)
        if args.context:
            state_sha = sha256_bytes(source_bytes)
        state = load_yaml(state_path, source_bytes=source_bytes)
    except ValueError as exc:
        reason = str(exc)
        if args.context and source_bytes is not None and reason != "state must be a YAML mapping":
            reason = "state cannot be parsed as UTF-8 YAML; inspect the state file"
        payload = {"ok": False, "resume_mode": "targeted_revalidation", "errors": [{"kind": "state", "phase": 1, "reason": reason}]}
        if args.context:
            payload["state_sha256"] = state_sha
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    code, payload = validate_state(state, state_path, include_fingerprints=args.fingerprints)
    if args.context:
        payload = context_payload(payload, state, state_path)
        payload["state_sha256"] = state_sha
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
