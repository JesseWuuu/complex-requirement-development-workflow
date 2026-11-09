"""Exercise the split workflow without rewriting legacy approval semantics."""
from __future__ import annotations

import copy
import unittest

from scripts import test_check_resume_state as fixtures
from scripts.check_resume_state import canonical_sha256, context_payload, validate_state


class SplitWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResumeStateCheckTest(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.state = self.fixture.state
        self.state["schema"] = "requirement-spec/v4"
        self.state["artifacts"]["technical_design"] = {
            "status": "not_started",
            "path": str(self.fixture.delivery / "technical-design.md"),
            "sha256": None, "approved_at": None, "approval_note": None,
        }

    def approve(self, name):
        record = self.state["artifacts"][name]
        from pathlib import Path
        path = Path(record["path"])
        self.fixture.write(path, name + " approved content\n")
        record.update(status="approved", sha256=self.fixture.digest(path),
                      approved_at="2026-11-09T12:00:00+08:00", approval_note="用户确认 " + name)

    def plan(self):
        for name in self.state["artifacts"]:
            self.approve(name)
        self.state["direction"]["status"] = "superseded"
        self.state["current_phase"] = 7
        self.rehash_plan()

    def rehash_plan(self):
        self.state["plan_fingerprint_sha256"] = canonical_sha256({
            "grounding": self.state["repo_grounding"]["fingerprint_sha256"],
            **{name: record["sha256"] for name, record in self.state["artifacts"].items()},
        })

    def check(self):
        code, result = validate_state(self.state, self.fixture.state_path, include_fingerprints=True)
        return code, context_payload(result, self.state, self.fixture.state_path)

    def test_new_template_records_four_artifacts(self):
        import yaml
        from scripts.test_check_resume_state import SKILL_ROOT
        template = yaml.safe_load((SKILL_ROOT / "assets/workflow-state.yaml").read_text())
        self.assertEqual("requirement-spec/v4", template["schema"])
        self.assertEqual(set(self.state["artifacts"]), set(template["artifacts"]))
        self.assertTrue(template["artifacts"]["technical_design"]["path"].endswith("technical-design.md"))

    def test_context_routes_design_then_plan_then_review(self):
        self.approve("spec")
        self.approve("test")
        self.state["current_phase"] = 5
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertEqual({"spec", "test"}, {i["role"] for i in result["context"]["inputs"]})
        self.approve("technical_design")
        self.state["current_phase"] = 6
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertEqual({"spec", "test", "technical_design"}, {i["role"] for i in result["context"]["inputs"]})
        self.approve("implementation")
        self.rehash_plan()
        self.state["current_phase"] = 7
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertEqual({"prd", "spec", "test", "technical_design", "implementation"},
                         {i["role"] for i in result["context"]["inputs"]})

    def test_cannot_submit_implementation_before_design_approval(self):
        self.approve("spec")
        self.approve("test")
        self.approve("implementation")
        self.state["artifacts"]["implementation"]["status"] = "awaiting_approval"
        self.state["current_phase"] = 5
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertTrue(any(e["kind"] == "approval_order" and e["phase"] == 5 for e in result["errors"]))
        self.assertNotIn("context", result)

    def test_cannot_advance_phase_six_with_unapproved_design(self):
        self.approve("spec")
        self.approve("test")
        self.state["current_phase"] = 6
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertEqual(5, result["earliest_candidate_phase"])

    def prepare_joint_revision(self, names=("technical_design", "implementation")):
        self.plan()
        self.fixture.pass_consistency_review()
        review = self.state["consistency_review"]
        review["status"] = "invalidated"
        phase = 4 if "test" in names else 5
        review["revision_decision"].update(
            status="authorized", return_phase=phase, artifacts=list(names),
            review_conclusion_sha256=review["conclusion_sha256"],
            decided_at="2026-11-09T12:00:00+08:00",
            decision_note="用户授权联合返修这些文档，完成后联合审阅",
        )
        self.state.update(current_phase=phase, status="awaiting_approval",
                          plan_fingerprint_sha256=None, next_action="等待联合批准")
        for name in names:
            self.state["artifacts"][name].update(
                status="awaiting_approval", approved_at=None, approval_note=None,
            )

    def test_authorized_joint_revision_restores_all_pending_documents(self):
        for names in (("technical_design", "implementation"),
                      ("test", "technical_design", "implementation")):
            with self.subTest(names=names):
                self.prepare_joint_revision(names)
                for phase in range(self.state["current_phase"], 7):
                    self.state["current_phase"] = phase
                    code, result = self.check()
                    self.assertEqual(0, code, result)
                    self.assertEqual(set(self.state["artifacts"]),
                                     {item["role"] for item in result["context"]["inputs"]})
                    for name in names:
                        self.assertEqual("awaiting_approval", result["context"]["approvals"][name]["status"])
                    self.assertIsNone(result["fingerprints"]["recorded_inputs"]["plan_fingerprint_sha256"])

    def test_joint_revision_requires_current_explicit_authorization(self):
        mutations = (
            {"status": "awaiting_decision"},
            {"status": "declined"},
            {"status": "invalidated"},
            {"artifacts": ["implementation"]},
            {"review_conclusion_sha256": "a" * 64},
            {"decided_at": None},
            {"decision_note": None},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.prepare_joint_revision()
                self.state["consistency_review"]["revision_decision"].update(mutation)
                code, result = self.check()
                self.assertNotEqual(0, code)
                self.assertTrue(any(e["kind"] == "approval_order" for e in result["errors"]))
                self.assertNotIn("context", result)

    def test_joint_revision_does_not_approve_downstream_or_advance_to_review(self):
        for mutation in ("approve_implementation", "review", "execute"):
            with self.subTest(mutation=mutation):
                self.prepare_joint_revision()
                if mutation == "approve_implementation":
                    self.approve("implementation")
                else:
                    self.state["current_phase"] = 7 if mutation == "review" else 8
                code, result = self.check()
                self.assertNotEqual(0, code)
                self.assertTrue(any(e["kind"] == "approval_order" for e in result["errors"]))
                self.assertNotIn("context", result)

    def test_joint_revision_still_detects_document_drift(self):
        from pathlib import Path
        self.prepare_joint_revision()
        self.fixture.write(Path(self.state["artifacts"]["implementation"]["path"]), "changed after delivery")
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertTrue(any(e["kind"] == "implementation" for e in result["drift"]))
        self.assertNotIn("context", result)

    def test_joint_approval_requires_new_review_before_execution(self):
        self.prepare_joint_revision()
        for name in ("technical_design", "implementation"):
            self.approve(name)
        self.rehash_plan()
        self.state.update(current_phase=7, status="in_progress")
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.state["current_phase"] = 8
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertTrue(any(e["kind"] == "consistency_review" for e in result["errors"]))

    def test_missing_design_cannot_be_hidden_by_schema_change(self):
        del self.state["artifacts"]["technical_design"]
        code, result = self.check()
        self.assertEqual(2, code)
        self.assertTrue(any(e["kind"] == "technical_design" for e in result["errors"]))

    def test_design_drift_returns_phase_five_without_old_context(self):
        self.plan()
        self.fixture.pass_consistency_review()
        from pathlib import Path
        self.fixture.write(Path(self.state["artifacts"]["technical_design"]["path"]), "changed mechanism")
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertEqual(5, result["earliest_candidate_phase"])
        self.assertNotIn("context", result)
        self.assertTrue(any(i["record_ref"].endswith("#artifacts.technical_design") for i in result["revalidation_inputs"]))

    def test_design_reapproval_invalidates_old_review_and_authorization(self):
        self.plan()
        self.fixture.authorize()
        old_plan = self.state["plan_fingerprint_sha256"]
        self.state["artifacts"]["technical_design"]["sha256"] = "a" * 64
        self.rehash_plan()
        self.assertNotEqual(old_plan, self.state["plan_fingerprint_sha256"])
        code, result = self.check()
        kinds = {e["kind"] for e in result["errors"]}
        self.assertNotEqual(0, code)
        self.assertIn("consistency_review_binding", kinds)
        self.assertIn("implementation_authorization", kinds)

    def test_phase_eight_requires_review_before_implementation(self):
        self.plan()
        self.state["current_phase"] = 8
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertEqual(7, result["earliest_candidate_phase"])

    def test_phase_eight_finishes_last_batch_automatically(self):
        self.plan()
        self.fixture.verify_implementation()
        self.state["current_phase"] = 8
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertTrue(result["context"]["implementation_result"]["auto_finish_ready"])
        self.fixture.complete_final_batch()
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertEqual("complete", result["context"]["status"])
        self.assertEqual(1, result["context"]["implementation_result"]["batch_progress"]["completed"])

    def test_execution_diagnostics_use_phase_eight(self):
        self.plan()
        self.fixture.verify_implementation()
        self.state["current_phase"] = 8
        self.state["implementation_result"]["batches"] = []
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertTrue(any(e["kind"] == "implementation_batches" and e["phase"] == 8 for e in result["errors"]))

    def test_legacy_approval_hashes_are_unchanged(self):
        self.state["schema"] = "requirement-spec/v3"
        del self.state["artifacts"]["technical_design"]
        self.fixture.approve_planning()
        old_plan = self.state["plan_fingerprint_sha256"]
        before = copy.deepcopy(self.state)
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertEqual(old_plan, result["fingerprints"]["recorded_inputs"]["plan_fingerprint_sha256"])
        self.assertEqual(before, self.state)
        self.assertEqual("legacy_continuation", result["schema_compatibility"]["mode"])
        self.fixture.verify_implementation()
        self.fixture.complete_final_batch()
        code, result = self.check()
        self.assertEqual(0, code, result)
        self.assertEqual(7, result["current_phase"])
        self.assertEqual("historical_read_only", result["schema_compatibility"]["mode"])

    def test_migration_cannot_inherit_old_three_document_authorization(self):
        self.state["schema"] = "requirement-spec/v3"
        self.fixture.approve_planning()
        self.fixture.authorize()
        self.state["schema"] = "requirement-spec/v4"
        self.state["current_phase"] = 5
        code, result = self.check()
        self.assertNotEqual(0, code)
        self.assertIsNone(result["fingerprints"]["recorded_inputs"]["plan_fingerprint_sha256"])
        self.assertTrue(any(e["kind"] == "implementation_authorization" for e in result["errors"]))


if __name__ == "__main__":
    unittest.main()
