import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Link2, Plug, Tags } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { databaseKeys } from "../../api/queries/databases";
import type { Database, Engine } from "../../api/queries/databases";
import { jobKeys } from "../../api/queries/jobs";
import { RowMenu } from "../../components/page/RowMenu";
import { IconButton } from "../../components/ui/IconButton";
import { MenuGroup, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { ResourceTagsDialog } from "../app/settings/ResourceTagsDialog";
import { reportActionError } from "../apps/useAppActions";
import { CopyConnection } from "./CopyConnection";
import { isNotAdded } from "./DatabasesTable";
import { can } from "./engines";

/**
 * What a row offers at its end: to copy its connection when an application uses it; to read how to reach it otherwise;
 * nothing for one that is gone or that Noust does not hold yet (adding it is in its Backup cell). Then its menu.
 */
export function DatabaseRowActions({ database, engines }: { database: Database; engines: readonly Engine[] | undefined }) {
  const t = useT();
  const navigate = useNavigate();
  return (
    <span className="flex items-center justify-end gap-1">
      {database.missing || isNotAdded(database) ? null : (database.apps ?? []).length > 0 ? (
        <CopyConnection engine={database.engine} name={database.name} />
      ) : (
        <IconButton
          label={t("databases.listRow.connection", { name: database.name })}
          icon={<Plug />}
          size="sm"
          onClick={() => void navigate({ to: "/databases/$engine/$name/connect", params: { engine: database.engine, name: database.name } })}
        />
      )}
      <DatabaseMenu database={database} engines={engines} />
    </span>
  );
}

/** The menu at the end of a database's row: open it, back it up now, query it, connect to it. */
function DatabaseMenu({ database, engines }: { database: Database; engines: readonly Engine[] | undefined }) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const engine = engines?.find((item) => item.name === database.engine);
  const params = { engine: database.engine, name: database.name };
  const backup = useMutation({
    mutationFn: () => request("post", "/api/databases/backup-policies/{engine}/{database}/run", { params: { engine: database.engine, database: database.name } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      toast.info(t("databases.list.backupQueued", { name: database.name }), {
        action: { label: t("databases.list.follow"), onClick: () => void navigate({ to: "/databases/$engine/$name/backups", params }) },
      });
    },
    onError: (error) => reportActionError(t("databases.list.backupFailed", { name: database.name }), error),
  });
  // A use found in an application's environment becomes a recorded link; the application is
  // not touched (its environment already names the database), so nothing needs confirming.
  const record = useMutation({
    mutationFn: (app: string) =>
      request("post", "/api/databases/databases/{engine}/{name}/links/detected", { params: { engine: database.engine, name: database.name }, body: { app } }),
    // The row says it: the application is a link now, and no longer "Detected".
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.database(database.engine, database.name) });
    },
    onError: (error, app) => reportActionError(t("databases.list.recordFailed", { app, name: database.name }), error),
  });
  const detected = (database.detected_apps ?? []).filter((app) => !(database.apps ?? []).includes(app));
  const [editingTags, setEditingTags] = useState(false);
  return (
    <>
      <RowMenu name={database.name}>
        <MenuItem icon={<OBJECT_ICONS.database />} onClick={() => void navigate({ to: "/databases/$engine/$name", params })}>
          {t("databases.list.open")}
        </MenuItem>
        {can(engine, "sql") ? (
          <MenuItem icon={<OBJECT_ICONS.terminal />} disabled={database.missing} onClick={() => void navigate({ to: "/databases/$engine/$name/query", params })}>
            {t("databases.list.query")}
          </MenuItem>
        ) : null}
        <MenuItem icon={<Plug />} disabled={database.missing} onClick={() => void navigate({ to: "/databases/$engine/$name/connect", params })}>
          {t("databases.list.connect")}
        </MenuItem>
        <MenuItem icon={<Tags />} disabled={database.missing} onClick={() => setEditingTags(true)}>
          {t("databases.list.editTags")}
        </MenuItem>
        {can(engine, "dump") ? (
          <>
            <MenuSeparator />
            <MenuItem icon={<OBJECT_ICONS.backup />} disabled={database.missing || backup.isPending} onClick={() => backup.mutate()}>
              {t("common.words.backUpNow")}
            </MenuItem>
          </>
        ) : null}
        {detected.length > 0 ? (
          <>
            <MenuSeparator />
            <MenuGroup label={t("databases.list.detectedGroup")}>
              {detected.map((app) => (
                <MenuItem key={app} icon={<Link2 />} disabled={record.isPending} description={t("databases.list.recordLinkDescription")} onClick={() => record.mutate(app)}>
                  {t("databases.list.recordLink", { app })}
                </MenuItem>
              ))}
            </MenuGroup>
          </>
        ) : null}
      </RowMenu>
      {editingTags ? (
        <ResourceTagsDialog
          name={database.name}
          noun={t("databases.list.noun")}
          tags={database.tags ?? []}
          save={(tags) => request("put", "/api/databases/databases/{engine}/{name}/tags", { params: { engine: database.engine, name: database.name }, body: { tags } })}
          onSaved={() => void queryClient.invalidateQueries({ queryKey: databaseKeys.lists })}
          onClose={() => setEditingTags(false)}
        />
      ) : null}
    </>
  );
}
