import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

/**
 * Explicit model switching for local-harness.
 *
 * Nothing in this extension selects a cloud model from task text. The previous
 * heuristic router could expose an existing session transcript to a cloud
 * provider. Until the separate brief/handoff boundary is implemented, this
 * extension only permits switching back to the configured local model.
 */

const LOCAL_PROVIDER = "llama-cpp";
const LOCAL_MODEL = "qwen3.5-4b";

async function switchTo(
  pi: ExtensionAPI,
  ctx: ExtensionContext,
  provider: string,
  modelId: string,
): Promise<void> {
  const model = ctx.modelRegistry.find(provider, modelId);
  if (!model) {
    ctx.ui.notify(`Model not found: ${provider}/${modelId}`, "warning");
    return;
  }
  if (!ctx.modelRegistry.hasConfiguredAuth(model)) {
    ctx.ui.notify(`No authentication configured for ${provider}/${modelId}`, "warning");
    return;
  }
  if (!(await pi.setModel(model))) {
    ctx.ui.notify(`Could not switch to ${provider}/${modelId}`, "error");
    return;
  }
  ctx.ui.notify(`Using ${provider}/${modelId}`, "info");
}

export default function modelSelector(pi: ExtensionAPI): void {
  pi.registerCommand("local-model", {
    description: "Switch this session back to the local Qwen model",
    handler: async (_args, ctx) => {
      await switchTo(pi, ctx, LOCAL_PROVIDER, LOCAL_MODEL);
    },
  });

  pi.on("model_select", async (event, ctx) => {
    const location = event.model.provider === LOCAL_PROVIDER ? "local" : "cloud";
    ctx.ui.setStatus("model-location", `${location}: ${event.model.id}`);
  });
}
