// Installed into a Komodo Action. Read-only: lists the project's Git refs and, when a
// ref is given, shows the resolved commit before the operator submits a release.
// This Action never builds and never deploys.
const project = __PROJECT__;
const title = __TITLE__;
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未查询分支");
}
const ref = String(ARGS.ref ?? "").trim();
if (ref && !/^[A-Za-z0-9_][A-Za-z0-9_./-]{0,255}$/.test(ref)) throw new Error("分支/提交名称不合法");
const id = crypto.randomUUID().replaceAll("-", "");
const request = { id, project, action: "list-refs", options: { ref }, expires_at: Date.now() / 1000 + 60 };
await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
console.log(`已请求 ${title} 的来源分支列表，任务 ${id}`);
let result;
for (let attempt = 0; attempt < 150; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 1000));
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (["succeeded", "failed", "interrupted"].includes(result.status)) break;
}
if (!result || !["succeeded", "failed", "interrupted"].includes(result.status)) {
  throw new Error(`等待超时，未取到分支列表；核对 Windows 任务 ${id}，不要重复提交`);
}
if (result.kind !== "refs") throw new Error(`任务 ${id} 不是分支查询结果，请改用发布入口`);
const source = result.mode === "remote" ? `远程 ${result.remote}（在 ${result.project} 的 Git 仓库上执行 ls-remote）` : "本机仓库";
console.log(`来源：${source}`);
console.log(`已登记分支：${(result.approved ?? []).join(", ") || "（无）"}｜默认：${result.default_ref ?? "（无）"}`);
for (const [name, sha] of Object.entries(result.approved_ref_tips ?? {})) {
  const same = result.released_commit ? (sha === result.released_commit ? "就是已发布提交" : "与已发布提交不同（远端有新内容）") : "尚无发布记录";
  console.log(`分支 ${name} 当前指向 ${sha}（${same}）`);
}
console.log(`远程分支 ${result.branch_count ?? 0} 个：${(result.branches ?? []).join(", ") || "（无）"}`);
console.log(`标签 ${result.tag_count ?? 0} 个：${(result.tags ?? []).join(", ") || "（无）"}`);
if (result.resolution_error) console.log(`解析失败：${result.resolution_error}`);
if (result.requested_ref) {
  console.log(`待发布分支：${result.requested_ref}｜已登记：${result.requested_ref_approved ? "是" : "否"}`);
  console.log(`将构建提交：${result.commit ?? "未解析到"}`);
}
if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
if (result.requested_ref && !result.requested_ref_approved) {
  throw new Error(`分支 ${result.requested_ref} 未在平台权威配方登记，发布入口会拒绝；如需发布请先登记该分支`);
}
if (result.requested_ref && !result.commit) {
  throw new Error(`分支 ${result.requested_ref} 无法解析为完整提交：${result.resolution_error ?? "未知原因"}`);
}
