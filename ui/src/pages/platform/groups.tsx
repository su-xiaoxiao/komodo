/**
 * The unified group + container view: every registered Compose group with its containers and the
 * group lifecycle controls, all in one place.
 *
 * Why it exists: Komodo's native Stack buttons run `docker compose up -d` / `docker compose -p X
 * stop` straight on the daemon and never take releases/.release.lock, so they can cut across a
 * release. This section drives the installed `<group>-stack` Action instead, which takes the same
 * lock as the release transaction; the native Stack resources are removed so no lock-free button
 * is left. The container witness in the release engine stays as the supplementary protection.
 */
import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from "@mantine/core";
import { Types } from "komodo_client";
import { useEffect, useMemo, useRef } from "react";
import { t } from "@/localization/runtime";
import { PlatformGroups, shortSha, stackAction } from "@/lib/platform";
import { usePlatformRun, type Running } from "./run";

export default function GroupsSection({
  installed,
  onRun,
}: {
  installed: Types.ActionListItem[] | undefined;
  onRun: (run: Running) => void;
}) {
  const { start, isPending, update, complete, payload, failure } = usePlatformRun(
    onRun,
    t("Groups"),
  );
  const names = useMemo(() => new Set((installed ?? []).map((a) => a.name)), [installed]);
  const overview = payload as PlatformGroups | undefined;
  const started = useRef(false);

  // Load on mount: the group list is the entry point of the page.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    start("platform-groups", {});
  }, [start]);

  const lock = overview?.lock;
  const groups = overview?.groups ?? [];

  const lifecycle = (group: string, operation: string) =>
    start(stackAction(group), { operation, group });

  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm">
        <Group gap="sm">
          <Title order={4}>{t("Groups and containers")}</Title>
          <Code>platform-groups</Code>
        </Group>
        <Group gap="sm">
          {isPending && <Loader size="xs" />}
          <Button size="xs" variant="default" onClick={() => start("platform-groups", {})}>
            {t("Refresh")}
          </Button>
        </Group>
      </Group>
      <Stack gap="sm">
        <Text size="xs" c="dimmed">
          {t(
            "Start and stop go through the installed <group>-stack entry, which shares releases/.release.lock with releases, so the two can never overlap. These buttons replace Komodo's native Stack buttons.",
          )}
        </Text>
        {lock?.held && (
          <Alert color="yellow" title={t("The release lock is held")}>
            {t("Holder: {0} {1} pid={2}", {
              0: lock.kind ?? "—",
              1: lock.subject ?? "—",
              2: lock.pid ?? "—",
            })}
            {" · "}
            {t("Start and stop are disabled until it is released.")}
          </Alert>
        )}
        {overview?.docker_error && <Alert color="red">{overview.docker_error}</Alert>}
        {failure && <Alert color="red">{failure}</Alert>}
        {!overview && complete && !failure && (
          <Text size="sm" c="dimmed">
            {t("No group overview was returned.")}
          </Text>
        )}

        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t("Group")}</Table.Th>
              <Table.Th>{t("Status")}</Table.Th>
              <Table.Th>{t("Container")}</Table.Th>
              <Table.Th>{t("State")}</Table.Th>
              <Table.Th>{t("Image")}</Table.Th>
              <Table.Th>{t("Lifecycle")}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {groups.map((group) => {
              const hasEntry = names.has(stackAction(group.name));
              const busy = group.total > 0 && group.running === group.total;
              const blocked = Boolean(lock?.held) || !hasEntry;
              const rows = group.containers.length ? group.containers : [null];
              return rows.map((container, index) => (
                <Table.Tr key={group.name + (container ? container.container_name : "-empty")}>
                  {index === 0 && (
                    <>
                      <Table.Td rowSpan={rows.length}>
                        <Group gap={6}>
                          <Text size="sm" fw={500}>
                            {group.name}
                          </Text>
                          {!group.managed && (
                            <Badge size="xs" variant="light" color="gray">
                              {t("Not registered")}
                            </Badge>
                          )}
                        </Group>
                        <Text size="xs" c="dimmed">
                          {t("{0} of {1} running", { 0: group.running, 1: group.total || group.expected })}
                        </Text>
                        {group.missing.length > 0 && (
                          <Text size="xs" c="yellow">
                            {t("Missing {0}", { 0: group.missing.join(", ") })}
                          </Text>
                        )}
                        {group.unexpected.length > 0 && (
                          <Text size="xs" c="yellow">
                            {t("Not in the registry: {0}", { 0: group.unexpected.join(", ") })}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td rowSpan={rows.length}>
                        <Badge variant="light" color={busy ? "teal" : group.total ? "yellow" : "gray"}>
                          {group.total === 0
                            ? t("No containers")
                            : busy
                              ? t("Running")
                              : t("Partially running")}
                        </Badge>
                      </Table.Td>
                    </>
                  )}
                  <Table.Td>
                    {container ? (
                      <Text size="sm">{container.container_name}</Text>
                    ) : (
                      <Text size="sm" c="dimmed">
                        —
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {container ? (
                      <Badge
                        size="sm"
                        variant="light"
                        color={container.state === "running" ? "teal" : "gray"}
                      >
                        {container.state}
                        {container.health ? ` / ${container.health}` : ""}
                      </Badge>
                    ) : (
                      <Text size="sm" c="dimmed">
                        —
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {container ? (
                      <Group gap={4}>
                        <Code>{shortSha(container.image_id, 19)}</Code>
                        {container.drift === true && (
                          <Badge size="xs" color="yellow" variant="light">
                            {t("Differs from the registered image")}
                          </Badge>
                        )}
                      </Group>
                    ) : (
                      <Text size="sm" c="dimmed">
                        —
                      </Text>
                    )}
                  </Table.Td>
                  {index === 0 && (
                    <Table.Td rowSpan={rows.length}>
                      <Group gap={6}>
                        <Tooltip
                          label={
                            !hasEntry
                              ? t("This group has no locked lifecycle entry")
                              : lock?.held
                                ? t("The release lock is held")
                                : t("Start the group through the locked entry")
                          }
                        >
                          <Button
                            size="xs"
                            variant="light"
                            disabled={blocked || busy}
                            onClick={() => lifecycle(group.name, "start")}
                          >
                            {t("Start")}
                          </Button>
                        </Tooltip>
                        <Tooltip
                          label={
                            !hasEntry
                              ? t("This group has no locked lifecycle entry")
                              : lock?.held
                                ? t("The release lock is held")
                                : t("Stop the group through the locked entry")
                          }
                        >
                          <Button
                            size="xs"
                            variant="light"
                            color="orange"
                            disabled={blocked || group.running === 0}
                            onClick={() => lifecycle(group.name, "stop")}
                          >
                            {t("Stop")}
                          </Button>
                        </Tooltip>
                        <Button
                          size="xs"
                          variant="default"
                          disabled={!hasEntry}
                          onClick={() => lifecycle(group.name, "status")}
                        >
                          {t("Status")}
                        </Button>
                        <Button
                          size="xs"
                          variant="default"
                          disabled={!hasEntry}
                          onClick={() => lifecycle(group.name, "logs")}
                        >
                          {t("Logs")}
                        </Button>
                      </Group>
                    </Table.Td>
                  )}
                </Table.Tr>
              ));
            })}
          </Table.Tbody>
        </Table>

        {update && (
          <Text size="xs" c="dimmed">
            {t("Last overview:")} <Code>{update._id?.$oid}</Code>
            {" · "}
            {t("state {0}", { 0: update.status })}
          </Text>
        )}
      </Stack>
    </Card>
  );
}


