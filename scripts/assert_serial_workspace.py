#!/usr/bin/env python3

"""Atomically claim, check, or complete one module in a strict-serial workspace."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


MEMBER_STATUSES = {"queued", "in_progress", "blocked", "complete"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"workspace does not exist: {path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"workspace cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("workspace must be a YAML mapping")
    return value


def write_yaml_atomically(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(value, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, path.stat().st_mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def resolve_path(raw: Any, base: Path) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip()).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def validate_workspace(
    workspace: dict[str, Any], workspace_path: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    if workspace.get("schema") != "requirement-workspace/v1":
        errors.append("workspace.schema must be 'requirement-workspace/v1'")
    if workspace.get("execution_mode") != "strict_serial":
        errors.append("workspace.execution_mode must be 'strict_serial'")

    workflow_dir = resolve_path(workspace.get("workflow_dir"), workspace_path.parent)
    if workflow_dir is None or workflow_dir != workspace_path.parent.resolve():
        errors.append("workspace.workflow_dir must identify the directory containing workspace.yaml")

    raw_members = workspace.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        return [], [*errors, "workspace.members must contain at least one member"]

    members: list[dict[str, Any]] = []
    slugs: set[str] = set()
    sequences: set[int] = set()
    for index, item in enumerate(raw_members):
        if not isinstance(item, dict):
            errors.append(f"workspace.members[{index}] must be a mapping")
            continue
        slug = item.get("module_slug")
        sequence = item.get("sequence")
        status = item.get("status")
        if not isinstance(slug, str) or not slug.strip():
            errors.append(f"workspace.members[{index}].module_slug is missing")
            continue
        if slug in slugs:
            errors.append(f"duplicate module_slug: {slug}")
        slugs.add(slug)
        if not isinstance(sequence, int) or sequence < 1:
            errors.append(f"member {slug} must have a positive integer sequence")
            continue
        if sequence in sequences:
            errors.append(f"duplicate member sequence: {sequence}")
        sequences.add(sequence)
        if status not in MEMBER_STATUSES:
            errors.append(f"member {slug} has invalid status: {status!r}")
        members.append(item)

    members.sort(key=lambda member: member.get("sequence", 0))
    for index, member in enumerate(members, start=1):
        if member.get("sequence") != index:
            errors.append(
                f"member {member.get('module_slug')} sequence must be contiguous; expected {index}"
            )

    active_module = workspace.get("active_module")
    active_owner = workspace.get("active_owner")
    in_progress = [
        member.get("module_slug") for member in members if member.get("status") == "in_progress"
    ]
    if len(in_progress) > 1:
        errors.append(f"only one module may be in_progress, found: {', '.join(in_progress)}")
    if active_module is None:
        if active_owner is not None:
            errors.append("workspace.active_owner must be null when no module is active")
        if in_progress:
            errors.append("an in_progress member requires workspace.active_module")
    else:
        active = next(
            (member for member in members if member.get("module_slug") == active_module), None
        )
        if active is None:
            errors.append(f"workspace.active_module is not registered: {active_module}")
        elif active.get("status") != "in_progress":
            errors.append("workspace.active_module must reference an in_progress member")
        if not isinstance(active_owner, str) or not active_owner.strip():
            errors.append("an active workspace requires a non-empty active_owner")
        if in_progress and in_progress != [active_module]:
            errors.append("the in_progress member must match workspace.active_module")

    return members, errors


def previous_incomplete(members: list[dict[str, Any]], target: dict[str, Any]) -> list[str]:
    target_sequence = target["sequence"]
    return [
        str(member.get("module_slug"))
        for member in members
        if member.get("sequence", 0) < target_sequence and member.get("status") != "complete"
    ]


def next_incomplete(members: list[dict[str, Any]]) -> str | None:
    return next(
        (
            str(member.get("module_slug"))
            for member in members
            if member.get("status") != "complete"
        ),
        None,
    )


def result_payload(
    *,
    ok: bool,
    action: str,
    module: str,
    workspace: dict[str, Any] | None,
    target: dict[str, Any] | None = None,
    changed: bool = False,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    members = workspace.get("members", []) if isinstance(workspace, dict) else []
    normalized = [item for item in members if isinstance(item, dict)]
    normalized.sort(key=lambda item: item.get("sequence", 0))
    return {
        "ok": ok,
        "action": action,
        "module": module,
        "changed": changed,
        "member_status": target.get("status") if isinstance(target, dict) else None,
        "active_module": workspace.get("active_module") if isinstance(workspace, dict) else None,
        "active_owner": workspace.get("active_owner") if isinstance(workspace, dict) else None,
        "next_module": next_incomplete(normalized),
        "errors": errors or [],
    }


def ensure_owner(owner: str | None) -> str:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("--owner is required for claim, check, and complete")
    return owner.strip()


def check_module_state_complete(target: dict[str, Any], workspace_path: Path) -> str | None:
    state_path = resolve_path(target.get("state_path"), workspace_path.parent)
    if state_path is None:
        return "target member state_path is missing"
    try:
        state = load_yaml(state_path)
    except ValueError as exc:
        return str(exc)
    if state.get("status") != "complete":
        return "target module state must be complete before releasing the workspace"
    membership = state.get("workspace")
    if isinstance(membership, dict):
        recorded_slug = membership.get("module_slug")
        if recorded_slug not in (None, target.get("module_slug")):
            return "target module state belongs to another workspace member"
    return None


def apply_action(args: argparse.Namespace, workspace_path: Path) -> tuple[int, dict[str, Any]]:
    owner = None
    if args.action in {"claim", "check", "complete"}:
        try:
            owner = ensure_owner(args.owner)
        except ValueError as exc:
            return 2, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=None,
                errors=[str(exc)],
            )

    lock_path = workspace_path.with_name(f".{workspace_path.name}.gate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            workspace = load_yaml(workspace_path)
        except ValueError as exc:
            return 2, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=None,
                errors=[str(exc)],
            )

        members, errors = validate_workspace(workspace, workspace_path)
        target = next(
            (member for member in members if member.get("module_slug") == args.module), None
        )
        if target is None:
            errors.append(f"target module is not registered: {args.module}")
        if errors:
            return 2, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
                errors=errors,
            )

        if args.action == "status":
            return 0, result_payload(
                ok=True,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
            )

        assert target is not None
        assert owner is not None
        blockers = previous_incomplete(members, target)
        if blockers:
            return 1, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
                errors=[f"earlier modules are not complete: {', '.join(blockers)}"],
            )

        active_module = workspace.get("active_module")
        active_owner = workspace.get("active_owner")

        if args.action == "claim":
            if target.get("status") == "complete":
                return 1, result_payload(
                    ok=False,
                    action=args.action,
                    module=args.module,
                    workspace=workspace,
                    target=target,
                    errors=["a complete module cannot be claimed"],
                )
            if active_module is None:
                timestamp = now_iso()
                workspace["active_module"] = args.module
                workspace["active_owner"] = owner
                workspace["active_since"] = timestamp
                target["status"] = "in_progress"
                target["activated_at"] = target.get("activated_at") or timestamp
                target["last_owner"] = owner
                workspace["next_action"] = f"继续模块 {args.module}"
                write_yaml_atomically(workspace_path, workspace)
                return 0, result_payload(
                    ok=True,
                    action=args.action,
                    module=args.module,
                    workspace=workspace,
                    target=target,
                    changed=True,
                )
            if active_module != args.module:
                return 1, result_payload(
                    ok=False,
                    action=args.action,
                    module=args.module,
                    workspace=workspace,
                    target=target,
                    errors=[f"another module is active: {active_module}"],
                )
            if active_owner == owner:
                return 0, result_payload(
                    ok=True,
                    action=args.action,
                    module=args.module,
                    workspace=workspace,
                    target=target,
                )
            if not args.takeover:
                return 1, result_payload(
                    ok=False,
                    action=args.action,
                    module=args.module,
                    workspace=workspace,
                    target=target,
                    errors=[f"module {args.module} is owned by another task: {active_owner}"],
                )
            workspace["active_owner"] = owner
            workspace["active_since"] = now_iso()
            target["last_owner"] = owner
            workspace["next_action"] = f"继续模块 {args.module}"
            write_yaml_atomically(workspace_path, workspace)
            return 0, result_payload(
                ok=True,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
                changed=True,
            )

        if active_module != args.module:
            message = (
                "no module is active" if active_module is None else f"another module is active: {active_module}"
            )
            return 1, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
                errors=[message],
            )
        if active_owner != owner:
            return 1, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
                errors=[f"module {args.module} is owned by another task: {active_owner}"],
            )

        if args.action == "check":
            return 0, result_payload(
                ok=True,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
            )

        state_error = check_module_state_complete(target, workspace_path)
        if state_error is not None:
            return 1, result_payload(
                ok=False,
                action=args.action,
                module=args.module,
                workspace=workspace,
                target=target,
                errors=[state_error],
            )
        timestamp = now_iso()
        target["status"] = "complete"
        target["completed_at"] = timestamp
        workspace["active_module"] = None
        workspace["active_owner"] = None
        workspace["active_since"] = None
        remaining = next_incomplete(members)
        workspace["next_action"] = (
            f"激活模块 {remaining}" if remaining is not None else "工作区全部模块已完成"
        )
        write_yaml_atomically(workspace_path, workspace)
        return 0, result_payload(
            ok=True,
            action=args.action,
            module=args.module,
            workspace=workspace,
            target=target,
            changed=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="Path to requirement-workspace/v1 workspace.yaml")
    parser.add_argument("--module", required=True, help="Module slug to claim, check, or complete")
    parser.add_argument(
        "--action",
        choices=("claim", "check", "complete", "status"),
        default="status",
        help="Workspace gate action; status is read-only",
    )
    parser.add_argument("--owner", help="Stable task or run identifier")
    parser.add_argument(
        "--takeover",
        action="store_true",
        help="Transfer the active module to a new owner after confirming the previous task is inactive",
    )
    args = parser.parse_args()
    if args.takeover and args.action != "claim":
        payload = result_payload(
            ok=False,
            action=args.action,
            module=args.module,
            workspace=None,
            errors=["--takeover is only valid with --action claim"],
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    code, payload = apply_action(args, args.workspace.expanduser().resolve())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
