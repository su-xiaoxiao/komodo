// Installed into a Komodo Action. Runs one stack lifecycle operation (start/stop/status/logs)
// through the Windows executor so it shares the release lock with release transactions.
const group = __GROUP__;
const defaultOperation = __OPERATION__;
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未执行启停");
}
const operation = String(ARGS.operation ?? defaultOperation).trim().toLowerCase();
if (!["start", "stop", "status", "logs"].includes(operation)) throw new Error("操作只能是 start/stop/status/logs");
const id = crypto.randomUUID().replaceAll("-", "");
const request = { id, project: group, action: "stack", options: { operation, group }, expires_at: Date.now() / 1000 + 60 };
await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
console.log(`${group}：${operation} 已提交，任务 ${id}（与发布事务共用 releases/.release.lock，二者不会重叠）`);
let result, lastStage = "";
const seen = new Set();
for (let attempt = 0; attempt < 360; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 2000));
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (result.kind !== "stack") throw new Error(`任务 ${id} 不是启停结果，请核对入口`);
  if (result.stage && result.stage !== lastStage) { console.log(`阶段：${result.stage}`); lastStage = result.stage; }
  for (const line of result.output ?? []) if (!seen.has(line)) { console.log(line); seen.add(line); }
  if (["succeeded", "failed", "interrupted"].includes(result.status)) break;
}
if (!result || !["succeeded", "failed", "interrupted"].includes(result.status)) {
  throw new Error(`等待超时，结果未知；核对 Windows 任务 ${id}，不要重复提交`);
}
if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
console.log(`${group}：${operation} 完成`);
