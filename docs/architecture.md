# Atelier skeleton

Atelier is a coordination hub for work in separate project repositories.
Instructions define the coordination process; a small Python runtime stores confirmed inputs and exposes Herdr inspection.
The existing local model evaluation package remains available through the harness command.

## Ownership

- AGENTS.md is the agent entry point and points to reference files.
- teammates/ contains reusable functional role definitions.
- projects/ contains project profiles, project owner assignments, and changing review criteria.
- workflows/ contains orchestration procedures.
- harness/atelier.py implements the initial atelier command within the existing installable package.
- tests/test_atelier.py verifies proposal validation, approval, and Herdr boundaries.

The source package stays named harness for now to preserve existing imports and packaging.
Repository and distribution renaming remain open.

## State

Proposals live under XDG_STATE_HOME/atelier/proposals, defaulting to ~/.local/state/atelier/proposals.
Each proposal is a separate JSON file created exclusively and approved by atomic replacement.
An approval record represents confirmation received from Joseph; the command cannot authenticate who confirmed it.
Run the approval command only after receiving confirmation.
This skeleton assumes one chief of staff writes a given proposal.
Private project and teammate memory will live outside Git; its schema and curation lifecycle remain open.

## Current boundary

The CLI validates target directories, workflow references, project reviewer criteria, and model capability ranks supplied in the proposal.
Ranks express a project policy, rather than a measured capability claim made by the runtime.
It rejects review ranks at or below the generation rank.
It records proposals and approvals and can list Herdr agents from a managed pane.
Agent dispatch, retries, execution events, classifier training, plugins, and the dashboard are future slices.
Read workflows/worker-review.md for the initial orchestration procedure.

## Verification

Run python3 -m unittest discover -s tests.
Run python3 -m harness.atelier --help for CLI usage.
Run the writing-for-agents documentation linter on changed agent reference files.
Use GitHub issue #2 for the first workflow and issue #3 for the deferred visualization design.
