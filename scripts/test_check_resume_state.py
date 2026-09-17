from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.check_resume_state import canonical_sha256, context_payload, main, sha256_bytes, sha256_text, validate_state


SKILL_ROOT = Path(__file__).resolve().parent.parent
CHECKER = SKILL_ROOT / "scripts" / "check_resume_state.py"


class ResumeStateCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.delivery = self.root / "docs" / "spec" / "feature"
        self.workflow = self.root / "docs" / "spec" / ".workflow" / "feature"
        self.delivery.mkdir(parents=True)
        self.workflow.mkdir(parents=True)
        self.prd = self.delivery / "prd.md"
        self.rule = self.root / "AGENTS.md"
        self.target = self.root / "src" / "Feature.kt"
        self.write(self.prd, "requirement\n")
        self.write(self.rule, "rule\n")
        self.write(self.target, "old implementation\n")
        self.state_path = self.workflow / "state.yaml"
        self.state = self.base_state()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def digest(path: Path) -> str:
        return sha256_text(path.read_text(encoding="utf-8"))

    def grounding_fingerprint(self) -> str:
        lines = [
            f"prd | {self.prd} |  | {self.digest(self.prd)}",
            f"rules | {self.rule} |  | {self.digest(self.rule)}",
            f"owner | {self.target} | Feature | {self.state['repo_grounding']['relevant_targets'][0]['sha256']}",
        ]
        return sha256_text("\n".join(sorted(lines)))

    def base_state(self) -> dict:
        target_sha = self.digest(self.target)
        state = {
            "schema": "requirement-spec/v3",
            "feature_slug": "feature",
            "repo_root": str(self.root),
            "delivery_dir": str(self.delivery),
            "workflow_dir": str(self.workflow),
            "workspace": {"workspace_path": None, "module_slug": None},
            "inputs": {
                "prd": {"path": str(self.prd), "sha256": self.digest(self.prd)},
                "project_rules": [
                    {"path": str(self.rule), "role": "rules", "sha256": self.digest(self.rule)}
                ],
                "design_targets": [],
            },
            "current_phase": 2,
            "status": "in_progress",
            "next_action": "核对技术依据",
            "open_blockers": [],
            "recovery": {"migrated_from_tasks": []},
            "direction": {
                "status": "not_started",
                "summary": None,
                "approved_at": None,
                "approval_note": None,
            },
            "artifacts": {
                name: {
                    "status": "not_started",
                    "path": str(self.delivery / f"{name}.md"),
                    "sha256": None,
                    "approved_at": None,
                    "approval_note": None,
                }
                for name in ("spec", "test", "implementation")
            },
            "repo_grounding": {
                "status": "current",
                "checked_at": "2026-09-03T10:00:00+08:00",
                "fingerprint_sha256": None,
                "evidence_pack": {"path": None, "sha256": None, "input_fingerprint_sha256": None},
                "relevant_targets": [
                    {
                        "path": str(self.target),
                        "symbol": "Feature",
                        "role": "owner",
                        "sha256": target_sha,
                    }
                ],
            },
            "plan_fingerprint_sha256": None,
            "consistency_review": {
                "status": "not_started",
                "plan_fingerprint_sha256": None,
                "reviewer_ref": None,
                "attempt": 0,
                "outcome": None,
                "conclusion": None,
                "conclusion_sha256": None,
                "completed_at": None,
                "revision_decision": {
                    "status": "not_required",
                    "return_phase": None,
                    "artifacts": [],
                    "review_conclusion_sha256": None,
                    "decided_at": None,
                    "decision_note": None,
                },
            },
            "implementation_authorization": {
                "status": "not_granted",
                "plan_fingerprint_sha256": None,
                "granted_at": None,
                "approval_note": None,
            },
            "implementation_result": {
                "status": "not_started",
                "plan_fingerprint_sha256": None,
                "output_manifest_complete": False,
                "output_fingerprint_sha256": None,
                "changes": [],
                "verification": [],
                "batches": [],
                "completed_at": None,
            },

        }
        self.state = state
        state["repo_grounding"]["fingerprint_sha256"] = self.grounding_fingerprint()
        return state

    def approve_planning(self) -> None:
        for name in ("spec", "test", "implementation"):
            path = self.delivery / f"{name}.md"
            self.write(path, f"{name} content\n")
            self.state["artifacts"][name].update(
                {
                    "status": "approved",
                    "sha256": self.digest(path),
                    "approved_at": "2026-09-03T10:10:00+08:00",
                    "approval_note": f"{name} approved",
                }
            )
        self.state["direction"]["status"] = "superseded"
        self.state["current_phase"] = 6
        self.state["next_action"] = "执行独立审阅"
        self.state["plan_fingerprint_sha256"] = canonical_sha256(
            {
                "grounding": self.state["repo_grounding"]["fingerprint_sha256"],
                "implementation": self.state["artifacts"]["implementation"]["sha256"],
                "spec": self.state["artifacts"]["spec"]["sha256"],
                "test": self.state["artifacts"]["test"]["sha256"],
            }
        )

    def pass_consistency_review(self) -> None:
        conclusion = "Documents are consistent."
        self.state["consistency_review"].update(
            {
                "status": "passed",
                "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
                "reviewer_ref": "reviewer-phase-6",
                "attempt": 1,
                "outcome": "no_findings",
                "conclusion": conclusion,
                "conclusion_sha256": sha256_text(conclusion),
                "completed_at": "2026-09-03T10:20:00+08:00",
            }
        )

    def authorize(self) -> None:
        self.pass_consistency_review()
        self.state["implementation_authorization"] = {
            "status": "granted",
            "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
            "granted_at": "2026-09-03T10:30:00+08:00",
            "approval_note": "Implement the approved plan",
        }

    def verify_implementation(self, *, removed: bool = False) -> None:
        self.authorize()
        if removed:
            self.target.unlink()
            change = {"path": str(self.target), "kind": "removed", "role": "owner", "sha256": None}
        else:
            self.write(self.target, "new implementation\n")
            change = {
                "path": str(self.target),
                "kind": "present",
                "role": "owner",
                "sha256": self.digest(self.target),
            }
        output_line = f"{change['kind']} | {change['path']} | {change['role']} | {change['sha256'] or ''}"
        self.state["implementation_result"] = {
            "status": "verified",
            "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
            "output_manifest_complete": True,
            "output_fingerprint_sha256": sha256_text(output_line),
            "changes": [change],
            "verification": [
                {"check": "unit tests", "result": "passed", "evidence": "1 test passed"}
            ],
            "completed_at": "2026-09-03T10:40:00+08:00",
        }
        self.state["current_phase"] = 7
        self.state["status"] = "awaiting_approval"
        self.state["next_action"] = "等待用户审阅默认批次"
        self.deliver_final_batch()

    def deliver_final_batch(self) -> None:
        result = self.state["implementation_result"]
        result["batches"] = [{
            "name": "默认批次", "steps": ["I-1"], "status": "awaiting_review",
            "changes": copy.deepcopy(result["changes"]),
            "verification": copy.deepcopy(result["verification"]),
            "approved_at": None, "approval_note": None,
            "approved_input_fingerprint_sha256": None, "accepted_unresolved": None,
        }]
        self.bind_batch(result["batches"][0])

    def bind_batch(self, batch: dict) -> None:
        if batch.get("plan_fingerprint_sha256") is None:
            batch["plan_fingerprint_sha256"] = self.state["plan_fingerprint_sha256"]
        lines = [f"{item['kind']} | {item['path']} | {item['role']} | {item['sha256'] or ''}"
                 for item in batch["changes"]]
        batch["output_fingerprint_sha256"] = sha256_text("\n".join(sorted(lines)))
        batch["review_input_fingerprint_sha256"] = canonical_sha256({
            "plan": batch["plan_fingerprint_sha256"], "name": batch["name"],
            "steps": batch["steps"], "output": batch["output_fingerprint_sha256"],
            "verification": batch["verification"],
        })

    def approve_final_batch(self, *, outcome: str = "no_findings") -> None:
        # Helper models an explicit approval of the current delivered version.
        self.deliver_final_batch()
        batch = self.state["implementation_result"]["batches"][-1]
        batch.update(status="approved", approved_at="2026-09-03T10:50:00+08:00",
                     approval_note="用户明确确认本批通过",
                     approved_input_fingerprint_sha256=batch["review_input_fingerprint_sha256"])
        if any(check["result"] != "passed" for check in batch["verification"]):
            batch["accepted_unresolved"] = "用户明确接受已列明的验证失败/阻塞及其影响"
        self.state["status"] = "complete"
        self.state["next_action"] = "汇报最终结果"

    def complete_final_batch(self) -> None:
        self.deliver_final_batch()
        batch = self.state["implementation_result"]["batches"][-1]
        batch.update(status="completed", review_input_fingerprint_sha256=None)
        self.state.update(status="complete", next_action="汇报最终结果及验证遗留")

    def run_check(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        self.state_path.write_text(
            yaml.safe_dump(self.state, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.invoke_check(*args)

    def invoke_check(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [sys.executable, str(CHECKER), *args, str(self.state_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def test_uses_fast_path_for_current_early_state(self) -> None:
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertEqual("fast_path", result["resume_mode"])

    def test_approved_spec_does_not_require_direction_file_or_backfill(self) -> None:
        path = self.delivery / "spec.md"
        self.write(path, "spec content\n")
        self.state["artifacts"]["spec"].update(
            {
                "status": "approved",
                "sha256": self.digest(path),
                "approved_at": "2026-09-03T10:10:00+08:00",
                "approval_note": "spec approved",
            }
        )
        self.state["direction"]["status"] = "superseded"
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertFalse((self.workflow / "direction-brief.md").exists())
        self.assertEqual([], result["errors"])

    def test_accepts_complete_workflow_without_delegation_ledger(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertNotIn("delegation", self.state)
        self.assertTrue(result["ok"])

    def test_user_can_accept_recorded_nonblocking_findings(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch(outcome="findings")
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertTrue(result["ok"])

    def test_reports_prd_drift_at_phase_one(self) -> None:
        self.write(self.prd, "changed requirement\n")
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertEqual(1, result["earliest_candidate_phase"])
        self.assertIn("prd", {item["kind"] for item in result["drift"]})

    def test_reports_approved_spec_drift_at_phase_three(self) -> None:
        self.approve_planning()
        self.write(self.delivery / "spec.md", "changed spec\n")
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("spec", {item["kind"] for item in result["drift"]})

    def test_reports_unregistered_grounding_target_change(self) -> None:
        self.write(self.target, "unexpected change\n")
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("relevant_target", {item["kind"] for item in result["drift"]})

    def test_accepts_authorized_output_replacing_grounding_baseline(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertTrue(result["ok"])

    def test_accepts_authorized_removal_of_grounding_target(self) -> None:
        self.approve_planning()
        self.verify_implementation(removed=True)
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertTrue(result["ok"])

    def test_rejects_wrong_plan_fingerprint(self) -> None:
        self.approve_planning()
        self.state["plan_fingerprint_sha256"] = "0" * 64
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("plan_fingerprint", {item["kind"] for item in result["errors"]})

    def test_rejects_approval_order_violation(self) -> None:
        path = self.delivery / "test.md"
        self.write(path, "test content\n")
        self.state["artifacts"]["test"].update(
            {
                "status": "approved",
                "sha256": self.digest(path),
                "approved_at": "2026-09-03T10:10:00+08:00",
                "approval_note": "test approved",
            }
        )
        completed, result = self.run_check()
        self.assertEqual(2, completed.returncode)
        self.assertIn("approval_order", {item["kind"] for item in result["errors"]})

    def test_complete_workflow_requires_completed_final_batch(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.state["status"] = "complete"
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("workflow_complete", {item["kind"] for item in result["errors"]})

    def test_rejects_review_attempt_above_two(self) -> None:
        self.approve_planning()
        self.state["consistency_review"].update(
            {
                "status": "in_progress",
                "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
                "reviewer_ref": "reviewer",
                "attempt": 3,
            }
        )
        completed, result = self.run_check()
        self.assertEqual(2, completed.returncode)
        self.assertIn("consistency_review", {item["kind"] for item in result["errors"]})

    def test_blocked_review_requires_second_attempt(self) -> None:
        self.approve_planning()
        self.state["consistency_review"].update(
            {
                "status": "blocked",
                "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
                "attempt": 1,
            }
        )
        completed, result = self.run_check()
        self.assertEqual(2, completed.returncode)
        self.assertIn("consistency_review", {item["kind"] for item in result["errors"]})

    def test_rejects_review_bound_to_old_plan(self) -> None:
        self.approve_planning()
        self.state["consistency_review"].update(
            {
                "status": "in_progress",
                "plan_fingerprint_sha256": "0" * 64,
                "reviewer_ref": "reviewer",
                "attempt": 1,
            }
        )
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("consistency_review_binding", {item["kind"] for item in result["errors"]})

    def await_revision_decision(self) -> None:
        self.approve_planning()
        conclusion = "Implementation document needs one correction."
        conclusion_sha = sha256_text(conclusion)
        self.state["consistency_review"].update(
            {
                "status": "completed",
                "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
                "reviewer_ref": "reviewer-phase-6",
                "attempt": 1,
                "outcome": "findings",
                "conclusion": conclusion,
                "conclusion_sha256": conclusion_sha,
                "completed_at": "2026-09-03T10:20:00+08:00",
                "revision_decision": {
                    "status": "awaiting_decision",
                    "return_phase": 5,
                    "artifacts": ["implementation"],
                    "review_conclusion_sha256": conclusion_sha,
                    "decided_at": None,
                    "decision_note": None,
                },
            }
        )
        self.state["status"] = "awaiting_approval"
        self.state["next_action"] = "等待用户决定是否返修 implementation.md"

    def test_accepts_completed_phase_six_findings_awaiting_revision_decision(self) -> None:
        self.await_revision_decision()
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertTrue(result["ok"])

    def test_pending_revision_decision_prevents_passed_consistency_review(self) -> None:
        self.await_revision_decision()
        self.state["consistency_review"]["status"] = "passed"

        completed, result = self.run_check()

        self.assertEqual(1, completed.returncode)
        self.assertEqual("targeted_revalidation", result["resume_mode"])
        self.assertTrue(any(item["kind"] == "revision_decision" and item["phase"] == 6 for item in result["errors"]))

    def test_pending_revision_decision_prevents_granted_implementation_authorization(self) -> None:
        for phase, review_status in ((6, "completed"), (6, "passed"), (7, "passed")):
            with self.subTest(phase=phase, review_status=review_status):
                self.await_revision_decision()
                self.state["current_phase"] = phase
                self.state["consistency_review"]["status"] = review_status
                self.state["implementation_authorization"].update(
                    status="granted",
                    plan_fingerprint_sha256=self.state["plan_fingerprint_sha256"],
                    granted_at="2026-09-03T10:30:00+08:00",
                    approval_note="Implement the approved plan",
                )

                completed, result = self.run_check()

                self.assertEqual(1, completed.returncode)
                self.assertEqual("targeted_revalidation", result["resume_mode"])
                self.assertTrue(any(item["kind"] == "revision_decision" and item["phase"] == 6 for item in result["errors"]))

    def await_local_revision_during_implementation(self) -> None:
        self.state = self.base_state()
        self.approve_planning()
        self.authorize()
        self.state.update(
            current_phase=7,
            status="in_progress",
            next_action="继续无依赖的 I-2；I-1 等待返修决定",
            open_blockers=["I-1 及其下游暂停，等待 implementation.md 返修决定"],
        )
        self.write(self.target, "authorized I-2 implementation\n")
        self.state["implementation_result"].update(
            status="in_progress",
            plan_fingerprint_sha256=self.state["plan_fingerprint_sha256"],
            changes=[{
                "path": str(self.target), "kind": "present", "role": "I-2 owner",
                "sha256": self.digest(self.target),
            }],
            batches=[{"name": "当前批次", "steps": ["I-1", "I-2"], "status": "in_progress"}],
        )
        self.state["consistency_review"]["revision_decision"].update(
            status="awaiting_decision",
            return_phase=5,
            artifacts=["implementation"],
            review_conclusion_sha256=self.state["consistency_review"]["conclusion_sha256"],
        )

    def test_pending_local_revision_preserves_authorized_phase_seven_resume(self) -> None:
        self.await_local_revision_during_implementation()

        completed, result = self.run_check("--context")

        self.assertEqual(0, completed.returncode, result)
        self.assertEqual("fast_path", result["resume_mode"])
        self.assertEqual(7, result["earliest_candidate_phase"])
        context = result["context"]
        self.assertEqual("granted", context["approvals"]["implementation_authorization"]["status"])
        self.assertEqual("awaiting_decision", context["approvals"]["revision_decision"]["status"])
        self.assertEqual(self.state["open_blockers"], context["open_blockers"])
        self.assertEqual(self.state["next_action"], context["next_action"])

    def test_pending_local_revision_does_not_bypass_invalid_authorization_or_drift(self) -> None:
        for change, expected_kind in (
            ("authorization", "implementation_authorization"),
            ("result_binding", "implementation_result"),
            ("review_binding", "consistency_review_binding"),
            ("implementation", "implementation"),
            ("prd", "prd"),
            ("output", "implementation_change"),
        ):
            with self.subTest(change=change):
                self.await_local_revision_during_implementation()
                if change == "authorization":
                    self.state["implementation_authorization"]["plan_fingerprint_sha256"] = "0" * 64
                elif change == "result_binding":
                    self.state["implementation_result"]["plan_fingerprint_sha256"] = "0" * 64
                elif change == "review_binding":
                    self.state["consistency_review"]["plan_fingerprint_sha256"] = "0" * 64
                elif change == "implementation":
                    self.write(self.delivery / "implementation.md", "changed plan\n")
                elif change == "prd":
                    self.write(self.prd, "changed requirement\n")
                else:
                    self.write(self.target, "unregistered output\n")

                completed, result = self.run_check("--context")

                self.assertEqual(1, completed.returncode, result)
                self.assertEqual("targeted_revalidation", result["resume_mode"])
                self.assertNotIn("context", result)
                self.assertIn(expected_kind, {item["kind"] for item in result["errors"] + result["drift"]})

    def test_pending_revision_prevents_implementation_completion(self) -> None:
        for status in ("implemented", "verified"):
            with self.subTest(status=status):
                self.await_local_revision_during_implementation()
                self.verify_implementation()
                self.state["implementation_result"]["status"] = status

                completed, result = self.run_check()

                self.assertEqual(1, completed.returncode, result)
                self.assertIn("revision_decision", {item["kind"] for item in result["errors"]})


    def test_verified_result_requires_complete_manifest(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.state["implementation_result"]["output_manifest_complete"] = False
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("implementation_result", {item["kind"] for item in result["errors"]})


    def test_explicit_user_acceptance_closes_workflow_with_unresolved_verification(self) -> None:
        """Explicit user acceptance may close the workflow while failed checks stay recorded."""
        for outcome in ("no_findings", "findings"):
            with self.subTest(outcome=outcome):
                self.approve_planning()
                self.verify_implementation()
                implementation = self.state["implementation_result"]
                implementation["status"] = "implemented"
                implementation["verification"] = [
                    {"check": "build", "result": "failed", "evidence": "compiler error"},
                    {"check": "device", "result": "blocked", "evidence": "test account unavailable"},
                ]
                self.approve_final_batch(outcome=outcome)
                completed, result = self.run_check()
                self.assertEqual(0, completed.returncode, result)
                self.assertTrue(result["ok"])
                self.assertEqual("implemented", implementation["status"])
                self.assertEqual(["failed", "blocked"], [item["result"] for item in implementation["verification"]])

    def test_verified_cannot_conceal_failed_or_blocked_checks(self) -> None:
        """Relaxing the review gate must not relabel unverified results as passed."""
        for outcome in ("failed", "blocked"):
            with self.subTest(outcome=outcome):
                self.approve_planning()
                self.verify_implementation()
                self.state["implementation_result"]["verification"][0]["result"] = outcome
                completed, result = self.run_check()
                self.assertEqual(1, completed.returncode, result)
                self.assertIn("verification", {item["kind"] for item in result["errors"]})

    def test_implemented_still_requires_complete_recorded_output_and_checks(self) -> None:
        """Failures can be reported, but missing implementation evidence cannot be skipped."""
        for field, value, kind, exit_code in (
            ("output_manifest_complete", False, "implementation_result", 1),
            ("output_fingerprint_sha256", "0" * 64, "output_fingerprint", 1),
            ("verification", [], "verification", 1),
            ("completed_at", None, "implementation_result", 2),
        ):
            with self.subTest(field=field):
                self.approve_planning()
                self.verify_implementation()
                self.state["implementation_result"].update(status="implemented", **{field: value})
                completed, result = self.run_check()
                self.assertEqual(exit_code, completed.returncode, result)
                self.assertIn(kind, {item["kind"] for item in result["errors"]})




    def test_each_batch_waits_for_explicit_approval_before_next_batch(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        result = self.state["implementation_result"]
        result["status"] = "in_progress"
        result["batches"].append({"name": "集成批次", "steps": ["I-2"], "status": "pending"})
        code, payload = self.run_check("--context")
        self.assertEqual(0, code.returncode, payload)
        progress = payload["context"]["implementation_result"]["batch_progress"]
        self.assertEqual(0, progress["approved"])
        self.assertEqual("awaiting_review", progress["current"]["status"])
        self.assertEqual("集成批次", progress["next"]["name"])
        self.assertIn("#implementation_result.batches[0].approval_note", progress["current"]["approval_ref"])
        self.assertNotIn("1 test passed", code.stdout)
        for later_status in ("in_progress", "revising", "awaiting_review", "approved"):
            with self.subTest(later_status=later_status):
                later = copy.deepcopy(result["batches"][0])
                later.update(name="集成批次", steps=["I-2"], status=later_status)
                result["batches"][1] = later
                code, payload = self.run_check()
                self.assertNotEqual(0, code.returncode)
                self.assertIn("batch_order", {item["kind"] for item in payload["errors"]})

    def test_review_checkpoint_requires_workflow_to_stop(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.state["status"] = "in_progress"
        code, payload = self.run_check("--context")
        self.assertEqual(1, code.returncode, payload)
        self.assertNotIn("context", payload)
        self.assertIn("batch_approval", {item["kind"] for item in payload["errors"]})

    def test_approval_requires_user_evidence_and_exact_delivered_version(self) -> None:
        for field in ("approved_at", "approval_note", "approved_input_fingerprint_sha256"):
            with self.subTest(field=field):
                self.approve_planning()
                self.verify_implementation()
                self.approve_final_batch()
                self.state["implementation_result"]["batches"][0][field] = None
                code, payload = self.run_check()
                self.assertNotEqual(0, code.returncode)
                self.assertIn("batch_approval", {item["kind"] for item in payload["errors"]})

    def test_failed_or_blocked_batch_delivery_is_allowed_but_approval_needs_explicit_acceptance(self) -> None:
        for outcome in ("failed", "blocked"):
            with self.subTest(outcome=outcome):
                self.approve_planning()
                self.verify_implementation()
                result = self.state["implementation_result"]
                result["status"] = "implemented"
                result["verification"][0]["result"] = outcome
                self.deliver_final_batch()
                code, payload = self.run_check()
                self.assertEqual(0, code.returncode, payload)
                self.approve_final_batch()
                result["batches"][0]["accepted_unresolved"] = None
                # Acceptance remains mandatory before a non-final batch can advance.
                result["status"] = "in_progress"
                result["batches"].append({"name": "下一批", "steps": ["I-2"], "status": "pending"})
                self.state["status"] = "in_progress"
                code, payload = self.run_check()
                self.assertEqual(1, code.returncode, payload)
                self.assertIn("batch_approval", {item["kind"] for item in payload["errors"]})

    def test_refreshing_review_fingerprints_cannot_renew_existing_approval(self) -> None:
        for mutation in ("output", "verification", "scope"):
            with self.subTest(mutation=mutation):
                self.approve_planning()
                self.verify_implementation()
                self.approve_final_batch()
                result = self.state["implementation_result"]
                batch = result["batches"][0]
                if mutation == "output":
                    self.write(self.target, "modified after user approval\n")
                    result["changes"][0]["sha256"] = self.digest(self.target)
                    batch["changes"] = copy.deepcopy(result["changes"])
                elif mutation == "verification":
                    result["verification"][0]["evidence"] = "different result details"
                    batch["verification"] = copy.deepcopy(result["verification"])
                else:
                    batch["steps"] = ["I-2"]
                self.bind_batch(batch)
                result["output_fingerprint_sha256"] = batch["output_fingerprint_sha256"]
                code, payload = self.run_check()
                self.assertEqual(1, code.returncode, payload)
                self.assertIn("batch_approval", {item["kind"] for item in payload["errors"]})

    def test_current_manifest_cannot_silently_move_past_latest_reviewed_snapshot(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        result = self.state["implementation_result"]
        self.write(self.target, "new content with old batch approval\n")
        result["changes"][0]["sha256"] = self.digest(self.target)
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("batch_output", {item["kind"] for item in payload["errors"]})

    def test_subsequent_batch_can_legitimately_modify_previously_approved_file(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        result = self.state["implementation_result"]
        first = copy.deepcopy(result["batches"][0])
        self.state["status"] = "in_progress"
        result["status"] = "in_progress"
        result["batches"].append({"name": "集成批次", "steps": ["I-2"], "status": "in_progress"})
        self.write(self.target, "changed in later batch\n")
        result["changes"][0]["sha256"] = self.digest(self.target)
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)
        second = result["batches"][1]
        second.update(status="awaiting_review", changes=copy.deepcopy(result["changes"]),
                      verification=copy.deepcopy(result["verification"]))
        self.bind_batch(second)
        result.update(status="verified", output_fingerprint_sha256=second["output_fingerprint_sha256"])
        self.state["status"] = "awaiting_approval"
        code, payload = self.run_check("--fingerprints")
        self.assertEqual(0, code.returncode, payload)
        self.assertEqual(first, result["batches"][0])
        self.assertEqual(first["output_fingerprint_sha256"], payload["fingerprints"]["implementation_batches"][0]["output_fingerprint_sha256"])
        self.assertNotEqual(first["output_fingerprint_sha256"], second["output_fingerprint_sha256"])
        second.update(status="approved", approved_at="2026-09-03T11:00:00+08:00",
                      approval_note="用户通过集成批次", approved_input_fingerprint_sha256=second["review_input_fingerprint_sha256"])
        self.state["status"] = "complete"
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)

    def revise_later_batch_plan(self) -> None:
        path = self.delivery / "implementation.md"
        self.write(path, path.read_text(encoding="utf-8") + "Revise I-2 only; I-1 is unchanged.\n")
        self.state["artifacts"]["implementation"].update(
            sha256=self.digest(path), approved_at="2026-09-03T12:00:00+08:00",
            approval_note="用户批准后批 I-2 的新方案，前批 I-1 不变",
        )
        self.state["plan_fingerprint_sha256"] = canonical_sha256({
            "grounding": self.state["repo_grounding"]["fingerprint_sha256"],
            **{name: self.state["artifacts"][name]["sha256"]
               for name in ("spec", "test", "implementation")},
        })
        self.authorize()
        self.state["implementation_result"]["plan_fingerprint_sha256"] = self.state["plan_fingerprint_sha256"]

    def prepare_replanned_batches(self) -> dict:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        result = self.state["implementation_result"]
        self.state.update(status="in_progress", next_action="按新方案继续第二批")
        result["status"] = "in_progress"
        result["batches"].append({"name": "集成批次", "steps": ["I-2"], "status": "in_progress"})
        self.revise_later_batch_plan()
        return result["batches"][0]

    def record_unaffected_batch(self, batch: dict) -> None:
        batch["plan_revalidation"] = {
            "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
            "approved_input_fingerprint_sha256": batch["approved_input_fingerprint_sha256"],
            "checked_at": "2026-09-03T12:10:00+08:00",
            "note": "对照新方案复核：只调整 I-2，I-1 的契约、范围、输出和验证均不受影响",
        }

    def test_unaffected_batch_approval_survives_replanning_and_final_completion(self) -> None:
        first = self.prepare_replanned_batches()
        original = copy.deepcopy(first)
        self.record_unaffected_batch(first)
        code, payload = self.run_check("--context", "--fingerprints")
        self.assertEqual(0, code.returncode, payload)
        self.assertEqual("集成批次", payload["context"]["implementation_result"]["batch_progress"]["current"]["name"])
        fingerprints = payload["fingerprints"]["implementation_batches"][0]
        self.assertEqual(original["review_input_fingerprint_sha256"], fingerprints["review_input_fingerprint_sha256"])
        self.assertEqual(original["plan_fingerprint_sha256"], fingerprints["plan_fingerprint_sha256"])
        self.assertEqual(original, {key: value for key, value in first.items() if key != "plan_revalidation"})

        result = self.state["implementation_result"]
        self.write(self.target, "completed implementation under revised I-2\n")
        result["changes"][0]["sha256"] = self.digest(self.target)
        last = result["batches"][-1]
        last.update(status="completed", changes=copy.deepcopy(result["changes"]),
                    verification=copy.deepcopy(result["verification"]))
        self.bind_batch(last)
        result.update(status="verified", output_fingerprint_sha256=last["output_fingerprint_sha256"])
        self.state["status"] = "complete"
        code, payload = self.run_check("--context")
        self.assertEqual(0, code.returncode, payload)
        self.assertEqual("complete", payload["context"]["status"])

    def test_old_plan_approval_requires_current_version_bound_impact_revalidation(self) -> None:
        first = self.prepare_replanned_batches()
        self.record_unaffected_batch(first)
        valid = copy.deepcopy(first["plan_revalidation"])
        cases = [None, "checked", {},
                 {**valid, "plan_fingerprint_sha256": first["plan_fingerprint_sha256"]},
                 {**valid, "approved_input_fingerprint_sha256": "0" * 64},
                 {**valid, "checked_at": None}, {**valid, "note": " "}]
        for record in cases:
            with self.subTest(record=record):
                first["plan_revalidation"] = record
                code, payload = self.run_check("--context")
                self.assertNotEqual(0, code.returncode, payload)
                self.assertIn("batch_revalidation", {item["kind"] for item in payload["errors"]})
                self.assertNotIn("context", payload)
                self.assertIn(f"{self.state_path.resolve()}#implementation_result.batches",
                              {item["record_ref"] for item in payload["revalidation_inputs"]})
        first["plan_revalidation"] = valid
        self.revise_later_batch_plan()
        code, payload = self.run_check()
        self.assertNotEqual(0, code.returncode, payload)
        self.assertIn("batch_revalidation", {item["kind"] for item in payload["errors"]})

    def test_impact_revalidation_cannot_replace_approval_or_current_authorization(self) -> None:
        first = self.prepare_replanned_batches()
        self.record_unaffected_batch(first)
        saved = copy.deepcopy(self.state)
        for mutation, expected_kind in (("approval", "batch_approval"), ("output", "batch_approval"),
                                        ("scope", "batch_approval"), ("verification", "batch_approval"),
                                        ("authorization", "implementation_authorization")):
            with self.subTest(mutation=mutation):
                self.state = copy.deepcopy(saved)
                first = self.state["implementation_result"]["batches"][0]
                if mutation == "approval":
                    first["approval_note"] = None
                elif mutation == "authorization":
                    self.state["implementation_authorization"]["plan_fingerprint_sha256"] = first["plan_fingerprint_sha256"]
                else:
                    if mutation == "output":
                        first["changes"][0]["sha256"] = "0" * 64
                    elif mutation == "scope":
                        first["steps"] = ["I-3"]
                    else:
                        first["verification"][0]["evidence"] = "changed verification evidence"
                    self.bind_batch(first)
                code, payload = self.run_check("--context")
                self.assertNotEqual(0, code.returncode, payload)
                self.assertIn(expected_kind, {item["kind"] for item in payload["errors"]})
                self.assertNotIn("context", payload)

    def test_undelivered_or_automatically_completed_batch_cannot_use_old_plan(self) -> None:
        first = self.prepare_replanned_batches()
        self.record_unaffected_batch(first)
        for status in ("awaiting_review", "completed"):
            with self.subTest(status=status):
                last = copy.deepcopy(first)
                last.update(name="集成批次", steps=["I-2"], status=status,
                            approved_at=None, approval_note=None, approved_input_fingerprint_sha256=None)
                self.bind_batch(last)
                result = self.state["implementation_result"]
                result["batches"][-1] = last
                result["status"] = "verified"
                self.state["status"] = "awaiting_approval" if status == "awaiting_review" else "complete"
                code, payload = self.run_check("--context")
                self.assertNotEqual(0, code.returncode, payload)
                self.assertIn("batch_binding", {item["kind"] for item in payload["errors"]})
                self.assertNotIn("context", payload)

    def test_legacy_batch_plan_can_be_backfilled_without_changing_user_approval(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        result = self.state["implementation_result"]
        first = result["batches"][0]
        original_plan = first.pop("plan_fingerprint_sha256")
        for value in ("missing", None):
            if value is None:
                first["plan_fingerprint_sha256"] = None
            code, payload = self.run_check()
            self.assertEqual(0, code.returncode, payload)
        original_approval = first["approved_input_fingerprint_sha256"]
        result["status"] = "in_progress"
        result["batches"].append({"name": "集成批次", "steps": ["I-2"], "status": "in_progress"})
        self.state["status"] = "in_progress"
        self.revise_later_batch_plan()
        self.record_unaffected_batch(first)
        code, payload = self.run_check()
        self.assertNotEqual(0, code.returncode, payload)
        first["plan_fingerprint_sha256"] = original_plan
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)
        self.assertEqual(original_approval, first["approved_input_fingerprint_sha256"])

    def test_revising_batch_can_preserve_old_delivery_without_reusing_approval(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        result = self.state["implementation_result"]
        result["status"] = "in_progress"
        result["batches"][0]["status"] = "revising"
        self.state["status"] = "in_progress"
        self.write(self.target, "user requested correction\n")
        result["changes"][0]["sha256"] = self.digest(self.target)
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)

    def test_invalidated_batches_retain_historical_evidence(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        self.state.update(status="invalidated", current_phase=5, plan_fingerprint_sha256=None)
        self.state["artifacts"]["implementation"]["status"] = "reopened"
        self.state["consistency_review"]["status"] = "invalidated"
        self.state["implementation_authorization"]["status"] = "invalidated"
        result = self.state["implementation_result"]
        result["status"] = "invalidated"
        first = result["batches"][0]
        first["status"] = "invalidated"
        second = copy.deepcopy(first)
        second.update(name="后继失效批次", steps=["I-2"])
        result["batches"].append(second)
        # Current filesystem still has the authorized implementation output;
        # old grounding must be rechecked while stale approvals remain readable.
        self.write(self.target, "old implementation\n")
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("batch_invalidated", {item["kind"] for item in payload["errors"]})
        self.assertNotIn("batch_order", {item["kind"] for item in payload["errors"]})

    def test_revision_can_retain_invalidated_later_batches_without_activating_them(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        result = self.state["implementation_result"]
        result["status"] = "in_progress"
        result["batches"][0]["status"] = "revising"
        result["batches"].extend([
            {"name": "失效批次", "steps": ["I-2"], "status": "invalidated"},
            {"name": "后继批次", "steps": ["I-3"], "status": "pending"},
        ])
        self.state["status"] = "in_progress"
        code, payload = self.run_check("--context")
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("batch_invalidated", {item["kind"] for item in payload["errors"]})
        self.assertNotIn("batch_order", {item["kind"] for item in payload["errors"]})
        self.assertNotIn("context", payload)

    def test_approved_prefix_remains_bound_to_current_output_while_next_batch_is_pending(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        result = self.state["implementation_result"]
        result["status"] = "in_progress"
        result["batches"].append({"name": "下一批", "steps": ["I-2"], "status": "pending"})
        self.state["status"] = "in_progress"
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)
        self.write(self.target, "changed while next batch was not started\n")
        result["changes"][0]["sha256"] = self.digest(self.target)
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("batch_output", {item["kind"] for item in payload["errors"]})

    def test_in_progress_batch_needs_registered_batches(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.state["implementation_result"].update(status="in_progress", batches=[])
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("implementation_batches", {item["kind"] for item in payload["errors"]})

    def test_unfinished_implementation_cannot_complete_even_with_all_user_approvals(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        self.state["implementation_result"]["status"] = "in_progress"
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("workflow_complete", {item["kind"] for item in payload["errors"]})

    def test_final_verification_cannot_change_after_user_review(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        self.state["implementation_result"]["verification"][0]["evidence"] = "changed verification evidence"
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("batch_verification", {item["kind"] for item in payload["errors"]})

    def test_batch_fingerprints_are_available_before_their_values_are_saved(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        batch = self.state["implementation_result"]["batches"][0]
        expected = batch["review_input_fingerprint_sha256"]
        batch["review_input_fingerprint_sha256"] = None
        batch["output_fingerprint_sha256"] = None
        code, payload = self.run_check("--fingerprints")
        self.assertEqual(1, code.returncode, payload)
        computed = payload["fingerprints"]["implementation_batches"][0]
        self.assertEqual(expected, computed["review_input_fingerprint_sha256"])
        self.assertEqual(self.state["implementation_result"]["output_fingerprint_sha256"], computed["output_fingerprint_sha256"])
        self.assertIsNone(batch["approved_input_fingerprint_sha256"])

    def make_legacy_completed_state(self) -> None:
        self.state["schema"] = "requirement-spec/v2"
        result = self.state["implementation_result"]
        result.pop("batches", None)
        conclusion = "Legacy terminal reviewer completed."
        self.state["post_implementation_review"] = {
            "status": "completed", "attempt": 1, "reviewer_ref": "legacy-reviewer",
            "input_fingerprint_sha256": canonical_sha256({
                "output": result["output_fingerprint_sha256"],
                "plan": self.state["plan_fingerprint_sha256"], "verification": result["verification"],
            }),
            "outcome": "no_findings", "conclusion": conclusion,
            "conclusion_sha256": sha256_text(conclusion), "completed_at": "2026-09-03T11:00:00+08:00",
        }
        self.state["status"] = "complete"

    def test_completed_v2_history_is_readable_without_reacceptance(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.make_legacy_completed_state()
        code, payload = self.run_check("--context")
        self.assertEqual(0, code.returncode, payload)
        self.assertEqual("complete", payload["context"]["status"])
        self.assertIn("post_implementation_review", payload["context"]["reviews"])
        self.assertNotIn("batches", self.state["implementation_result"])

    def test_unfinished_v2_cannot_use_old_review_as_user_batch_approval(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.make_legacy_completed_state()
        self.state["status"] = "in_progress"
        code, payload = self.run_check("--context")
        self.assertEqual(1, code.returncode, payload)
        self.assertNotIn("context", payload)
        self.assertIn("schema_migration", {item["kind"] for item in payload["errors"]})
        self.assertIn("not user batch approval", payload["errors"][0]["reason"])

    def test_v2_planning_migration_preserves_existing_approvals(self) -> None:
        self.approve_planning()
        self.state["schema"] = "requirement-spec/v2"
        self.state["implementation_result"].pop("batches")
        approved = copy.deepcopy(self.state["artifacts"])
        code, payload = self.run_check("--context")
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("schema_migration", {item["kind"] for item in payload["errors"]})
        self.state["schema"] = "requirement-spec/v3"
        self.state["implementation_result"]["batches"] = []
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)
        self.assertEqual(approved, self.state["artifacts"])

    def test_single_final_batch_completes_without_any_user_review(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.complete_final_batch()
        code, payload = self.run_check("--context")
        self.assertEqual(0, code.returncode, payload)
        result = payload["context"]["implementation_result"]
        self.assertEqual({"total": 1, "approved": 0, "completed": 1, "current": None, "next": None},
                         result["batch_progress"])
        self.assertFalse(result["auto_finish_ready"])
        batch = self.state["implementation_result"]["batches"][0]
        for field in ("approved_at", "approval_note", "approved_input_fingerprint_sha256",
                      "review_input_fingerprint_sha256", "accepted_unresolved"):
            self.assertIsNone(batch[field])
        self.assertNotIn("post_implementation_review", self.state)

    def test_final_failed_or_blocked_verification_does_not_need_user_acceptance(self) -> None:
        for outcome in ("failed", "blocked"):
            with self.subTest(outcome=outcome):
                self.approve_planning()
                self.verify_implementation()
                result = self.state["implementation_result"]
                result["status"] = "implemented"
                result["verification"][0]["result"] = outcome
                self.complete_final_batch()
                code, payload = self.run_check("--context")
                self.assertEqual(0, code.returncode, payload)
                self.assertIsNone(result["batches"][0]["accepted_unresolved"])
                self.assertEqual(1, payload["context"]["implementation_result"]["verification_counts"][outcome])

    def test_final_automatic_completion_still_requires_all_prior_user_approvals(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        approved = copy.deepcopy(self.state["implementation_result"]["batches"][0])
        self.complete_final_batch()
        result = self.state["implementation_result"]
        result["batches"][0].update(name="最终集成", steps=["I-2"])
        result["batches"].insert(0, approved)
        code, payload = self.run_check()
        self.assertEqual(0, code.returncode, payload)
        for missing in ("status", "approval_note", "approved_input_fingerprint_sha256"):
            with self.subTest(missing=missing):
                result["batches"][0] = copy.deepcopy(approved)
                result["batches"][0][missing] = "awaiting_review" if missing == "status" else None
                code, payload = self.run_check()
                self.assertNotEqual(0, code.returncode)
                self.assertTrue({"batch_order", "batch_approval"} & {item["kind"] for item in payload["errors"]})

    def test_completed_cannot_bypass_user_review_on_an_intermediate_batch(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.complete_final_batch()
        result = self.state["implementation_result"]
        later = copy.deepcopy(result["batches"][0])
        later.update(name="最终集成", steps=["I-2"])
        result["batches"].append(later)
        code, payload = self.run_check()
        self.assertEqual(1, code.returncode, payload)
        self.assertIn("batch_order", {item["kind"] for item in payload["errors"]})

    def test_completed_final_batch_must_not_claim_user_approval(self) -> None:
        for field, value in (("approved_at", "2026-09-03T11:00:00+08:00"),
                             ("approval_note", "invented acceptance"),
                             ("approved_input_fingerprint_sha256", "0" * 64)):
            with self.subTest(field=field):
                self.approve_planning()
                self.verify_implementation()
                self.complete_final_batch()
                self.state["implementation_result"]["batches"][0][field] = value
                code, payload = self.run_check()
                self.assertEqual(1, code.returncode, payload)
                self.assertIn("batch_approval", {item["kind"] for item in payload["errors"]})

    def test_automatic_completion_keeps_implementation_evidence_and_authorization_gates(self) -> None:
        for mutation in ("unfinished", "invalidated", "manifest", "snapshot", "verification", "binding", "authorization", "drift"):
            with self.subTest(mutation=mutation):
                self.approve_planning()
                self.verify_implementation()
                self.complete_final_batch()
                result = self.state["implementation_result"]
                if mutation == "unfinished":
                    result["status"] = "in_progress"
                elif mutation == "invalidated":
                    result["batches"][0]["status"] = "invalidated"
                elif mutation == "manifest":
                    result["output_manifest_complete"] = False
                elif mutation == "snapshot":
                    result["batches"][0]["changes"] = []
                elif mutation == "verification":
                    result["batches"][0]["verification"] = []
                elif mutation == "binding":
                    result["plan_fingerprint_sha256"] = "0" * 64
                elif mutation == "authorization":
                    self.state["implementation_authorization"]["status"] = "invalidated"
                else:
                    self.write(self.target, "unauthorized output drift\n")
                code, payload = self.run_check("--context")
                self.assertNotEqual(0, code.returncode, payload)
                self.assertNotIn("context", payload)

    def test_old_final_awaiting_review_context_directs_automatic_close_without_writing(self) -> None:
        for outcome in ("passed", "failed", "blocked"):
            with self.subTest(outcome=outcome):
                self.approve_planning()
                self.verify_implementation()
                result = self.state["implementation_result"]
                result["status"] = "verified" if outcome == "passed" else "implemented"
                result["verification"][0]["result"] = outcome
                self.deliver_final_batch()
                self.run_check()
                before = self.state_path.read_bytes()
                code, payload = self.invoke_check("--context")
                self.assertEqual(0, code.returncode, payload)
                self.assertTrue(payload["context"]["implementation_result"]["auto_finish_ready"])
                self.assertIn("无需用户批准", payload["context"]["next_action"])
                self.assertEqual(before, self.state_path.read_bytes())
                self.assertEqual("awaiting_review", result["batches"][0]["status"])

    def test_old_review_checkpoint_only_auto_closes_complete_valid_final_delivery(self) -> None:
        for mutation in ("unfinished", "nonfinal", "missing_verification", "invalidated", "drift"):
            with self.subTest(mutation=mutation):
                self.approve_planning()
                self.verify_implementation()
                result = self.state["implementation_result"]
                if mutation in {"unfinished", "nonfinal"}:
                    result["status"] = "in_progress"
                    if mutation == "nonfinal":
                        result["batches"].append({"name": "还有一批", "steps": ["I-2"], "status": "pending"})
                elif mutation == "missing_verification":
                    result["verification"] = []
                elif mutation == "invalidated":
                    result["batches"][0]["status"] = "invalidated"
                else:
                    self.write(self.target, "output drift before automatic close\n")
                code, payload = self.run_check("--context")
                if code.returncode == 0:
                    self.assertFalse(payload["context"]["implementation_result"]["auto_finish_ready"])
                else:
                    self.assertNotIn("context", payload)

    def test_rejects_legacy_v1_schema_with_migration_hint(self) -> None:
        self.state["schema"] = "requirement-spec/v1"
        completed, result = self.run_check()
        self.assertEqual(2, completed.returncode)
        reasons = " ".join(item["reason"] for item in result["errors"])
        self.assertIn("migrate v1", reasons)

    def test_context_is_opt_in_and_preserves_default_json(self) -> None:
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertEqual(
            {"ok", "schema", "feature_slug", "current_phase", "resume_mode",
             "earliest_candidate_phase", "drift", "errors"},
            set(result),
        )
        contextual, enriched = self.invoke_check("--context")
        self.assertEqual(completed.returncode, contextual.returncode)
        self.assertEqual(result, {key: value for key, value in enriched.items()
                                  if key not in {"context", "state_sha256"}})
        self.assertEqual(sha256_bytes(self.state_path.read_bytes()), enriched["state_sha256"])

    def test_fingerprints_report_bookkeeping_values_without_writing_files(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch()
        self.run_check()
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }
        completed, result = self.invoke_check("--context", "--fingerprints")
        self.assertEqual(0, completed.returncode, result)
        self.assertIn("context", result)
        fingerprints = result["fingerprints"]
        self.assertEqual({
            str(path.resolve()): self.digest(path) for path in (
                self.prd, self.rule, self.target,
                *(self.delivery / f"{name}.md" for name in ("spec", "test", "implementation")),
            )
        }, fingerprints["files"])
        self.assertEqual({
            "repo_grounding.fingerprint_sha256": self.state["repo_grounding"]["fingerprint_sha256"],
            "plan_fingerprint_sha256": self.state["plan_fingerprint_sha256"],
            "implementation_result.output_fingerprint_sha256": self.state["implementation_result"]["output_fingerprint_sha256"],
        }, fingerprints["recorded_inputs"])
        self.assertEqual({
            f"{name}.conclusion_sha256": self.state[name]["conclusion_sha256"]
            for name in ("consistency_review",)
        }, fingerprints["review_conclusions"])
        self.assertEqual(before, {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        })

    def test_fingerprints_keep_recorded_plan_after_authorized_source_changes(self) -> None:
        self.approve_planning()
        baseline = self.state["repo_grounding"]["fingerprint_sha256"]
        plan = self.state["plan_fingerprint_sha256"]
        self.verify_implementation()
        completed, result = self.run_check("--fingerprints")
        self.assertEqual(0, completed.returncode, result)
        fingerprints = result["fingerprints"]
        self.assertNotEqual(self.state["repo_grounding"]["relevant_targets"][0]["sha256"],
                            fingerprints["files"][str(self.target.resolve())])
        self.assertEqual(baseline, fingerprints["recorded_inputs"]["repo_grounding.fingerprint_sha256"])
        self.assertEqual(plan, fingerprints["recorded_inputs"]["plan_fingerprint_sha256"])

    def test_fingerprints_include_first_drafts_and_unhashed_saved_conclusions(self) -> None:
        draft = self.delivery / "spec.md"
        pack = self.workflow / "evidence.md"
        self.write(draft, "new draft\n")
        self.write(pack, "new evidence\n")
        self.state["artifacts"]["spec"].update(status="reopened", sha256=None)
        self.state["inputs"]["prd"]["sha256"] = None
        self.state["repo_grounding"]["evidence_pack"] = {"path": pack.name, "sha256": None}
        self.state["consistency_review"]["conclusion"] = "A saved conclusion without its hash."
        completed, result = self.run_check("--fingerprints")
        self.assertEqual(2, completed.returncode, result)
        fingerprints = result["fingerprints"]
        for path in (self.prd, draft, pack):
            self.assertEqual(self.digest(path), fingerprints["files"][str(path.resolve())])
        self.assertIsNone(fingerprints["files"][str((self.delivery / "test.md").resolve())])
        self.assertIsNone(fingerprints["recorded_inputs"]["plan_fingerprint_sha256"])
        self.assertEqual(sha256_text(self.state["consistency_review"]["conclusion"]),
                         fingerprints["review_conclusions"]["consistency_review.conclusion_sha256"])
        self.assertNotIn("post_implementation_review.conclusion_sha256", fingerprints["review_conclusions"])

    def test_context_routes_minimal_inputs_for_each_phase(self) -> None:
        expected = {
            1: ["prd"], 2: ["prd"], 3: ["prd", "spec"],
            4: ["spec", "test"], 5: ["spec", "test", "implementation"],
            6: ["prd", "spec", "test", "implementation"],
            7: ["spec", "test", "implementation"],
        }
        for phase, roles in expected.items():
            with self.subTest(phase=phase):
                self.approve_planning()
                if phase < 6:
                    self.state["plan_fingerprint_sha256"] = None
                    for name, artifact_phase in {"spec": 3, "test": 4, "implementation": 5}.items():
                        if artifact_phase >= phase:
                            self.state["artifacts"][name]["status"] = (
                                "awaiting_approval" if artifact_phase == phase else "not_started"
                            )
                self.state["current_phase"] = phase
                completed, result = self.run_check("--context")
                self.assertEqual(0, completed.returncode, result)
                context = result["context"]
                self.assertEqual(roles, [item["role"] for item in context["inputs"]])
                self.assertNotIn("design_targets_ref", context)
                self.assertNotIn(str(self.target), json.dumps(context))
                self.assertNotIn(str(self.rule), json.dumps(context))
                if phase <= 3:
                    self.assertEqual("superseded", context["approvals"]["direction"]["status"])
                if phase < 6:
                    self.assertEqual({}, context["reviews"])
                if phase < 7:
                    self.assertNotIn("implementation_authorization", context["approvals"])
                else:
                    self.assertEqual("not_granted", context["approvals"]["implementation_authorization"]["status"])

    def test_context_preserves_supplemental_design_targets_as_a_compact_reference(self) -> None:
        target_url = "https://www.figma.com/design/checkout/Checkout?node-id=42-123"
        self.state["inputs"]["design_targets"] = [
            {"url": target_url, "node_id": "42:123", "description": "Supplemental design"}
        ]
        for phase in (2, 7):
            with self.subTest(phase=phase):
                if phase == 7:
                    self.approve_planning()
                self.state["current_phase"] = phase
                completed, result = self.run_check("--context")
                self.assertEqual(0, completed.returncode, result)
                context = result["context"]
                self.assertEqual(
                    f"{self.state_path.resolve()}#inputs.design_targets",
                    context["design_targets_ref"],
                )
                self.assertNotIn("design_targets", context)
                self.assertNotIn(target_url, completed.stdout)
                self.assertNotIn("42:123", completed.stdout)
                self.assertNotIn("Supplemental design", completed.stdout)

    def test_context_uses_recorded_paths_and_skips_unstarted_or_missing_documents(self) -> None:
        self.state["current_phase"] = 3
        custom = self.root / "custom" / "contract.md"
        self.write(custom, "existing draft")
        artifact = self.state["artifacts"]["spec"]
        # The state directory is docs/spec/.workflow/feature, four levels deep.
        artifact.update(path="../../../../custom/contract.md", status="reopened")
        completed, result = self.run_check("--context")
        self.assertEqual(0, completed.returncode, result)
        self.assertEqual(str(custom.resolve()), result["context"]["inputs"][1]["path"])
        for status, remove in (("not_started", False), ("reopened", True)):
            with self.subTest(status=status):
                artifact["status"] = status
                if remove:
                    custom.unlink()
                completed, result = self.run_check("--context")
                self.assertEqual(0, completed.returncode, result)
                self.assertEqual(["prd"], [item["role"] for item in result["context"]["inputs"]])

    def test_context_only_includes_a_relevant_valid_grounding_pack(self) -> None:
        pack = self.workflow / "saved-evidence.md"
        self.write(pack, "saved technical evidence")
        self.state["repo_grounding"]["evidence_pack"] = {
            "path": pack.name,
            "sha256": self.digest(pack),
            "input_fingerprint_sha256": self.state["repo_grounding"]["fingerprint_sha256"],
        }
        for phase, roles in ((1, ["prd"]), (2, ["prd", "grounding_pack"])):
            self.state["current_phase"] = phase
            completed, result = self.run_check("--context")
            self.assertEqual(0, completed.returncode, result)
            self.assertEqual(roles, [item["role"] for item in result["context"]["inputs"]])
        self.state["repo_grounding"]["status"] = "invalidated"
        completed, result = self.run_check("--context")
        self.assertEqual(0, completed.returncode, result)
        self.assertEqual(["prd"], [item["role"] for item in result["context"]["inputs"]])

    def test_context_preserves_pending_revision_and_batch_review_pointers(self) -> None:
        self.approve_planning()
        self.pass_consistency_review()
        review = self.state["consistency_review"]
        conclusion = "An editorial correction requires a revision decision."
        review.update(status="completed", outcome="findings", conclusion=conclusion,
                      conclusion_sha256=sha256_text(conclusion))
        review["revision_decision"].update(
            status="awaiting_decision", return_phase=5, artifacts=["implementation"],
            review_conclusion_sha256=review["conclusion_sha256"],
        )
        self.state.update(status="awaiting_approval", next_action="等待用户决定是否返修 implementation.md",
                          open_blockers=["返修尚未授权"])
        completed, result = self.run_check("--context")
        self.assertEqual(0, completed.returncode, result)
        context = result["context"]
        self.assertEqual(self.state["next_action"], context["next_action"])
        self.assertEqual(self.state["open_blockers"], context["open_blockers"])
        self.assertEqual("awaiting_decision", context["approvals"]["revision_decision"]["status"])
        self.assertEqual(["implementation"], context["approvals"]["revision_decision"]["artifacts"])
        self.assertEqual("findings", context["reviews"]["consistency_review"]["outcome"])
        self.assertNotIn("implementation_authorization", context["approvals"])
        self.state = self.base_state()
        self.approve_planning()
        self.verify_implementation()
        self.approve_final_batch(outcome="findings")
        completed, result = self.run_check("--context")
        self.assertEqual(0, completed.returncode, result)
        context = result["context"]
        self.assertEqual({"passed": 1, "failed": 0, "blocked": 0}, context["implementation_result"]["verification_counts"])
        self.assertEqual(f"{self.state_path.resolve()}#implementation_result.verification", context["implementation_result"]["verification_ref"])
        self.assertNotIn("post_implementation_review", context["reviews"])
        progress = context["implementation_result"]["batch_progress"]
        self.assertEqual({"total": 1, "approved": 1, "completed": 0, "current": None, "next": None}, progress)
        self.assertNotIn("conclusion_sha256", json.dumps(context))
        self.assertNotIn("fingerprint_sha256", json.dumps(context))

    def test_context_suppresses_stale_actions_and_hashes_on_drift(self) -> None:
        self.state["next_action"] = "must not suggest this stale action"
        self.state["inputs"]["design_targets"] = [
            {"url": "https://www.figma.com/design/checkout/Checkout?node-id=42-123"}
        ]
        self.write(self.target, "changed source content")
        completed, plain = self.run_check()
        contextual, result = self.invoke_check("--context")
        self.assertEqual(1, contextual.returncode)
        self.assertEqual(completed.returncode, contextual.returncode)
        self.assertNotIn("context", result)
        self.assertNotIn("#inputs.design_targets", contextual.stdout)
        self.assertNotIn("figma.com", contextual.stdout)
        self.assertNotIn(self.state["next_action"], contextual.stdout)
        self.assertNotIn("expected", result["drift"][0])
        self.assertNotIn("actual", result["drift"][0])
        self.assertIn("expected", plain["drift"][0])
        self.assertEqual([{
            "phase": 2, "path": str(self.target.resolve()),
            "record_ref": f"{self.state_path.resolve()}#repo_grounding.relevant_targets",
        }], result["revalidation_inputs"])
        self.assertNotIn("changed source content", contextual.stdout)

    def test_context_preserves_revision_authorization_after_return_to_writing(self) -> None:
        artifact_phases = {"spec": 3, "test": 4, "implementation": 5}
        for name, phase in artifact_phases.items():
            with self.subTest(phase=phase):
                self.state = self.base_state()
                self.approve_planning()
                self.pass_consistency_review()
                review = self.state["consistency_review"]
                review["status"] = "invalidated"
                review["revision_decision"].update(
                    status="authorized", return_phase=phase, artifacts=[name],
                    review_conclusion_sha256=review["conclusion_sha256"],
                    decided_at="2026-09-03T11:00:00+08:00", decision_note="Revise the named document",
                )
                for artifact, artifact_phase in artifact_phases.items():
                    if artifact_phase >= phase:
                        self.state["artifacts"][artifact]["status"] = "reopened"
                self.state.update(current_phase=phase, plan_fingerprint_sha256=None)
                completed, result = self.run_check("--context")
                self.assertEqual(0, completed.returncode, result)
                context = result["context"]
                self.assertEqual("authorized", context["approvals"]["revision_decision"]["status"])
                self.assertEqual([name], context["approvals"]["revision_decision"]["artifacts"])
                self.assertEqual("invalidated", context["reviews"]["consistency_review"]["status"])
                self.assertIn("conclusion_ref", context["reviews"]["consistency_review"])
                self.assertNotIn("implementation_authorization", context["approvals"])

    def test_context_keeps_only_recorded_workspace_references(self) -> None:
        completed, result = self.run_check("--context")
        self.assertEqual(0, completed.returncode, result)
        self.assertNotIn("workspace", result["context"])
        self.state["workspace"] = {
            "workspace_path": "../workspace.yaml", "module_slug": "module-one",
            "runtime_history": ["must not be included"],
        }
        completed, result = self.run_check("--context")
        self.assertEqual(0, completed.returncode, result)
        self.assertEqual({"workspace_path": "../workspace.yaml", "module_slug": "module-one"},
                         result["context"]["workspace"])

    def test_context_invalid_state_retains_failure_code_without_execution_hints(self) -> None:
        for mutation in (lambda: self.state.update(schema="requirement-spec/v1"),
                         lambda: self.state.update(artifacts=None)):
            with self.subTest(mutation=mutation):
                self.state = self.base_state()
                mutation()
                completed, result = self.run_check("--context")
                self.assertEqual(2, completed.returncode, result)
                self.assertNotIn("context", result)
                self.assertEqual(sha256_bytes(self.state_path.read_bytes()), result["state_sha256"])
        self.write(self.state_path, "invalid: [state-content-must-not-be-echoed")
        completed, result = self.invoke_check("--context")
        self.assertEqual(2, completed.returncode)
        self.assertNotIn("context", result)
        self.assertNotIn("state-content-must-not-be-echoed", completed.stdout)
        self.assertEqual(sha256_bytes(self.state_path.read_bytes()), result["state_sha256"])
        self.state_path.unlink()
        completed, result = self.invoke_check("--context")
        self.assertEqual(2, completed.returncode)
        self.assertIsNone(result["state_sha256"])

    def test_context_projection_does_not_read_document_content(self) -> None:
        self.approve_planning()
        code, validated = validate_state(self.state, self.state_path)
        self.assertEqual(0, code)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected content read")), \
                patch.object(Path, "read_text", side_effect=AssertionError("unexpected text read")):
            result = context_payload(validated, self.state, self.state_path)
        self.assertEqual(4, len(result["context"]["inputs"]))

    def test_context_reads_state_bytes_once_for_parsing_and_digest(self) -> None:
        self.run_check()
        reads: list[Path] = []
        original = Path.read_bytes

        def read_bytes(path: Path) -> bytes:
            reads.append(path.resolve())
            return original(path)

        with patch.object(sys, "argv", [str(CHECKER), "--context", str(self.state_path)]), \
                patch.object(Path, "read_bytes", read_bytes), patch("builtins.print"):
            self.assertEqual(0, main())
        self.assertEqual(1, reads.count(self.state_path.resolve()))

    def test_context_cli_never_mutates_files(self) -> None:
        self.approve_planning()
        self.run_check()

        def snapshot() -> dict:
            return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                    for path in self.root.rglob("*") if path.is_file()}

        for drift in (False, True):
            if drift:
                self.write(self.target, "unregistered change")
            before = snapshot()
            completed, result = self.invoke_check("--context")
            self.assertEqual(1 if drift else 0, completed.returncode, result)
            self.assertEqual(before, snapshot())


if __name__ == "__main__":
    unittest.main()
