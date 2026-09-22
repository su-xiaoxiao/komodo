import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Divider,
  Group,
  Loader,
  Modal,
  ScrollArea,

  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { Types } from "komodo_client";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { t } from "@/localization/runtime";
import { useExecute, useRead, useSetTitle } from "@/lib/hooks";
import {
  PlatformProject,
  PlatformRefs,

  PlatformSourceEdit,
  PlatformVersions,
  actionFailureText,
  parsePlatformPayload,
  platformProjects,
  sourceDefaults,
  refArgs,
  releaseArgs,
  shortSha,
  sourceArgs,
  updateText,
} from "@/lib/platform";

type Running = {
  title: string;
  id: string;
} | null;

export default function Platform() {
  useSetTitle("Platform");
  const { data: actions } = useRead("ListActions", {});
  const projects = useMemo(() => platformProjects(actions), [actions]);
  const [selected, setSelected] = useState<string | null>(null);
  const prefix = selected ?? projects[0]?.prefix ?? null;
  const project = projects.find((item) => item.prefix === prefix);
  const [run, setRun] = useState<Running>(null);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <Stack gap={2}>
          <Title order={2}>{t("Platform")}</Title>
          <Text size="sm" c="dimmed">
            {t(
              "Repository source, branch/commit selection and running images, driven by the installed Actions.",
            )}
          </Text>
        </Stack>
        <Group>
          <Select
            label={t("Project")}
            data={projects.map((item) => item.prefix)}
            value={prefix}
            onChange={setSelected}
            placeholder={t("Project")}
            w={220}
            allowDeselect={false}
          />
          <Button
            component={Link}
            to="/updates"
            variant="default"
            size="sm"
          >
            {t("Task logs")}
          </Button>
        </Group>
      </Group>

      {projects.length === 0 && (
        <Alert color="yellow" title={t("No platform project found")}>
          {t(
            "Install the local platform adapters first: the page only lists projects that have the versions and list-refs Actions.",
          )}
        </Alert>
      )}

      {project && (
        <Stack gap="lg">
          <SourceSection project={project} onRun={setRun} />
          <RefsSection project={project} onRun={setRun} />
          <VersionsSection project={project} onRun={setRun} />
        </Stack>
      )}

      {run && <RunPanel run={run} onClose={() => setRun(null)} />}
    </Stack>
  );
}

/**
 * Shared plumbing: every section runs one installed Action and follows the resulting update.
 * The machine-readable line of the completed update becomes the section's data, so the page
 * shows exactly what the executor reported, and the log panel can still be opened.
 */
function usePlatformRun(onRun: (run: Running) => void, label: string) {
  const { mutateAsync: execute, isPending } = useExecute("RunAction");
  const [updateId, setUpdateId] = useState<string>();
  const { data: update } = useRead(
    "GetUpdate",
    { id: updateId as string },
    { enabled: !!updateId, refetchInterval: updateId ? 1_500 : false },
  );
  const complete = update?.status === "Complete";
  const payload = useMemo(
    () => (complete ? parsePlatformPayload<unknown>(update?.logs) : undefined),
    [complete, update?.logs],
  );
  // An Action that fails before answering (for example an invalid source block) has no
  // machine-readable line; the section must still say what went wrong.
  const failure = complete && !payload ? actionFailureText(update?.logs) : undefined;
  const start = async (action: string, args: Record<string, string>) => {
    setUpdateId(undefined);
    try {
      const submitted = (await execute({ action, args })) as Types.Update;
      const id = submitted?._id?.$oid;
      if (!id) throw new Error("no update id");
      setUpdateId(id);
      onRun({ title: `${label} · ${action}`, id });
      notifications.show({
        title: t("Task submitted"),
        message: `${action} · ${id}`,
        color: "blue",
      });
      return id;
    } catch (error) {
      notifications.show({
        title: t("Task could not be submitted"),
        message: String((error as Error)?.message ?? error),
        color: "red",
      });
      return undefined;
    }
  };
  return { start, isPending, update, complete, payload, failure };
}

