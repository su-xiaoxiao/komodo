// Installed into a Komodo Action. Runs one stack lifecycle operation (start/stop/status/logs)
// through the Windows executor so it shares the release lock with release transactions.
const group = __GROUP__;
const defaultOperation = __OPERATION__;
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未执行启停");
}
const settled = ["succeeded", "failed", "interrupted", "timed_out"];
let result, lastStage = "", operation;
// 结果未知时用同一个任务号回查：job=<任务号> 只读取结果，不会重新执行启停。
const resumed = String(ARGS.job ?? "").trim();
let id = resumed;
if (resumed) {
  if (!/^[0-9a-f]{32}$/.test(resumed)) throw new Error("job 必须是 32 位十六进制任务号");
  operation = "回查";
  let known = false;
  for (const path of [`${queue}/responses/${id}.json`, `${queue}/requests/${id}.json`, `${queue}/claims/${id}.json`]) {
    try { await Deno.stat(path); known = true; break; }
    catch (error) { if (!(error instanceof Deno.errors.NotFound)) throw error; }
  }
  if (!known) throw new Error(`未找到任务 ${id} 的请求、认领或结果记录，请核对任务号`);
  console.log(`${group}：回查任务 ${id}（只读，不重新执行启停）`);
} else {
  operation = String(ARGS.operation ?? defaultOperation).trim().toLowerCase();
  if (!["start", "stop", "status", "logs"].includes(operation)) throw new Error("操作只能是 start/stop/status/logs");
  id = crypto.randomUUID().replaceAll("-", "");
  const request = { id, project: group, action: "stack", options: { operation, group }, expires_at: Date.now() / 1000 + 60 };
  await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
  await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
  console.log(`${group}：${operation} 已提交，任务 ${id}（与发布事务共用 releases/.release.lock，二者不会重叠）`);
}
const seen = new Set();
// 执行器对单次启停有 900s 预算（LOCAL_STACK_TIMEOUT），并会把超时如实回报为 timed_out；
// 这里多等一会儿，只有在连执行器都没能回报时才提示回查。
const deadline = Date.now() + 960000;
while (Date.now() < deadline) {
  await new Promise(resolve => setTimeout(resolve, 2000));
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (result.kind !== "stack") throw new Error(`任务 ${id} 不是启停结果，请核对入口`);
  if (result.project && result.project !== group) throw new Error(`任务 ${id} 属于 ${result.project}，与本入口 ${group} 不一致`);
  if (result.stage && result.stage !== lastStage) { console.log(`阶段：${result.stage}`); lastStage = result.stage; }
  for (const line of result.output ?? []) if (!seen.has(line)) { console.log(line); seen.add(line); }
  if (settled.includes(result.status)) break;
}
if (!result || !settled.includes(result.status)) {
  throw new Error(`等待超时，结果未知；用同一入口回查 job=${id}（只读结果，不会重复执行启停）`);
}
// 报告的结果与后台是否仍在执行是两件事：超时只说明"没等到结论"，
// 停止 Docker 客户端也不等于服务端已撤销，必须核对容器状态。
const execution = result.execution ?? {};
if (execution.running) {
  console.log("后台执行：仍在执行（结果未知；Docker 服务端操作可能仍在进行，必须核对容器状态）");
} else if (execution.outcome) {
  console.log(`后台执行：已结束，实际结果 ${execution.outcome}${execution.restarted ? "（执行器重启过，结论需核对）" : ""}`);
  if (execution.error) console.log(`后台执行错误：${execution.error}`);
}
if (result.status === "timed_out") {
  const background = execution.running ? "仍在执行" : `已结束（实际结果 ${execution.outcome ?? "未知"}）`;
  throw new Error(`${group}：${operation} 超时，结果未知（${result.error ?? "执行器已超时"}）；后台${background}；`
    + `用 job=${id} 回查并核对容器状态，不要重复提交`);
}
if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
console.log(`${group}：${operation} 完成`);
