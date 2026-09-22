import { Page } from "mogh_ui";
import {
  useDashboardPreferences,
  useSetTitle,
  useTagsFilter,
} from "@/lib/hooks";
import { ICONS } from "@/lib/icons";
import { Group, Stack } from "@mantine/core";
import { GroupSummary } from "@/components/platform/group-overview";
import DashboardRecents from "./recents";
import ExportToml from "@/components/export-toml";
import ServerShowStats from "@/resources/server/show-stats";
import ShowTables from "./show-tables";
import DashboardTables from "./tables";
import DashboardActiveResources from "./active";

export default function Dashboard() {
  const { preferences } = useDashboardPreferences();
  const tags = useTagsFilter();
  useSetTitle(undefined);
  return (
    <Page
      aboveTitle={<DashboardActiveResources />}
      title="Dashboard"
      icon={ICONS.Dashboard}
      oppositeTitle={
        <Group w={{ base: "100%", xs: "fit-content" }}>
          <ShowTables />
          <ServerShowStats />
          <ExportToml tags={preferences.showTables ? tags : undefined} />
        </Group>
      }
    >
      <Stack gap="lg">
        <GroupSummary />
        {preferences.showTables ? <DashboardTables /> : <DashboardRecents />}
      </Stack>
    </Page>
  );
}
