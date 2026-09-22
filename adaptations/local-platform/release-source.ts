// Installed into a Komodo Action. Reviewed edit of one project's *source* block in the
// authoritative recipe (config/project-console.json). The platform validates the block,
// keeps a timestamped backup and writes atomically; build commands, verifier and release
// metadata are not editable here on purpose.
const project = __PROJECT__;
const title = __TITLE__;
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未保存来源配置");
}
const mode = String(ARGS.mode ?? "local").trim();
if (!["local", "remote"].includes(mode)) throw new Error("来源模式只能是 local 或 remote");
const refs = String(ARGS.refs ?? "").split(",").map(item => item.trim()).filter(Boolean);
if (!refs.length) throw new Error("请至少填写一个已批准分支（逗号分隔）");
const defaultRef = String(ARGS.default_ref ?? refs[0]).trim();
const remote = String(ARGS.remote ?? "").trim();
const id = crypto.randomUUID().replaceAll("-", "");
const request = { id, project, action: "set-source",
                  options: { mode, remote, refs, default_ref: defaultRef }, expires_at: Date.now() / 1000 + 60 };
await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
console.log(`已提交 ${title} 来源配置修改：mode=${mode} refs=[${refs.join(", ")}] default=${defaultRef}${remote ? " remote=" + remote : ""}`);
let result;
for (let attempt = 0; attempt < 60; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 1000));
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (["succeeded", "failed", "interrupted"].includes(result.status)) break;
}
if (!result || result.kind !== "source-edit") throw new Error(`未取到来源修改结果，核对 Windows 任务 ${id}`);
// 机器可读行：页面用它回显"改了什么/配方 digest/备份"；失败时也先输出结构化原因。
console.log("__PLATFORM_JSON__ " + JSON.stringify({ schema: 1, job: id, ...result }));
if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
console.log(`已应用：模式 ${result.source.mode}｜已登记分支 ${(result.source.refs ?? []).join(", ")}｜默认 ${result.source.default_ref}`);
console.log(`配方 digest：${String(result.recipe_digest).slice(0, 12)}｜备份：${result.backup}｜未改动的段落：${(result.unchanged_sections ?? []).join(", ")}`);
if (result.lock_holder) console.log(`注意：修改期间有操作持有发布锁（${result.lock_holder}）；在途任务仍使用其入队时的配方版本。`);