function SectionCard({
  title,
  action,
  onRefresh,
  children,
}: {
  title: string;
  action?: string;
  onRefresh?: () => void;
  children: React.ReactNode;
}) {
  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm">
        <Group gap="sm">
          <Title order={4}>{title}</Title>
          {action && (
            <Code>
              {action}
            </Code>
          )}
        </Group>
        {onRefresh && (
          <Button size="xs" variant="default" onClick={onRefresh}>
            {t("Refresh")}
          </Button>
        )}
      </Group>
      {children}
    </Card>
  );
}

const data = <T,>(payload: unknown): T | undefined =>
  payload && typeof payload === "object" ? (payload as T) : undefined;

function SourceSection({
  project,
  onRun,
}: {
  project: PlatformProject;
  onRun: (run: Running) => void;
}) {
  const { start, isPending, payload, failure } = usePlatformRun(onRun, t("Repository source"));
  const { data: action } = useRead("GetAction", { action: project.source });
  const [draft, setDraft] = useState<{
    mode: string;
    remote: string;
    refs: string;
    defaultRef: string;
  } | null>(null);

  // The reviewed editor's current defaults describe the authoritative source block; the Action
  // stores them as a JSON string, so parse them instead of silently editing an empty block.
  const defaults = useMemo(
    () => sourceDefaults(action?.config?.arguments),
    [action?.config?.arguments],
  );
  const current = draft ?? defaults;
  const edit = data<PlatformSourceEdit>(payload);

  const update = (patch: Partial<typeof current>) =>
    setDraft({ ...current, ...patch });

  const save = async () => {
    await start(
      project.source,
      sourceArgs(
        current.mode,
        current.remote,
        current.refs
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        current.defaultRef,
      ),
    );
  };

  return (
    <SectionCard title={t("Repository source")} action={project.source}>
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
          <TextInput
            label={t("Mode")}
            value={current.mode}
            onChange={(event) => update({ mode: event.currentTarget.value })}
            description={t("local or remote")}
          />
          <TextInput
            label={t("Remote")}
            value={current.remote}
            onChange={(event) => update({ remote: event.currentTarget.value })}
            disabled={current.mode !== "remote"}
            description={t("A remote name configured on the build host")}
          />
          <TextInput
            label={t("Registered refs")}
            value={current.refs}
            onChange={(event) => update({ refs: event.currentTarget.value })}
            description={t("Comma separated; only these may be built")}
          />
          <TextInput
            label={t("Default ref")}
            value={current.defaultRef}
            onChange={(event) => update({ defaultRef: event.currentTarget.value })}
            description={t("Must be one of the registered refs")}
          />
        </SimpleGrid>
        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {t(
              "Saving goes through the reviewed source editor: one block only, normalised and validated, full backup, no write when unchanged.",
            )}
          </Text>
          <Button
            size="xs"
            loading={isPending}
            disabled={!current.refs.trim()}
            onClick={save}
          >
            {t("Save source")}
          </Button>
        </Group>
        {failure && <Alert color="red">{failure}</Alert>}
        {edit && (
          <Alert
            color={edit.status === "succeeded" ? "teal" : "red"}
            title={
              edit.status === "succeeded"
                ? edit.changed === false
                  ? t("Unchanged: nothing was written")
                  : t("Source updated")
                : t("Source update failed")
            }
          >
            <Text size="sm">
              {edit.status === "succeeded"
                ? t(
                    "Recipe digest {0} · backup {1} · untouched sections {2}",
                    {
                      0: shortSha(edit.recipe_digest, 12),
                      1: edit.backup ?? "—",
                      2: (edit.unchanged_sections ?? []).join(", ") || "—",
                    },
                  )
                : edit.error}
            </Text>
          </Alert>
        )}
      </Stack>
    </SectionCard>
  );
}

