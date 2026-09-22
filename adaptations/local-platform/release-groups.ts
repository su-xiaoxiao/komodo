// Installed into a Komodo Action. Read-only: lists the registered Compose groups with their
// containers and the shared release lock, so the platform page can show the group lifecycle
// controls next to the containers. This Action never starts, stops, builds or deploys anything.
const queue = "/local-queue";
const heartbeat = JSON.parse(await Deno.readTextFile(`${queue}/heartbeat.json`));
if (heartbeat.protocol !== 1 || Date.now() / 1000 - heartbeat.updated_at > 15) {
  throw new Error("Windows 执行器离线，未查询分组");
}
const id = crypto.randomUUID().replaceAll("-", "");
const request = { id, project: "platform", action: "groups", options: {}, expires_at: Date.now() / 1000 + 60 };
await Deno.writeTextFile(`${queue}/requests/${id}.tmp`, JSON.stringify(request));
await Deno.rename(`${queue}/requests/${id}.tmp`, `${queue}/requests/${id}.json`);
let result;
for (let attempt = 0; attempt < 60; attempt++) {
  await new Promise(resolve => setTimeout(resolve, 1000));
  try { result = JSON.parse(await Deno.readTextFile(`${queue}/responses/${id}.json`)); }
  catch (error) { if (error instanceof Deno.errors.NotFound) continue; throw error; }
  if (["succeeded", "failed", "interrupted"].includes(result.status)) break;
}
if (!result || result.kind !== "groups") throw new Error(`未取到分组信息，核对 Windows 任务 ${id}`);
// 机器可读行：页面用它渲染"组 + 容器 + 锁"的统一视图；失败时也要给出结构化原因。
console.log("__PLATFORM_JSON__ " + JSON.stringify({ schema: 1, job: id, ...result }));
if (result.status !== "succeeded") throw new Error(result.error ?? result.status);
const lock = result.lock ?? {};
console.log(`发布锁：${lock.held ? `被持有（${lock.kind ?? "未标注"} ${lock.subject ?? ""} pid=${lock.pid ?? "?"}）` : "空闲"}`);
for (const group of result.groups ?? []) {
  const state = group.running === group.total && group.total > 0 ? "运行中" : group.total === 0 ? "无容器" : "部分运行";
  console.log(`组 ${group.name}：${state}（${group.running}/${group.total}）｜服务 ${(group.services ?? []).join(", ") || "（无登记）"}`
    + `${(group.missing ?? []).length ? `｜缺少 ${group.missing.join(", ")}` : ""}`
    + `${(group.unexpected ?? []).length ? `｜多出 ${group.unexpected.join(", ")}` : ""}`);
  for (const service of group.containers ?? []) {
    console.log(`  ${service.name}：${service.state}${service.health ? "/" + service.health : ""}｜镜像 ${String(service.image_id).slice(0, 19)}`
      + `${service.drift === true ? "｜与登记镜像不一致" : ""}`);
  }
}
if (result.docker_error) console.log(`注意：${result.docker_error}`);
