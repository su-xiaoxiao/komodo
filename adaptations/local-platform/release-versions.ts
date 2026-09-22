// Installed into a Komodo Action. Read-only: shows which source revision is configured,
// which candidate was built, what the last successful release is and what is running.
const project = __PROJECT__;
const title = __TITLE__;
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未查询版本");
}
const id = crypto.randomUUID().replaceAll("-", "");
const request = { id, project, action: "versions", options: {}, expires_at: Date.now() / 1000 + 60 };
await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
let result;
for (let attempt = 0; attempt < 60; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 1000));
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (["succeeded", "failed", "interrupted"].includes(result.status)) break;
}
if (!result || result.kind !== "versions") throw new Error(`未取到版本信息，核对 Windows 任务 ${id}`);
// 机器可读行：页面用它渲染版本卡；失败时也要给出结构化原因，因此在状态判断之前输出。
console.log("__PLATFORM_JSON__ " + JSON.stringify({ schema: 1, job: id, ...result }));
if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
const repository = result.repository ?? {};
const recipe = result.recipe ?? {};
console.log(`${title} 版本信息`);
console.log(`仓库 HEAD：${repository.head ? repository.head.slice(0, 12) : "（未知/非 Git）"}｜分支：${repository.branch ?? "（游离）"}｜已跟踪改动：${repository.dirty ? "有" : "无"}｜未跟踪：${repository.untracked ?? 0}`);
console.log(`配方：来源模式 ${recipe.source_mode ?? "（无）"}｜已登记分支 ${(recipe.approved_refs ?? []).join(", ") || "（无）"}｜默认 ${recipe.default_ref ?? "（无）"}｜digest ${(recipe.digest ?? "").slice(0, 12)}`);
if (result.candidate) {
  console.log(`候选构建：任务 ${result.candidate.job}｜${result.candidate.action ?? ""}｜${result.candidate.status}｜版本 ${result.candidate.version ?? "（无）"}｜提交 ${(result.candidate.commit ?? "（无）").slice(0, 12)}｜已部署：${result.candidate.deployed ? "是" : "否"}`);
} else {
  console.log("候选构建：（无记录）");
}
if (result.release) {
  console.log(`最近成功发布：${result.release.version}｜提交 ${(result.release.commit ?? result.release.snapshot ?? "（快照）").slice(0, 12)}｜上一版本 ${result.release.previous_version ?? "（无）"}`);
} else {
  console.log("最近成功发布：（无记录）");
}
for (const service of result.running ?? []) {
  console.log(`运行 ${service.service}：${service.state}${service.health ? "/" + service.health : ""}｜镜像 ${String(service.image_id).slice(0, 19)}｜与发布一致 ${service.matches_release ? "是" : "否"}｜与候选一致 ${service.matches_candidate ? "是" : "否"}${(service.repo_digests ?? []).length ? "｜" + service.repo_digests[0] : ""}`);
}
console.log(`结论：${result.verdict}`);