function RefsSection({
  project,
  onRun,
}: {
  project: PlatformProject;
  onRun: (run: Running) => void;
}) {
  const { start, isPending, payload, failure } = usePlatformRun(onRun, t("Branches"));
  const [ref, setRef] = useState<string>("");
  const [confirming, confirm] = useDisclosure(false);
  const refs = data<PlatformRefs>(payload);

  const refresh = async (requested?: string) => {
    await start(project.listRefs, refArgs(requested ?? ""));
    if (requested) setRef(requested);
  };

  const approved = refs?.approved ?? [];
  const tips = refs?.approved_ref_tips ?? {};
  const released = refs?.released_commit ?? null;

  return (
    <SectionCard
      title={t("Branch and commit")}
      action={project.listRefs}
      onRefresh={() => refresh()}
    >
      <Stack gap="sm">
        {failure && <Alert color="red">{failure}</Alert>}
        <Group align="flex-end">
          <Select
            label={t("Registered ref")}
            data={approved}
            value={ref || null}
            onChange={(value) => setRef(value ?? "")}
            placeholder={approved.length ? t("Select a branch") : t("Refresh first")}
            w={280}
            disabled={!approved.length}
          />
          <Button
            size="sm"
            variant="default"
            loading={isPending}
            disabled={!ref}
            onClick={() => refresh(ref)}
          >
            {t("Preview commit")}
          </Button>
          <Button size="sm" disabled={!ref} onClick={confirm.open}>
            {t("Build and deploy")}
          </Button>
          <Button
            size="sm"
            variant="light"
            disabled={!refs}
            onClick={() => refresh()}
          >
            {t("Refresh")}
          </Button>
        </Group>

        {refs && (
          <>
            <Group gap="sm">
              <Badge variant="light">
                {refs.mode === "remote"
                  ? t("Remote {0}", { 0: refs.remote ?? "—" })
                  : t("Local repository")}
              </Badge>
              <Badge variant="light">
                {t("Default {0}", { 0: refs.default_ref ?? "—" })}
              </Badge>
              <Badge variant="light">
                {t("Remote branches {0} · tags {1}", {
                  0: refs.branch_count ?? 0,
                  1: refs.tag_count ?? 0,
                })}
              </Badge>
              {released && (
                <Badge variant="light">
                  {t("Released commit {0}", { 0: shortSha(released) })}
                </Badge>
              )}
            </Group>

            <Table striped withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t("Registered ref")}</Table.Th>
                  <Table.Th>{t("Current tip")}</Table.Th>
                  <Table.Th>{t("Compared to the released commit")}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {approved.map((name) => (
                  <Table.Tr key={name}>
                    <Table.Td>
                      <Text size="sm">{name}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Code>{shortSha(tips[name])}</Code>
                    </Table.Td>
                    <Table.Td>
                      {!released ? (
                        <Text size="sm" c="dimmed">
                          {t("No release record yet")}
                        </Text>
                      ) : tips[name] === released ? (
                        <Badge color="teal" variant="light">
                          {t("Same as released")}
                        </Badge>
                      ) : (
                        <Badge color="yellow" variant="light">
                          {t("New commits since the release")}
                        </Badge>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>

            {refs.requested_ref && (
              <Alert
                color={
                  refs.requested_ref_approved && refs.commit ? "teal" : "red"
                }
                title={t("Preview for {0}", { 0: refs.requested_ref })}
              >
                <Text size="sm">
                  {refs.commit
                    ? t("Will build commit {0}", { 0: refs.commit })
                    : refs.resolution_error ??
                      t("This ref cannot be resolved to a commit")}
                </Text>
              </Alert>
            )}
            {(refs.commits ?? []).length > 0 && (
              <Stack gap={2}>
                <Text size="sm" fw={500}>
                  {t("Recent commits")}
                </Text>
                {(refs.commits ?? []).slice(0, 8).map((commit) => (
                  <Text key={commit.sha} size="xs" c="dimmed">
                    {shortSha(commit.sha)} · {commit.subject}
                  </Text>
                ))}
              </Stack>
            )}
            {!approved.length && (
              <Alert color="yellow">
                {t(
                  "No ref is registered for this project, so nothing can be built. Register one in the source section first.",
                )}
              </Alert>
            )}
          </>
        )}
        {!refs && (
          <Text size="sm" c="dimmed">
            {t("Refresh to load the registered refs and their current tips.")}
          </Text>
        )}
      </Stack>

      <Modal
        opened={confirming}
        onClose={confirm.close}
        title={t("Build and deploy {0}", { 0: ref })}
      >
        <Stack>
          <Text size="sm">
            {t(
              "Builds the selected ref in an isolated worktree, runs the project verification, releases the images and checks health. The previous images stay available for rollback.",
            )}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={confirm.close}>
              {t("Cancel")}
            </Button>
            <Button
              onClick={() => {
                confirm.close();
                start(project.build, releaseArgs("build-deploy", { ref }));
              }}
            >
              {t("Submit")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </SectionCard>
  );
}

function VersionsSection({
  project,
  onRun,
}: {
  project: PlatformProject;
  onRun: (run: Running) => void;
}) {
  const { start, isPending, payload, failure } = usePlatformRun(onRun, t("Versions"));
  const [version, setVersion] = useState("");
  const [confirming, confirm] = useDisclosure(false);
  const versions = data<PlatformVersions>(payload);

  const refresh = () => start(project.versions, {});

  return (
    <SectionCard
      title={t("Versions and running images")}
      action={project.versions}
      onRefresh={refresh}
    >
      <Stack gap="sm">
        {failure && <Alert color="red">{failure}</Alert>}
        {!versions && (
          <Text size="sm" c="dimmed">
            {t(
              "Refresh to load the repository HEAD, the configured source, the candidate build, the last release and the running images.",
            )}
          </Text>
        )}
        {versions && (
          <>
            <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
              <Stat
                title={t("Repository")}
                lines={[
                  t("HEAD {0}", { 0: shortSha(versions.repository?.head) }),
                  t("Branch {0}", { 0: versions.repository?.branch ?? "—" }),
                  versions.repository?.dirty
                    ? t("Tracked changes present")
                    : t("No tracked changes"),
                  t("Untracked files {0}", {
                    0: versions.repository?.untracked ?? 0,
                  }),
                ]}
              />
              <Stat
                title={t("Configured source")}
                lines={[
                  t("Mode {0}", { 0: versions.recipe?.source_mode ?? "—" }),
                  t("Registered refs {0}", {
                    0: (versions.recipe?.approved_refs ?? []).join(", ") || "—",
                  }),
                  t("Default {0}", { 0: versions.recipe?.default_ref ?? "—" }),
                  t("Recipe digest {0}", {
                    0: shortSha(versions.recipe?.digest, 12),
                  }),
                ]}
              />
              <Stat
                title={t("Last release")}
                lines={versions.release
                  ? [
                      versions.release.version ?? "—",
                      t("Commit {0}", {
                        0: shortSha(
                          versions.release.commit ?? versions.release.snapshot,
                        ),
                      }),
                      t("Previous {0}", {
                        0: versions.release.previous_version ?? "—",
                      }),
                    ]
                  : [t("No release record yet")]}
              />
            </SimpleGrid>

            <Group gap="sm">
              <Badge
                variant="light"
                color={
                  versions.candidate?.deployed ? "teal" : versions.candidate ? "yellow" : "gray"
                }
              >
                {versions.candidate
                  ? t("Candidate {0} · {1}{2}", {
                      0: shortSha(versions.candidate.job, 8),
                      1: versions.candidate.status ?? "—",
                      2: versions.candidate.deployed
                        ? " · " + t("deployed")
                        : "",
                    })
                  : t("No candidate build")}
              </Badge>
              {versions.verdict && (
                <Badge variant="light" color="blue">
                  {versions.verdict}
                </Badge>
              )}
            </Group>

            <Table striped withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t("Service")}</Table.Th>
                  <Table.Th>{t("State")}</Table.Th>
                  <Table.Th>{t("Image")}</Table.Th>
                  <Table.Th>{t("Matches the release")}</Table.Th>
                  <Table.Th>{t("Matches the candidate")}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(versions.running ?? []).map((service) => (
                  <Table.Tr key={service.service}>
                    <Table.Td>{service.service}</Table.Td>
                    <Table.Td>
                      <Badge
                        variant="light"
                        color={
                          service.health === "healthy" || service.state === "running"
                            ? "teal"
                            : "yellow"
                        }
                      >
                        {service.state}
                        {service.health ? ` / ${service.health}` : ""}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Code>{shortSha(service.image_id, 19)}</Code>
                    </Table.Td>
                    <Table.Td>
                      <Yes value={service.matches_release} />
                    </Table.Td>
                    <Table.Td>
                      <Yes value={service.matches_candidate} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>

            <Divider />
            <Group align="flex-end">
              <TextInput
                label={t("Version to deploy")}
                value={version}
                onChange={(event) => setVersion(event.currentTarget.value)}
                placeholder={versions.release?.version ?? "2026-09-22.1"}
                w={320}
              />
              <Button
                size="sm"
                variant="default"
                disabled={!version}
                onClick={() => start(project.deploy, releaseArgs("deploy", { version }))}
              >
                {t("Deploy this version")}
              </Button>
              <Button size="sm" color="orange" variant="light" onClick={confirm.open}>
                {t("Roll back to the previous version")}
              </Button>
              {isPending && <Loader size="xs" />}
            </Group>
            <Text size="xs" c="dimmed">
              {t(
                "Only registered, already verified manifests can be deployed. A rollback restores the previous image set, never the data.",
              )}
            </Text>
          </>
        )}
      </Stack>

      <Modal
        opened={confirming}
        onClose={confirm.close}
        title={t("Roll back {0}", { 0: project.prefix })}
      >
        <Stack>
          <Text size="sm">
            {t(
              "Restores the previous verified image set for this project and checks health. Business data is not touched.",
            )}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={confirm.close}>
              {t("Cancel")}
            </Button>
            <Button
              color="orange"
              onClick={() => {
                confirm.close();
                start(project.rollback, releaseArgs("rollback"));
              }}
            >
              {t("Submit")}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </SectionCard>
  );
}

const Stat = ({ title, lines }: { title: string; lines: string[] }) => (
  <Card withBorder p="sm">
    <Text size="sm" fw={500} mb={4}>
      {title}
    </Text>
    <Stack gap={2}>
      {lines.map((line) => (
        <Text key={line} size="xs" c="dimmed">
          {line}
        </Text>
      ))}
    </Stack>
  </Card>
);

const Yes = ({ value }: { value: boolean | undefined }) =>
  value === undefined ? (
    <Text size="sm" c="dimmed">
      —
    </Text>
  ) : (
    <Badge variant="light" color={value ? "teal" : "yellow"}>
      {value ? t("Yes") : t("No")}
    </Badge>
  );

/**
 * Follows one submitted Action: the update carries the plateau logs and the page's own
 * machine-readable line. Cancelling here never cancels the Windows transaction.
 */
function RunPanel({ run, onClose }: { run: { title: string; id: string }; onClose: () => void }) {
  const { data: update } = useRead("GetUpdate", { id: run.id }, { refetchInterval: 1_500 });
  const payload = parsePlatformPayload<{ kind?: string; status?: string; error?: string }>(
    update?.logs,
  );
  const complete = update?.status === "Complete";
  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Group gap="sm">
          <Title order={5}>{run.title}</Title>
          <Code>{run.id}</Code>
          {!complete && <Loader size="xs" />}
          {payload?.status && (
            <Badge
              variant="light"
              color={payload.status === "succeeded" ? "teal" : payload.status === "failed" ? "red" : "yellow"}
            >
              {payload.status}
            </Badge>
          )}
        </Group>
        <Group gap="sm">
          <Button component={Link} to={`/updates/${run.id}`} size="xs" variant="default">
            {t("Open the task")}
          </Button>
          <Button size="xs" variant="subtle" onClick={onClose}>
            {t("Close")}
          </Button>
        </Group>
      </Group>
      {payload?.error && (
        <Alert color="red" mb="xs">
          {payload.error}
        </Alert>
      )}
      <ScrollArea h={240} type="auto">
        <Code block style={{ whiteSpace: "pre-wrap" }}>
          {updateText(update?.logs) || t("Waiting for the executor…")}
        </Code>
      </ScrollArea>
      <Text size="xs" c="dimmed" mt="xs">
        {t(
          "Closing or cancelling this view does not stop the Windows transaction; check the task id before resubmitting.",
        )}
      </Text>
    </Card>
  );
}
