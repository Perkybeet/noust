import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { MoreHorizontal, Trash2 } from "lucide-react";
import { useState } from "react";

import { databaseQuery } from "../../api/queries/databases";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import { ConnectionString } from "./ConnectionString";
import { DatabaseBackups } from "./DatabaseBackups";
import { engineLabel } from "./data";
import { SqlConsole } from "./SqlConsole";
import { useDatabaseActions } from "./useDatabaseActions";

function Overview({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const database = useQuery(databaseQuery(engine, name));

  if (database.isError && database.data === undefined) {
    return (
      <ErrorBlock
        error={database.error}
        title={t("databases.detail.overviewLoadFailed")}
        onRetry={() => void database.refetch()}
        retrying={database.isRefetching}
      />
    );
  }
  if (database.data === undefined) {
    return (
      <div className="rounded-card border border-border bg-surface px-4 py-1">
        <KeyValueListSkeleton rows={5} />
      </div>
    );
  }

  const info = database.data;
  const items: KeyValueItem[] = [
    { label: t("databases.fields.engine"), value: engineLabel(info.engine) },
    { label: info.engine === "redis" ? t("databases.fields.keys") : t("databases.fields.tables"), value: info.tables, mono: true },
    { label: t("databases.fields.size"), value: info.size, mono: true },
    { label: t("databases.fields.owner"), value: info.owner, mono: true },
    { label: t("databases.fields.encoding"), value: info.encoding, mono: true },
  ];

  return (
    <div className="rounded-card border border-border bg-surface px-4 py-1">
      <KeyValueList items={items} />
    </div>
  );
}

/** One database: what it is, its own backups, a connection string on request, and the console. */
export function DatabasePage({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const navigate = useNavigate();
  const { dropDatabase } = useDatabaseActions();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const isRedis = engine === "redis";
  const dropAction = isRedis ? t("databases.detail.dropSlot") : t("databases.table.dropDatabase");

  return (
    <>
      <PageHeader
        title={name}
        description={t(isRedis ? "databases.detail.aboutSlot" : "databases.detail.aboutDatabase", { engine: engineLabel(engine) })}
        breadcrumbs={[{ label: t("nav.databases.label"), to: "/databases" }]}
        actions={
          <Menu align="end" trigger={<Button icon={<MoreHorizontal aria-hidden="true" />}>{t("databases.detail.actionsMenu")}</Button>}>
            <MenuItem icon={<Trash2 />} destructive onClick={() => setConfirmOpen(true)}>
              {dropAction}
            </MenuItem>
          </Menu>
        }
      />
      <div className="flex flex-col gap-8">
        <Section title={t("nav.appTabs.overview.label")}>
          <Overview engine={engine} name={name} />
          <CommandHint command={`noust db info ${name} --engine ${engine}`} label={t("databases.fromTerminal")} />
        </Section>

        {/* The console is what this page is opened for most: right under what the database is. */}
        <SqlConsole engine={engine} database={name} />

        <DatabaseBackups engine={engine} database={name} />

        <ConnectionString engine={engine} database={name} />
      </div>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("databases.table.dropTitle", { name })}
        description={t("databases.detail.dropDescription", { name, engine: engineLabel(engine) })}
        confirmText={name}
        actionLabel={dropAction}
        onConfirm={async () => {
          await dropDatabase.mutateAsync({ engine, name });
          void navigate({ to: "/databases" });
        }}
      />
    </>
  );
}
