# Agent sandbox

This profile standardizes the filesystem, pi version, Python environment, user, CPU/memory ceilings,
and network boundary. It mounts only an isolated Git worktree, one explicitly selected GGUF, and an
artifact directory; it never mounts the host home, credentials, or Docker socket. The agent and model
router share an `internal: true` network with no internet route.

The image build copies only the locked Python project and runtime package, not the whole repository.
Every run records a digest over those build inputs plus the Pi config, telemetry extension, Dockerfile,
and both Compose definitions; the observer rejects a changed source bundle.

Set the exact local model and build with:

```sh
MODEL_FILE=/absolute/qwen.gguf WORKTREE=/absolute/worktree \
ARTIFACTS=/absolute/artifacts LOCAL_HARNESS_RUN_ID=manual-build \
  docker compose -f sandbox/compose.yaml build
```

Run `harness sandbox run WORK_ORDER PROMPT --worktree PATH --artifacts PATH`. The prompt is streamed on
stdin so it does not appear in the process list. The worktree should be a disposable Git worktree, not
the primary checkout. The primary image pins Node 24.21.0, Python 3.13.15, uv 0.12.7, and pi 0.85.1; the
router pins llama.cpp build b10985. Content digests remain a bootstrap limitation: every run manifest
records both Compose definitions, and production adoption should pin the resolved multi-architecture
digests after the first successful pull on the target GPU host.

Required checks run afterward with no network, a read-only candidate worktree, and no evidence-directory
mount. Test code can use `/tmp`, but it cannot rewrite the diff or the runner-owned contract artifacts.
