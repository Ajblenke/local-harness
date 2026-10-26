import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import atelier


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.criteria = self.root / "criteria.md"
        self.criteria.write_text("Verify acceptance criteria and regression checks.\n")
        self.environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.proposal = {
            "objective": "Implement the scoped change",
            "acceptance_criteria": "Existing checks pass and reviewer accepts the diff",
            "project": str(self.root),
            "review_criteria": str(self.criteria),
            "workflow": "worker-review",
            "worker": "worker",
            "reviewer": "project-reviewer",
            "generator_model": {"id": "generator", "rank": 1},
            "reviewer_model": {"id": "reviewer", "rank": 2},
        }

    def test_confirmation_is_explicit_and_survives_reload(self):
        record = atelier.create(self.proposal)
        self.assertEqual(record["status"], "proposed")
        approved = atelier.approve(record["id"])
        saved = json.loads(atelier.proposal_path(record["id"]).read_text())
        self.assertEqual(saved, approved)
        self.assertEqual(saved["status"], "approved")
        with self.assertRaises(ValueError):
            atelier.approve(record["id"])

    def test_equal_or_weaker_or_same_model_cannot_review(self):
        for model in ({"id": "reviewer", "rank": 1}, {"id": "reviewer", "rank": 0},
                      {"id": "generator", "rank": 2}):
            with self.subTest(model=model):
                proposal = copy.deepcopy(self.proposal)
                proposal["reviewer_model"] = model
                with self.assertRaises(ValueError):
                    atelier.create(proposal)
        self.assertFalse(atelier.state_dir().exists())

    def test_approval_rechecks_project_context_exists(self):
        record = atelier.create(self.proposal)
        self.criteria.unlink()
        with self.assertRaises(ValueError):
            atelier.approve(record["id"])
        saved = json.loads(atelier.proposal_path(record["id"]).read_text())
        self.assertEqual(saved["status"], "proposed")

    def test_proposal_id_cannot_escape_state_directory(self):
        with self.assertRaises(ValueError):
            atelier.proposal_path("../../outside")

    def test_herdr_inspection_requires_managed_pane(self):
        with patch.dict(os.environ, {"HERDR_ENV": "0"}), patch("subprocess.run") as run:
            with self.assertRaises(ValueError):
                atelier.herdr_agents()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
