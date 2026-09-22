# Model selection and cloud boundary

The interactive pi session defaults to `llama-cpp/qwen3.5-4b`. The
`model-selector.ts` extension does not inspect task text or automatically choose
a cloud model. Its `/local-model` command is a recovery path back to Qwen, and
its status indicator makes a non-local selection visible.

Automatic routing was removed because changing providers inside an existing
session would send that session's transcript and tool context to the new
provider. Keywords such as `implement`, `debug`, or `security` are not consent.

## Gemini

Pi 0.85.1 has a built-in `google` provider that reads `GEMINI_API_KEY`. Keep the
key in the environment or pi's private auth store; never put it in
`models.json`, this repository, telemetry, or a handoff artifact.

The free tier is available only through the deliberately bounded
`harness handoff` path. It does not change the privacy boundary. The command
copies named files into private state, then Bubblewrap starts a fresh process
that cannot see local sessions, telemetry, the source repository, Git
metadata, the state directory, the vault, or the live home directory.

The flow is:

1. Create an `.escalate-ok` marker in an opted-in repository.
2. Generate and review a minimal handoff brief.
3. Run `harness handoff create --objective ... --file ... --model gemini-...`.
4. Read the generated `brief.md`, including the exact model, files, sizes, and
   digests.
5. Run the printed command with its confirmation digest and `GEMINI_API_KEY`
   set in the environment.
6. Review the response and changed copies in the handoff directory. Nothing is
   applied back to the source repository automatically.

The runner enables only Pi's read, grep, find, list, edit, and write tools; it
does not enable a shell. It invokes the reviewed model once and does not retry
or fall back when quota is exhausted. Use `/local-model` if an interactive
session is ever pointed at anything other than Qwen.
