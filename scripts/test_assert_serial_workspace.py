#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml


SCRIPT = Path(__file__).with_name("assert_serial_workspace.py")


class SerialWorkspaceGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.repo = root / "repo"
        self.delivery = root / "spec" / "workspace"
        self.workflow = root / "spec" / ".workflow" / "workspace"
        self.repo.mkdir(parents=True)
        self.delivery.mkdir(parents=True)
        self.workflow.mkdir(parents=True)
        self.workspace_path = self.workflow / "workspace.yaml"

        self.state_a = self.workflow / "modules" / "a" / "state.yaml"
        self.state_b = self.workflow / "modules" / "b" / "state.yaml"
        self.state_a.parent.mkdir(parents=True)
        self.state_b.parent.mkdir(parents=True)
        self.write_state(self.state_a, "a", "in_progress")
        self.write_state(self.state_b, "b", "queued")

        self.workspace = {
            "schema": "requirement-workspace/v1",
            "workspace_slug": "workspace",
            "repo_root": str(self.repo),
            "delivery_dir": str(self.delivery),
            "workflow_dir": str(self.workflow),
            "execution_mode": "strict_serial",
            "active_module": None,
            "active_owner": None,
            "active_since": None,
            "prd": {"path": str(self.delivery / "prd.md")},
            "members": [
                {
                    "module_slug": "a",
                    "sequence": 1,
                    "delivery_dir": str(self.delivery / "modules" / "a"),
                    "state_path": str(self.state_a),
                    "status": "queued",
                    "activated_at": None,
                    "completed_at": None,
                    "last_owner": None,
                },
                {
                    "module_slug": "b",
                    "sequence": 2,
                    "delivery_dir": str(self.delivery / "modules" / "b"),
                    "state_path": str(self.state_b),
                    "status": "queued",
                    "activated_at": None,
                    "completed_at": None,
                    "last_owner": None,
                },
            ],
            "next_action": "激活模块 a",
        }
        self.write_workspace()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write_state(path: Path, module: str, status: str) -> None:
        path.write_text(
            yaml.safe_dump(
                {
                    "schema": "requirement-spec/v2",
                    "feature_slug": module,
                    "current_phase": 7 if status == "complete" else 1,
                    "status": status,
                    "workspace": {"module_slug": module},
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def write_workspace(self) -> None:
        self.workspace_path.write_text(
            yaml.safe_dump(self.workspace, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def read_workspace(self) -> dict:
        return yaml.safe_load(self.workspace_path.read_text(encoding="utf-8"))

    def run_gate(
        self,
        module: str,
        action: str,
        owner: str | None = None,
        *,
        takeover: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [
            sys.executable,
            str(SCRIPT),
            str(self.workspace_path),
            "--module",
            module,
            "--action",
            action,
        ]
        if owner is not None:
            command.extend(["--owner", owner])
        if takeover:
            command.append("--takeover")
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        return completed, json.loads(completed.stdout)

    def test_claims_first_module_and_records_owner(self) -> None:
        completed, result = self.run_gate("a", "claim", "task-a")
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        workspace = self.read_workspace()
        self.assertEqual("a", workspace["active_module"])
        self.assertEqual("task-a", workspace["active_owner"])
        self.assertEqual("in_progress", workspace["members"][0]["status"])

    def test_blocks_later_module_until_all_earlier_modules_complete(self) -> None:
        completed, result = self.run_gate("b", "claim", "task-b")
        self.assertEqual(1, completed.returncode)
        self.assertFalse(result["ok"])
        self.assertIn("earlier modules are not complete: a", result["errors"])

    def test_claim_is_idempotent_for_same_owner_and_exclusive_for_others(self) -> None:
        self.assertEqual(0, self.run_gate("a", "claim", "task-a")[0].returncode)

        repeated, repeated_result = self.run_gate("a", "claim", "task-a")
        self.assertEqual(0, repeated.returncode)
        self.assertFalse(repeated_result["changed"])

        other_owner, owner_result = self.run_gate("a", "claim", "task-other")
        self.assertEqual(1, other_owner.returncode)
        self.assertTrue(any("owned by another task" in item for item in owner_result["errors"]))

        other_module, module_result = self.run_gate("b", "claim", "task-b")
        self.assertEqual(1, other_module.returncode)
        self.assertFalse(module_result["ok"])

    def test_takeover_transfers_only_the_current_module(self) -> None:
        self.assertEqual(0, self.run_gate("a", "claim", "task-a")[0].returncode)
        completed, result = self.run_gate("a", "claim", "task-new", takeover=True)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue(result["changed"])
        self.assertEqual("task-new", self.read_workspace()["active_owner"])

    def test_check_requires_current_module_and_owner(self) -> None:
        self.assertEqual(0, self.run_gate("a", "claim", "task-a")[0].returncode)
        self.assertEqual(0, self.run_gate("a", "check", "task-a")[0].returncode)
        self.assertEqual(1, self.run_gate("a", "check", "task-other")[0].returncode)
        self.assertEqual(1, self.run_gate("b", "check", "task-b")[0].returncode)

    def test_complete_requires_module_state_to_be_complete(self) -> None:
        self.assertEqual(0, self.run_gate("a", "claim", "task-a")[0].returncode)
        completed, result = self.run_gate("a", "complete", "task-a")
        self.assertEqual(1, completed.returncode)
        self.assertTrue(any("module state must be complete" in item for item in result["errors"]))

    def test_complete_releases_workspace_and_enables_next_module(self) -> None:
        self.assertEqual(0, self.run_gate("a", "claim", "task-a")[0].returncode)
        self.write_state(self.state_a, "a", "complete")

        completed, result = self.run_gate("a", "complete", "task-a")
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertTrue(result["ok"])
        workspace = self.read_workspace()
        self.assertIsNone(workspace["active_module"])
        self.assertIsNone(workspace["active_owner"])
        self.assertEqual("complete", workspace["members"][0]["status"])
        self.assertEqual("b", result["next_module"])

        next_claim, next_result = self.run_gate("b", "claim", "task-b")
        self.assertEqual(0, next_claim.returncode, next_claim.stdout + next_claim.stderr)
        self.assertTrue(next_result["ok"])

    def test_concurrent_claims_allow_only_one_owner(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.run_gate, "a", "claim", "task-a"),
                executor.submit(self.run_gate, "a", "claim", "task-other"),
            ]
        results = [future.result() for future in futures]
        self.assertEqual(1, sum(completed.returncode == 0 for completed, _ in results))
        workspace = self.read_workspace()
        self.assertEqual("a", workspace["active_module"])
        self.assertIn(workspace["active_owner"], {"task-a", "task-other"})
        self.assertEqual(1, sum(member["status"] == "in_progress" for member in workspace["members"]))

    def test_rejects_invalid_workspace_structure(self) -> None:
        self.workspace["members"][1]["sequence"] = 1
        self.write_workspace()
        completed, result = self.run_gate("a", "status")
        self.assertEqual(2, completed.returncode)
        self.assertFalse(result["ok"])
        self.assertTrue(any("duplicate member sequence" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
