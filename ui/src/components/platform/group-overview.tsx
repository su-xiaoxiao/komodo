/**
 * The unified group overview: the single place to see and manage Compose groups.
 *
 * Design notes that matter:
 * - The overview data and the operation results are stored separately. Running status/logs/start/stop
 *   never clears the table (the previous version lost the list whenever an operation started, which
 *   made "no groups" appear out of nowhere).
 * - Start/stop go through the installed `<group>-stack` entry, which takes the same
 *   releases/.release.lock as a release transaction, so the two can never overlap. While that lock is
 *   held they are refused; status and logs stay available.
 * - Unmanaged Compose projects and standalone containers are shown too, clearly marked as having no
 *   controlled lifecycle entry.
 * - Groups are classified on the platform side (all running / partial / stopped / abnormal / not
 *   created) and `running` is never treated as `healthy`.
 */
import {
  Accordion,
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Collapse,
  Group,
  Loader,
  Progress,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { t } from "@/localization/runtime";
import { komodo_client } from "@/lib/hooks";
import { Types } from "komodo_client";
import {
  PLATFORM_MARKER,
  PlatformGroups,
  Group as ComposeGroup,
  GroupStatus,
  parsePlatformPayload,
  stackAction,
  updateText,
} from "@/lib/platform";

// Labels are English source strings so the dictionary can serve both languages.
const STATUS_META: Record<GroupStatus, { label: string; color: string }> = {
  running: { label: "All running", color: "teal" },
  partial: { label: "Partially running", color: "yellow" },
  stopped: { label: "All stopped", color: "gray" },
  abnormal: { label: "Abnormal", color: "red" },
  absent: { label: "Not created", color: "blue" },
};

const statusMeta = (status: GroupStatus | undefined) =>
  STATUS_META[status ?? "absent"] ?? STATUS_META.absent;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Run one Action to completion and return its logs. Independent of the overview state. */
async function runAction(action: string, args: Record<string, string>): Promise<Types.Log[]> {
  const client = komodo_client();
  const submitted = (await client.execute("RunAction", { action, args })) as Types.Update;
  const id = submitted?._id?.$oid;
  if (!id) throw new Error(t("The task was not accepted (no task id returned)"));
  for (let attempt = 0; attempt < 180; attempt++) {
    await sleep(1000);
    const update = (await client.read("GetUpdate", { id })) as Types.Update;
    if (update?.status === "Complete") return update.logs ?? [];
  }
  throw new Error(t("Timed out waiting for the task; check the task log"));
}

type Operation = {
  title: string;
  group?: string;
  status: "running" | "succeeded" | "failed";
  text?: string;
  error?: string;
};

export default function GroupOverview({ compact }: { compact?: boolean }) {
  const [overview, setOverview] = useState<PlatformGroups | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loadedAt, setLoadedAt] = useState<string | undefined>(undefined);
  const [operation, setOperation] = useState<Operation | undefined>(undefined);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [diagnostics, setDiagnostics] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const logs = await runAction("platform-groups", {});
      const payload = parsePlatformPayload<PlatformGroups>(logs);
      if (!payload) {
        const failure = updateText(logs).split("\n").filter((line) => line.startsWith("Error:")).pop();
        throw new Error(failure?.replace(/^Error:\s*/, "") ?? t("No group data was returned"));
      }
      setOverview(payload);                     // only a successful load replaces the data
      setLoadedAt(payload.generated_at);
    } catch (caught) {
      // Keep whatever was loaded before: a refresh failure must not look like "no groups".
      setError(String((caught as Error)?.message ?? caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const lifecycle = async (group: ComposeGroup, operation_name: string) => {
    const labels: Record<string, string> = {
      start: t("Start the group"),
      stop: t("Stop the group"),
      status: t("Group status"),
      logs: t("Group logs"),
    };
    setOperation({ title: labels[operation_name] ?? operation_name, group: group.name, status: "running" });
    try {
      const logs = await runAction(stackAction(group.name), { operation: operation_name, group: group.name });
      setOperation({
        title: labels[operation_name] ?? operation_name,
        group: group.name,
        status: "succeeded",
        text: updateText(logs),
      });
      // Refresh only after the operation finished; the table stayed visible the whole time.
      await load();
    } catch (caught) {
      setOperation({
        title: labels[operation_name] ?? operation_name,
        group: group.name,
        status: "failed",
        error: String((caught as Error)?.message ?? caught),
      });
    }
  };

  const lock = overview?.lock;
  const locked = Boolean(lock?.held);
  const summary = overview?.summary;
  const groups = overview?.groups ?? [];

  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm" align="flex-start">
        <Stack gap={2}>
          <Title order={compact ? 5 : 4}>{t("Compose groups")}</Title>
          <Text size="xs" c="dimmed">
            {t("Containers grouped by Compose project. Group start/stop take the same lock as a release; single-container operations do not.")}
          </Text>
        </Stack>
        <Group gap="sm">
          {loading && <Loader size="xs" />}
          <Button size="xs" variant="default" onClick={load} disabled={loading}>
            {t("Refresh")}
          </Button>
        </Group>
      </Group>

      {summary && (
        <Group gap="xs" mb="sm">
          <Badge variant="light">{t("Groups: {0}", { 0: summary.total })}</Badge>
          <Badge variant="light" color="teal">
            {t("All running: {0}", { 0: summary.running })}
          </Badge>
          <Badge variant="light" color="yellow">
            {t("Partially running: {0}", { 0: summary.partial })}
          </Badge>
          <Badge variant="light" color="gray">
            {t("All stopped: {0}", { 0: summary.stopped })}
          </Badge>
          <Badge variant="light" color="red">
            {t("Abnormal: {0}", { 0: summary.abnormal })}
          </Badge>
          <Badge variant="light" color="blue">
            {t("Not created: {0}", { 0: summary.absent })}
          </Badge>
          {summary.unmanaged > 0 && (
            <Badge variant="light" color="grape">
              {t("Unregistered projects: {0}", { 0: summary.unmanaged })}
            </Badge>
          )}
        </Group>
      )}

      {error && (
        <Alert color="red" mb="sm" title={t("Could not load the group data")}>
          <Stack gap={6}>
            <Text size="sm">{error}</Text>
            <Text size="xs" c="dimmed">
              {overview
                ? t("Showing the last successfully loaded data ({0}).", { 0: loadedAt ?? "—" })
                : t("No group data has loaded yet; please retry.")}
            </Text>
            <Group>
              <Button size="xs" onClick={load} loading={loading}>
                {t("Retry")}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {locked && (
        <Alert color="yellow" mb="sm" title={t("Group lifecycle is locked while a release is running")}>
          <Text size="sm">
            {t("A release or lifecycle operation is in progress ({0}); start and stop are disabled, status and logs remain available.", {
              0: lock?.subject ?? lock?.kind ?? "—",
            })}
          </Text>
        </Alert>
      )}

      {operation && (
        <Alert
          mb="sm"
          color={operation.status === "failed" ? "red" : operation.status === "succeeded" ? "teal" : "blue"}
          title={`${operation.title}${operation.group ? ` · ${operation.group}` : ""}`}
        >
          <Stack gap={4}>
            {operation.status === "running" && <Loader size="xs" />}
            {operation.error && <Text size="sm">{operation.error}</Text>}
            {operation.text && (
              <Collapse expanded={Boolean(operation.text)}>
                <Code block style={{ maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap" }}>
                  {operation.text}
                </Code>
              </Collapse>
            )}
            <Group>
              <Button size="xs" variant="subtle" onClick={() => setOperation(undefined)}>
                {t("Collapse")}
              </Button>
            </Group>
          </Stack>
        </Alert>
      )}

      {!overview && loading && <Text size="sm">{t("Loading groups…")}</Text>}

      {overview && groups.length === 0 && (
        <Alert color="yellow">{t("No Compose group is registered on the platform yet.")}</Alert>
      )}

      {groups.length > 0 && (
        <Accordion
          multiple
          variant="separated"
          value={expanded}
          onChange={(value) => setExpanded(value as string[])}
        >
          {groups.map((group) => {
            const meta = statusMeta(group.status);
            const hasEntry = group.managed;
            return (
              <Accordion.Item key={group.name} value={group.name}>
                <Accordion.Control>
                  <Group justify="space-between" wrap="nowrap" pr="sm">
                    <Group gap="sm" wrap="nowrap">
                      <Text fw={500}>{group.name}</Text>
                      <Badge variant="light" color={meta.color}>
                        {t(meta.label)}
                      </Badge>
                      {!hasEntry && (
                        <Badge variant="light" color="grape">
                          {t("Not registered")}
                        </Badge>
                      )}
                    </Group>
                    <Group gap="md" wrap="nowrap" visibleFrom="sm">
                      <Text size="sm">
                        {t("Running {0}/{1}", { 0: group.running, 1: group.total })}
                      </Text>
                      {group.managed && (
                        <Text size="sm" c={group.unhealthy ? "red" : "dimmed"}>
                          {t("Healthy {0}", { 0: group.healthy })}
                          {group.unhealthy ? ` · ${t("Unhealthy {0}", { 0: group.unhealthy })}` : ""}
                          {group.starting ? ` · ${t("Starting {0}", { 0: group.starting })}` : ""}
                        </Text>
                      )}
                    </Group>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="sm">
                    <Group gap="sm">
                      {hasEntry ? (
                        <>
                          <Tooltip label={locked ? t("Locked while a release is running") : t("Start the whole group through the locked entry")}>
                            <span>
                              <Button
                                size="xs"
                                variant="light"
                                disabled={locked || group.running === group.total}
                                onClick={() => lifecycle(group, "start")}
                              >
                                {t("Start")}
                              </Button>
                            </span>
                          </Tooltip>
                          <Tooltip label={locked ? t("Locked while a release is running") : t("Stop the whole group through the locked entry")}>
                            <span>
                              <Button
                                size="xs"
                                variant="light"
                                color="orange"
                                disabled={locked || group.running === 0}
                                onClick={() => lifecycle(group, "stop")}
                              >
                                {t("Stop")}
                              </Button>
                            </span>
                          </Tooltip>
                          <Button size="xs" variant="default" onClick={() => lifecycle(group, "status")}>
                            {t("Status")}
                          </Button>
                          <Button size="xs" variant="default" onClick={() => lifecycle(group, "logs")}>
                            {t("Logs")}
                          </Button>
                        </>
                      ) : (
                        <Text size="sm" c="dimmed">
                          {t("This project is not registered on the platform, so there is no controlled group lifecycle entry.")}
                        </Text>
                      )}
                    </Group>

                    {group.missing.length > 0 && (
                      <Text size="xs" c="yellow">
                        {t("Registered services not created yet: {0}", { 0: group.missing.join("、") })}
                      </Text>
                    )}
                    {group.unexpected.length > 0 && (
                      <Text size="xs" c="yellow">
                        {t("Services not in the registry: {0}", { 0: group.unexpected.join("、") })}
                      </Text>
                    )}

                    <Table striped withTableBorder>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>{t("Container")}</Table.Th>
                          <Table.Th>{t("Service")}</Table.Th>
                          <Table.Th>{t("Status")}</Table.Th>
                          <Table.Th>{t("Health")}</Table.Th>
                          <Table.Th>{t("Image")}</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {group.containers.length === 0 && (
                          <Table.Tr>
                            <Table.Td colSpan={5}>
                              <Text size="sm" c="dimmed">
                                {t("This group has no container yet.")}
                              </Text>
                            </Table.Td>
                          </Table.Tr>
                        )}
                        {group.containers.map((member) => (
                          <Table.Tr key={member.container_name}>
                            <Table.Td>{member.container_name}</Table.Td>
                            <Table.Td>{member.name}</Table.Td>
                            <Table.Td>
                              <Badge
                                variant="light"
                                color={member.state === "running" ? "teal" : member.state === "exited" ? "gray" : "red"}
                              >
                                {member.state}
                              </Badge>
                            </Table.Td>
                            <Table.Td>
                              {member.health ? (
                                <Badge
                                  variant="light"
                                  color={member.health === "healthy" ? "teal" : member.health === "starting" ? "yellow" : "red"}
                                >
                                  {member.health}
                                </Badge>
                              ) : (
                                <Text size="sm" c="dimmed">
                                  {t("No healthcheck")}
                                </Text>
                              )}
                            </Table.Td>
                            <Table.Td>
                              <Code>{String(member.image_id).slice(0, 19)}</Code>
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>

                    {group.projects.length > 0 && (
                      <Text size="xs" c="dimmed">
                        {t("Projects: {0}", { 0: group.projects.map((item) => item.title ?? item.id).join("、") })}
                      </Text>
                    )}
                  </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            );
          })}
        </Accordion>
      )}

      <Group justify="space-between" mt="sm">
        <Button size="xs" variant="subtle" onClick={() => setDiagnostics((value) => !value)}>
          {diagnostics ? t("Hide diagnostics") : t("Show diagnostics")}
        </Button>
        {loadedAt && (
          <Text size="xs" c="dimmed">
            {t("Data from {0}", { 0: loadedAt })}
          </Text>
        )}
      </Group>
      <Collapse expanded={diagnostics}>
        <Card withBorder mt="xs" p="sm">
          <Stack gap={4}>
            <Text size="xs" c="dimmed">
              {t("Diagnostics only: internal identifiers, not needed for daily work.")}
            </Text>
            <Text size="xs">
              {t("Group data entry")}：<Code>platform-groups</Code>
            </Text>
            <Text size="xs">
              {t("Group lifecycle entry")}：<Code>{"<组名>-stack"}</Code>
            </Text>
            <Text size="xs">
              {t("Lock")}：<Code>releases/.release.lock</Code>
              {lock?.held ? ` · ${t("holder")} ${lock.kind ?? "—"} ${lock.subject ?? "—"}` : ` · ${t("free")}`}
            </Text>
            <Text size="xs">
              {t("Marker")}：<Code>{PLATFORM_MARKER}</Code>
            </Text>
            {overview?.docker_error && <Text size="xs" c="red">{overview.docker_error}</Text>}
          </Stack>
        </Card>
      </Collapse>
    </Card>
  );
}

/** Dashboard counters: how many groups are in each state, with a way into the容器 page. */
export function GroupSummary() {
  const [overview, setOverview] = useState<PlatformGroups | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const logs = await runAction("platform-groups", {});
      const payload = parsePlatformPayload<PlatformGroups>(logs);
      if (!payload) throw new Error(t("No group data was returned"));
      setOverview(payload);
    } catch (caught) {
      setError(String((caught as Error)?.message ?? caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const summary = overview?.summary;
  const groups = overview?.groups ?? [];
  const attention = groups.filter((group) => group.status === "abnormal" || group.status === "partial");

  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>{t("Container groups")}</Title>
        <Group gap="sm">
          {loading && <Loader size="xs" />}
          <Button size="xs" variant="default" component={Link} to="/containers">
            {t("Manage on the containers page")}
          </Button>
        </Group>
      </Group>
      {error && (
        <Alert color="red" mb="xs">
          <Group justify="space-between">
            <Text size="sm">{error}</Text>
            <Button size="xs" onClick={load}>
              {t("Retry")}
            </Button>
          </Group>
        </Alert>
      )}
      {summary ? (
        <Stack gap="xs">
          <Group gap="lg">
            <Stat label={t("Groups")} value={summary.total} />
            <Stat label={t("All running")} value={summary.running} color="teal" />
            <Stat label={t("Partially running")} value={summary.partial} color="yellow" />
            <Stat label={t("All stopped")} value={summary.stopped} color="gray" />
            <Stat label={t("Abnormal")} value={summary.abnormal} color="red" />
            <Stat label={t("Not created")} value={summary.absent} color="blue" />
          </Group>
          {summary.total > 0 && (
            <Progress.Root size="lg">
              {(["running", "partial", "stopped", "abnormal", "absent"] as GroupStatus[]).map((status) => (
                <Progress.Section
                  key={status}
                  value={(summary[status] / summary.total) * 100}
                  color={statusMeta(status).color}
                />
              ))}
            </Progress.Root>
          )}
          {attention.length > 0 && (
            <Alert color="yellow" p="xs">
              <Text size="sm">
                {t("Needs attention: {0}", {
                  0: attention.map((group) => `${group.name}（${t(statusMeta(group.status).label)}）`).join("、"),
                })}
              </Text>
            </Alert>
          )}
          <Text size="xs" c="dimmed">
            {t("Counts come from the platform registry and the actual container state.")}
          </Text>
        </Stack>
      ) : (
        !error && <Text size="sm">{t("Loading groups…")}</Text>
      )}
    </Card>
  );
}

const Stat = ({ label, value, color }: { label: string; value: number; color?: string }) => (
  <Stack gap={0} align="center">
    <Text fw={700} size="xl" c={color}>
      {value}
    </Text>
    <Text size="xs" c="dimmed">
      {label}
    </Text>
  </Stack>
);

