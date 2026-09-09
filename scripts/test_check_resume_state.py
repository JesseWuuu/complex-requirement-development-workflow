from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.check_resume_state import canonical_sha256, sha256_text


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
            "schema": "requirement-spec/v2",
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
                "completed_at": None,
            },
            "post_implementation_review": {
                "status": "not_started",
                "input_fingerprint_sha256": None,
                "reviewer_ref": None,
                "attempt": 0,
                "outcome": None,
                "conclusion": None,
                "conclusion_sha256": None,
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
        self.state["next_action"] = "执行终结代码审阅"

    def complete_post_review(self, *, outcome: str = "no_findings") -> None:
        result = self.state["implementation_result"]
        input_fingerprint = canonical_sha256(
            {
                "output": result["output_fingerprint_sha256"],
                "plan": self.state["plan_fingerprint_sha256"],
                "verification": result["verification"],
            }
        )
        conclusion = "No findings." if outcome == "no_findings" else "One non-blocking finding."
        self.state["post_implementation_review"] = {
            "status": "completed",
            "input_fingerprint_sha256": input_fingerprint,
            "reviewer_ref": "reviewer-phase-7",
            "attempt": 1,
            "outcome": outcome,
            "conclusion": conclusion,
            "conclusion_sha256": sha256_text(conclusion),
            "completed_at": "2026-09-03T10:50:00+08:00",
        }
        self.state["status"] = "complete"
        self.state["next_action"] = "汇报最终结果"

    def run_check(self) -> tuple[subprocess.CompletedProcess[str], dict]:
        self.state_path.write_text(
            yaml.safe_dump(self.state, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, str(CHECKER), str(self.state_path)],
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
        self.complete_post_review()
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertNotIn("delegation", self.state)
        self.assertTrue(result["ok"])

    def test_findings_are_a_valid_terminal_review_outcome(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.complete_post_review(outcome="findings")
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

    def test_complete_workflow_requires_terminal_review(self) -> None:
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

    def test_accepts_completed_phase_six_findings_awaiting_revision_decision(self) -> None:
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
        completed, result = self.run_check()
        self.assertEqual(0, completed.returncode)
        self.assertTrue(result["ok"])

    def test_verified_result_requires_complete_manifest(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.state["implementation_result"]["output_manifest_complete"] = False
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("implementation_result", {item["kind"] for item in result["errors"]})

    def test_failed_or_blocked_checks_allow_terminal_review_to_start(self) -> None:
        """Code completion permits review even when no verification could pass."""
        for outcomes in (["failed"], ["blocked"], ["passed", "failed", "blocked"]):
            with self.subTest(outcomes=outcomes):
                self.approve_planning()
                self.verify_implementation()
                implementation = self.state["implementation_result"]
                implementation["status"] = "implemented"
                implementation["verification"] = [
                    {"check": f"check {index}", "result": outcome, "evidence": f"recorded {outcome}"}
                    for index, outcome in enumerate(outcomes)
                ]
                self.complete_post_review()
                self.state["status"] = "in_progress"
                review = self.state["post_implementation_review"]
                review.update(status="in_progress", outcome=None, conclusion=None, conclusion_sha256=None, completed_at=None)
                completed, result = self.run_check()
                self.assertEqual(0, completed.returncode, result)
                self.assertEqual("fast_path", result["resume_mode"])
                saved = yaml.safe_load(self.state_path.read_text(encoding="utf-8"))
                self.assertEqual(implementation["verification"], saved["implementation_result"]["verification"])

    def test_review_completion_closes_workflow_with_unresolved_verification(self) -> None:
        """Both review outcomes may close the workflow while failed checks stay recorded."""
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
                self.complete_post_review(outcome=outcome)
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

    def test_unfinished_implementation_cannot_skip_to_terminal_review(self) -> None:
        """Only verification problems are non-blocking; unfinished code is not review-ready."""
        self.approve_planning()
        self.verify_implementation()
        self.state["implementation_result"]["status"] = "in_progress"
        self.complete_post_review()
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        kinds = {item["kind"] for item in result["errors"]}
        self.assertIn("post_implementation_review_binding", kinds)
        self.assertIn("workflow_complete", kinds)

    def test_implemented_review_remains_bound_to_code_authorization_and_verification(self) -> None:
        """Recorded failures do not weaken approval, output drift, or review fingerprint checks."""
        for changed in ("authorization", "code", "verification"):
            with self.subTest(changed=changed):
                self.approve_planning()
                self.verify_implementation()
                implementation = self.state["implementation_result"]
                implementation["status"] = "implemented"
                implementation["verification"][0]["result"] = "blocked"
                self.complete_post_review()
                if changed == "authorization":
                    self.state["implementation_authorization"]["status"] = "invalidated"
                    expected_kind = "implementation_result"
                elif changed == "code":
                    self.write(self.target, "changed after review\n")
                    expected_kind = "implementation_change"
                else:
                    implementation["verification"][0]["evidence"] = "different missing condition"
                    expected_kind = "post_implementation_review_binding"
                completed, result = self.run_check()
                self.assertEqual(1, completed.returncode, result)
                self.assertIn(expected_kind, {item["kind"] for item in result["errors"] + result["drift"]})

    def test_rejects_terminal_review_after_verification_changes(self) -> None:
        self.approve_planning()
        self.verify_implementation()
        self.complete_post_review()
        self.state["implementation_result"]["verification"][0]["evidence"] = "different evidence"
        completed, result = self.run_check()
        self.assertEqual(1, completed.returncode)
        self.assertIn("post_implementation_review_binding", {item["kind"] for item in result["errors"]})

    def test_rejects_legacy_v1_schema_with_migration_hint(self) -> None:
        self.state["schema"] = "requirement-spec/v1"
        completed, result = self.run_check()
        self.assertEqual(2, completed.returncode)
        reasons = " ".join(item["reason"] for item in result["errors"])
        self.assertIn("migrate v1", reasons)


if __name__ == "__main__":
    unittest.main()
