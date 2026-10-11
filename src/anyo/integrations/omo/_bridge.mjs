// Native OmO extension: the Anyo wire handshake and final-result tool.
import { readFileSync, writeFileSync, writeSync } from "node:fs";

export default function anyoBridge(pi) {
  const policy = JSON.parse(readFileSync(process.env.ANYO_OMO_POLICY, "utf8"));
  const allowedModels = new Set(policy.models);
  const allowedTools = new Set([...policy.tools, "StructuredOutput"]);
  const emit = (type, data = {}) => writeSync(1, JSON.stringify({ type, ...data }) + "\n");
  const fail = (code) => {
    emit("anyo.error", { code });
    // Native OmO catches ordinary extension exceptions. Exit before a rejected
    // model can send a request, rather than relying on a swallowed exception.
    process.exit(86);
  };
  pi.on("session_start", async (_event, ctx) => {
    try {
      const config = JSON.parse(readFileSync(policy.catalog, "utf8"));
      const providers = new Set(policy.models.map((m) => m.split("/", 1)[0]));
      config.disabledProviders = [...new Set(ctx.modelRegistry.getAll()
        .map((m) => m.provider).filter((p) => !providers.has(p)))];
      writeFileSync(policy.catalog, JSON.stringify(config), { mode: 0o600 });
      await ctx.modelRegistry.refresh();
      const available = ctx.modelRegistry.getAll().map((m) => `${m.provider}/${m.id}`);
      if (available.length !== allowedModels.size || available.some((m) => !allowedModels.has(m))) {
        fail("configuration_failed");
      }
      const model = `${ctx.model?.provider}/${ctx.model?.id}`;
      if (model !== policy.selected) fail("configuration_mismatch");
      const registeredTools = new Set(pi.getAllTools().map((t) => t.name));
      if ([...allowedTools].some((t) => !registeredTools.has(t))) fail("configuration_failed");
      pi.setActiveTools([...allowedTools]);
      emit("anyo.ready", { model, models: available, tools: pi.getActiveTools() });
    } catch {
      fail("configuration_failed");
    }
  });
  pi.on("before_provider_request", (event, ctx) => {
    const model = event.model ?? ctx.model;
    const identifier = `${model?.provider}/${model?.id}`;
    if (!allowedModels.has(identifier)) fail("configuration_mismatch");
    emit("anyo.model", { model: identifier });
  });
  pi.on("tool_call", (event) => {
    if (!allowedTools.has(event.toolName)) {
      return { block: true, reason: "Tool is not enabled by this Anyo harness" };
    }
  });
  pi.registerTool({
    name: "StructuredOutput",
    label: "Anyo result",
    description: "Return the final Anyo success/failure envelope as your last action.",
    parameters: policy.schema,
    async execute(_id, args) {
      return {
        content: [{ type: "text", text: "Anyo result recorded." }],
        details: { anyo_result: args },
        terminate: true,
      };
    },
  });
}
