import { useQuery } from "@tanstack/react-query";
import { Copy, Eye, FileCode, Pencil, Plus } from "lucide-react";
import { useState } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cloudScriptsQuery } from "./data";
import type { CloudScript } from "./data";
import { ScriptDialog } from "./ScriptDialog";
import { useCloudActions } from "./useCloudActions";

function columnsFor(t: T): readonly Column<CloudScript>[] {
  return [
    {
      id: "name",
      header: t("cloud.scripts.column.name"),
      sortValue: (script) => script.name,
      cell: (script) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono className="font-medium">{script.name}</Mono>
          {script.builtin ? <Badge>{t("cloud.scripts.builtin")}</Badge> : null}
        </span>
      ),
    },
    { id: "kind", header: t("cloud.scripts.column.kind"), hideBelow: "sm", cell: (script) => <span className="text-13 text-fg">{script.kind === "cloud-config" ? t("cloud.scripts.kindConfig") : t("cloud.scripts.kindScript")}</span> },
    { id: "size", header: t("cloud.scripts.column.size"), hideBelow: "md", cell: (script) => <Mono tone="muted">{script.bytes} B</Mono> },
    {
      id: "description",
      header: t("cloud.scripts.column.description"),
      hideBelow: "lg",
      cell: (script) => (script.description !== "" ? <span className="text-13 text-fg-muted">{script.description}</span> : <EmptyCell reason={t("cloud.scripts.noDescription")} />),
    },
  ];
}

function RowActions({ script }: { script: CloudScript }) {
  const t = useT();
  const { deleteScript } = useCloudActions();
  const [opened, setOpened] = useState<"view" | "edit" | "copy" | null>(null);
  const [deleting, setDeleting] = useState(false);
  return (
    <>
      <Menu align="end" trigger={<IconButton label={t("cloud.scripts.actionsFor", { name: script.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
        {script.builtin ? (
          <MenuItem icon={<Eye />} onClick={() => setOpened("view")}>
            {t("cloud.scripts.view")}
          </MenuItem>
        ) : (
          <MenuItem icon={<Pencil />} onClick={() => setOpened("edit")}>
            {t("cloud.scripts.edit")}
          </MenuItem>
        )}
        <MenuItem icon={<Copy />} onClick={() => setOpened("copy")}>
          {t("cloud.scripts.copy")}
        </MenuItem>
        {script.builtin ? null : (
          <>
            <MenuSeparator />
            <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(true)}>
              {t("cloud.scripts.delete")}
            </MenuItem>
          </>
        )}
      </Menu>
      {opened === "view" || opened === "edit" ? <ScriptDialog name={script.name} open onOpenChange={() => setOpened(null)} /> : null}
      {opened === "copy" ? <ScriptDialog copyOf={script.name} open onOpenChange={() => setOpened(null)} /> : null}
      <ConfirmDialog
        friction="simple"
        destructive
        open={deleting}
        onOpenChange={setDeleting}
        title={t("cloud.scripts.deleteTitle", { name: script.name })}
        description={t("cloud.scripts.deleteBody")}
        actionLabel={t("cloud.scripts.deleteAction")}
        onConfirm={async () => {
          await deleteScript.mutateAsync(script.name);
        }}
      />
    </>
  );
}

/** The scripts and fragments a new server can boot with: Noust's examples, read-only, and the operator's own. */
export function ScriptsSection() {
  const t = useT();
  const query = useQuery(cloudScriptsQuery());
  const [writing, setWriting] = useState(false);
  const columns = columnsFor(t);
  return (
    <Section
      title={t("cloud.scripts.title")}
      description={t("cloud.scripts.description")}
      actions={
        <Button icon={<Plus aria-hidden="true" />} onClick={() => setWriting(true)}>
          {t("cloud.scripts.add")}
        </Button>
      }
    >
      <QueryState
        query={query}
        label={t("cloud.scripts.loadingLabel")}
        skeleton={<DataTable caption={t("cloud.scripts.caption")} columns={columns} rows={[]} getRowId={(script) => script.name} loading skeletonRows={3} />}
        isEmpty={(data) => data.length === 0}
        empty={<p className="text-13 text-fg-muted">{t("cloud.scripts.empty")}</p>}
      >
        {(scripts) => <DataTable caption={t("cloud.scripts.caption")} columns={columns} rows={scripts} getRowId={(script) => script.name} rowActions={(script) => <RowActions script={script} />} />}
      </QueryState>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">
        <FileCode aria-hidden="true" className="mr-1 inline size-icon-sm align-text-bottom" />
        {t("cloud.scripts.note")}
      </p>
      <CommandHint label={t("cloud.fromTerminal")} command="noust fleet cloud script list" />
      {writing ? <ScriptDialog open onOpenChange={setWriting} /> : null}
    </Section>
  );
}
