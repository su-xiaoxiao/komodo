/**
 * Shared plumbing for the platform page sections: run one installed Action, follow the resulting
 * update, and hand the section the machine-readable line it prints. Also the card wrapper.
 */
import { Button, Card, Code, Group, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { Types } from "komodo_client";
import { useMemo, useState } from "react";
import { t } from "@/localization/runtime";
import { useExecute, useRead } from "@/lib/hooks";
import { actionFailureText, parsePlatformPayload } from "@/lib/platform";

export type Running = { title: string; id: string } | null;

export function usePlatformRun(onRun: (run: Running) => void, label: string) {
  const { mutateAsync: execute, isPending } = useExecute("RunAction");
  const [updateId, setUpdateId] = useState<string>();
  const { data: update } = useRead(
    "GetUpdate",
    { id: updateId as string },
    { enabled: !!updateId, refetchInterval: updateId ? 1_500 : false },
  );
  const complete = update?.status === "Complete";
  const payload = useMemo(
    () => (complete ? parsePlatformPayload<Record<string, unknown>>(update?.logs) : undefined),
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

export function SectionCard({
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
          {action && <Code>{action}</Code>}
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

export const data = <T,>(payload: unknown): T | undefined =>
  payload && typeof payload === "object" ? (payload as T) : undefined;
