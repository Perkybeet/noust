import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCcwDot } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { databaseKeys, databasesQuery } from "../../api/queries/databases";
import type { PageHeaderProps } from "../../app/PageHeader";
import { GuardedButton, useCan } from "../../components/page/permissions";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { CreateDatabaseDialog } from "./CreateDatabaseDialog";
import { isNotAdded } from "./DatabasesTable";
import type { ServerActions } from "./servers/useServerActions";

/**
 * What the list of databases is headed by: its one primary action ("New database", which is also the one way to make a
 * database, from the list, from an application and from Home), and in its menu what is rarer: adding what Noust does not hold
 * yet. The engines are their own page: installing or adding one is there, and this dialog sends there when none is running.
 */
export function useDatabasesHeader(actions: ServerActions, description?: ReactNode): { header: PageHeaderProps; dialogs: ReactNode; openCreate: () => void } {
  const t = useT();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const list = useQuery(databasesQuery());
  const mayWrite = useCan("databases.write");
  const untracked = (list.data?.databases ?? []).filter(isNotAdded).filter((database) => !database.missing).length;

  const adopt = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/adopt", { body: { engine: null } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
    },
    onError: (error) => reportActionError(t("databases.adopt.failed"), error),
  });

  const Add = ICONS.add;
  const header: PageHeaderProps = {
    title: t("databases.page.title"),
    ...(description !== undefined ? { description } : {}),
    primaryAction: (
      <GuardedButton permission="databases.write" variant="primary" icon={<Add aria-hidden="true" />} onClick={() => setCreating(true)}>
        {t("databases.page.newDatabase")}
      </GuardedButton>
    ),
    // What is rare is offered to whoever may do it; a menu of nothing is not drawn.
    ...(mayWrite && untracked > 0
      ? {
          overflow: (
            <MenuItem icon={<RefreshCcwDot />} disabled={adopt.isPending} onClick={() => adopt.mutate()}>
              {t("databases.adopt.action", { count: untracked })}
            </MenuItem>
          ),
        }
      : {}),
  };

  return {
    header,
    dialogs: (
      <>
        <CreateDatabaseDialog open={creating} onOpenChange={setCreating} onAddEngine={actions.add} />
        {actions.dialogs}
      </>
    ),
    openCreate: () => setCreating(true),
  };
}
