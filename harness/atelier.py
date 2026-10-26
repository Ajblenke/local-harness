"""Atelier proposal ledger and read-only Herdr inspection."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "atelier/proposals"


def validate(proposal: dict) -> None:
    for field in ("objective", "acceptance_criteria", "worker", "reviewer"):
        if not isinstance(proposal.get(field), str) or not proposal[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    for field in ("project", "review_criteria"):
        value = proposal.get(field)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"{field} must be an absolute path")
    if not Path(proposal["project"]).is_dir():
        raise ValueError("project must be an existing directory")
    if not Path(proposal["review_criteria"]).is_file():
        raise ValueError("review_criteria must be an existing file")
    if proposal.get("workflow") != "worker-review":
        raise ValueError("the initial workflow is worker-review")
    for field in ("generator_model", "reviewer_model"):
        model = proposal.get(field)
        if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"].strip():
            raise ValueError(f"{field} requires a model id")
        if type(model.get("rank")) is not int or model["rank"] < 0:
            raise ValueError(f"{field} requires a nonnegative integer capability rank")
    generator, reviewer = proposal["generator_model"], proposal["reviewer_model"]
    if reviewer["rank"] <= generator["rank"] or reviewer["id"] == generator["id"]:
        raise ValueError("review requires a different model with a higher capability rank")


def proposal_path(identifier: str) -> Path:
    if str(uuid.UUID(identifier)) != identifier:
        raise ValueError("invalid proposal id")
    return state_dir() / f"{identifier}.json"


def create(proposal: dict) -> dict:
    validate(proposal)
    record = {"proposal": proposal, "id": str(uuid.uuid4()), "status": "proposed"}
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = proposal_path(record["id"])
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    return record


def approve(identifier: str) -> dict:
    path = proposal_path(identifier)
    record = json.loads(path.read_text())
    validate(record["proposal"])
    if record["status"] != "proposed":
        raise ValueError("only a proposed workflow can be approved")
    record["status"] = "approved"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(record, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return record


def herdr_agents() -> int:
    if os.environ.get("HERDR_ENV") != "1":
        raise ValueError("Herdr inspection requires a Herdr managed pane")
    return subprocess.run(["herdr", "agent", "list"], check=False, timeout=15).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("workflow", help="print the initial orchestration reference")
    propose = commands.add_parser("propose", help="validate and save a proposal JSON file")
    propose.add_argument("file", type=Path)
    approval = commands.add_parser("approve", help="record confirmation already received from Joseph")
    approval.add_argument("id")
    show = commands.add_parser("show", help="read a saved proposal")
    show.add_argument("id")
    commands.add_parser("agents", help="list agents in the current Herdr session")
    args = parser.parse_args(argv)
    try:
        if args.command == "agents":
            return herdr_agents()
        if args.command == "workflow":
            print((ROOT / "workflows/worker-review.md").read_text())
            return 0
        if args.command == "propose":
            proposal = json.loads(args.file.read_text())
            if not isinstance(proposal, dict):
                raise ValueError("proposal must be a JSON object")
            result = create(proposal)
        elif args.command == "approve":
            result = approve(args.id)
        else:
            result = json.loads(proposal_path(args.id).read_text())
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"atelier: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
