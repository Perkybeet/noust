import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCcwDot } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { databaseKeys, databasesQuery } from "../../api/queries/databases";
import { LinkTabs } from "../../app/LinkTabs";
import type { PageHeaderProps } from "../../app/PageHeader";
import { Button } from "../../components/ui/Button";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { CreateDatabaseDialog } from "./CreateDatabaseDialog";

/**
 * What the Databases and Engines tabs share: the header (its one primary action, "New
 * database", and in its menu adopting what Noust does not track yet), the tabs, and the dialog
 * the primary action opens.
 */
export function useDatabasesHeader(): { header: PageHeaderProps; tabs: ReactNode; dialogs: ReactNode; openCreate: () => void } {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const list = useQuery(databasesQuery());
  const untracked = (list.data?.databases ?? []).filter((database) => !database.tracked && !database.missing).length;
  // Adopting also records the uses found in applications' environments that point at one
  // database without doubt, so it has work to do while any database shows one.
  const detected = (list.data?.databases ?? []).some((database) => (database.detected_apps ?? []).some((app) => !(database.apps ?? []).includes(app)));

  const adopt = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/adopt", { body: { engine: null } }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      const links = result.links?.length ?? 0;
      if (result.adopted.length === 0 && links > 0) toast.success(t("databases.adopt.links", { count: links }));
      else toast.success(t("databases.adopt.done", { count: result.adopted.length }), links > 0 ? { description: t("databases.adopt.links", { count: links }) } : {});
    },
    onError: (error) => reportActionError(t("databases.adopt.failed"), error),
  });

  const Add = ICONS.add;
  return {
    header: {
      title: t("databases.page.title"),
      description: t("databases.page.description"),
      server: node,
      primaryAction: (
        <Button variant="primary" icon={<Add aria-hidden="true" />} onClick={() => setCreating(true)}>
          {t("databases.page.newDatabase")}
        </Button>
      ),
      overflow: (
        <MenuItem icon={<RefreshCcwDot />} disabled={(untracked === 0 && !detected) || adopt.isPending} onClick={() => adopt.mutate()}>
          {untracked > 0 ? t("databases.adopt.action", { count: untracked }) : detected ? t("databases.adopt.linksOnly") : t("databases.adopt.nothing")}
        </MenuItem>
      ),
    },
    tabs: (
      <LinkTabs
        label={t("databases.tabs.label")}
        tabs={[
          { label: "databases.tabs.databases", to: "/databases", exact: true, count: list.data === undefined ? null : list.data.total },
          { label: "databases.tabs.engines", to: "/databases/engines" },
        ]}
      />
    ),
    dialogs: <CreateDatabaseDialog open={creating} onOpenChange={setCreating} />,
    openCreate: () => setCreating(true),
  };
}
