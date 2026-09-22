# Gemini escalation plan

## Objective

Use a Gemini free-tier API key for explicitly approved hard tasks without
giving the cloud provider local sessions, telemetry, secrets, the vault, or the
live home directory. Local Qwen remains the default for main sessions and all
automated control-loop work.

## Boundary

Changing the model in an existing pi session is not an escalation boundary: it
would send the existing transcript and tool context to the new provider.
Keyword-based routing has therefore been removed.

A Gemini invocation must be a fresh process receiving only a reviewed
brief and explicitly named files copied into a temporary directory. It must
require all of the following:

- an `.escalate-ok` marker in the source repository;
- a brief the user has reviewed;
- an explicit confirmation naming Gemini and the files leaving the machine;
- `GEMINI_API_KEY` supplied through the process environment or pi's private
  auth store, never a repository file;
- deny rules for the state directory, pi sessions, credentials, dotfiles, and
  the vault;
- no automatic fallback, retry, or model selection when free-tier quota is
  exhausted.

## Intended flow

1. `harness handoff create --file FILE...` validates the opted-in repository and rejects
   secrets, symlinks, directories, and paths outside that repository.
2. It copies only named files into a private handoff directory and generates a
   review brief with the exact model, sizes, and content digests.
3. The user reviews the brief and approves one invocation.
4. `harness handoff run` verifies the confirmation and every copied file, then
   a fresh Pi process runs `google/<approved-model>` in a Bubblewrap filesystem
   with a minimal, shell-free tool set and no access to local harness state.
5. The response, stderr, result metadata, and changed copies stay in the
   private handoff directory for review; they are never applied automatically.

## Free-tier behavior

Rate limits and quota exhaustion are expected outcomes. The harness records
the stopped result and does not retry or fall back to another model.
Model choice should be explicit because Google's free-tier catalogue can
change independently of this repository.

## Current status

The copy/review/confirm/run boundary and deterministic deny-list tests are
implemented. Pi 0.85.1 uses the built-in `google` provider and
`GEMINI_API_KEY`, so no cloud credential belongs in `models.json`. The
Bubblewrap filesystem boundary has been smoke-tested locally without a key.
A live non-sensitive Gemini request and free-tier quota-error check still need
to be run by an operator with a key.
