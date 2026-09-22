// Installed into a Komodo Action. ARGS is supplied by Komodo.
// Cancelling this Action stops observation; the Windows release transaction continues.
const project = __PROJECT__;
const title = __TITLE__;
const defaultRef = __DEFAULT_REF__;
const metadataTarget = __METADATA_TARGET__;
const note = __NOTE__;
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未提交任务");
}
const operation = ARGS.operation ?? "build-deploy";
if (!["build-deploy", "deploy", "rollback", "reconcile"].includes(operation)) throw new Error("不支持的操作");
const options = operation === "build-deploy" ? { ref: ARGS.ref ?? defaultRef }
  : operation === "deploy" ? { version: ARGS.version } : {};
if (operation === "deploy" && typeof ARGS.version !== "string") throw new Error("请填写 version");
const id = operation === "reconcile" ? ARGS.job : crypto.randomUUID().replaceAll("-", "");
if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) throw new Error("无效任务 ID");
if (operation === "reconcile") {
  const original = JSON.parse(await Deno.readTextFile(`${queue}/requests/${id}.json`));
  if (original.project !== project) throw new Error("任务不属于此项目");
} else {
  const request = { id, project, action: operation, options, expires_at: Date.now() / 1000 + 60 };
  await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
  await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
}
console.log(`Windows 任务 ${id}，操作 ${operation}`);
console.log("取消本页面任务不会中断正在执行的发布；请按任务 ID 核对结果，勿重复提交。");
let lastStage = "";
const seen = new Set<string>();
for (let attempt = 0; attempt < 3600; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 2000));
  let result;
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (result.stage && result.stage !== lastStage) {
    console.log(`阶段：${result.stage}`); lastStage = result.stage;
  }
  for (const line of result.logs ?? []) if (!seen.has(line)) { console.log(line); seen.add(line); }
  if (["succeeded", "failed", "interrupted"].includes(result.status)) {
    // 机器可读行（单行）：页面用它渲染发布结果/版本/镜像；人类可读摘要照旧保留。
    console.log("__PLATFORM_JSON__ " + JSON.stringify({ schema: 1, kind: "release", job: id, project, title,
      operation, status: result.status, error: result.error ?? null, version: result.version ?? null,
      source_commit: result.source_commit ?? null, images: result.images ?? null,
      current_release: result.current_release ?? null, observed_at: result.observed_at ?? null }));
    console.log(JSON.stringify({ id, status: result.status, version: result.version,
      source_commit: result.source_commit, images: result.images, current_release: result.current_release }, null, 2));
    if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
    const release = result.current_release;
    if (release && operation !== "reconcile") {
      const origin = release.source?.commit ? `Git ${release.source.commit.slice(0, 12)}` : release.source?.snapshot ? `快照 ${release.source.snapshot.slice(0, 12)}` : "镜像来源待核实";
      const description = [`${title} · ${origin} · ${release.version}`,
        "Windows 发布执行器 / Docker 运行",
        `最近发布结果核对：${new Date(result.observed_at * 1000).toISOString()}`,
        `发布版本：${release.version}`, `Git 提交：${release.source?.commit ?? "未知"}`,
        ...Object.entries(release.images ?? {}).map(([service, image]) => `${service}: ${image}`),
        "以上为最近成功发布记录，实时容器状态请查看 Local / 容器。",
        note].join("\n");
      try {
        await komodo.write("UpdateResourceMeta", { target: metadataTarget, description });
      } catch { console.warn("发布已成功，但流水线说明更新失败；以任务结果中的版本/镜像为准。"); }
    }
    return;
  }
}
throw new Error(`等待超时，执行结果未知；核对 Windows 任务 ${id}，不要重新提交`);
