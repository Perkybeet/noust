import { useQuery } from "@tanstack/react-query";
import { Copy, Eye, Pencil, Plus } from "lucide-react";
import { useState } from "react";

import { QueryState } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { useCan } from "../../components/page/permissions";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
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
      header: t("common.words.name"),
      sortValue: (script) => script.name,
      // "Built in" is a tag of the name, on its line: the table's rows are all one height whatever they carry.
      cell: (script) => <NameWithTags name={script.name} mono tags={script.builtin ? [t("cloud.scripts.builtin")] : []} />,
    },
    { id: "kind", header: t("cloud.scripts.column.kind"), hideBelow: "sm", cell: (script) => <span className="text-13 text-fg">{script.kind === "cloud-config" ? t("cloud.scripts.kindConfig") : t("cloud.scripts.kindScript")}</span> },
    { id: "size", header: t("cloud.scripts.column.size"), hideBelow: "md", cell: (script) => <Mono tone="muted">{script.bytes} B</Mono> },
    {
      id: "description",
      header: t("cloud.scripts.column.description"),
      hideBelow: "lg",
      // One line, cut at the end: a long description would widen the table past its slab, and the whole of it is its title.
      cell: (script) =>
        script.description !== "" ? (
          <span title={script.description} className="block max-w-sm truncate text-13 text-fg-muted">
            {script.description}
          </span>
        ) : (
          <EmptyCell reason={t("cloud.scripts.noDescription")} />
        ),
    },
  ];
}

function ScriptActions({ script }: { script: CloudScript }) {
  const t = useT();
  const { deleteScript } = useCloudActions();
  const [opened, setOpened] = useState<"view" | "edit" | "copy" | null>(null);
  const [deleting, setDeleting] = useState(false);
  return (
    <>
      <RowMenu name={script.name}>
        {script.builtin ? (
          <MenuItem icon={<Eye />} onClick={() => setOpened("view")}>
            {t("cloud.scripts.view")}
          </MenuItem>
        ) : (
          <MenuItem icon={<Pencil />} onClick={() => setOpened("edit")}>
            {t("common.words.edit")}
          </MenuItem>
        )}
        <MenuItem icon={<Copy />} onClick={() => setOpened("copy")}>
          {t("cloud.scripts.copy")}
        </MenuItem>
        {script.builtin ? null : (
          <>
            <MenuSeparator />
            <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(true)}>
              {t("common.words.delete")}
            </MenuItem>
          </>
        )}
      </RowMenu>
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
          // design-exception: write-without-permission opened only from the script's row menu, which is drawn only for fleet.manage
          await deleteScript.mutateAsync(script.name);
        }}
      />
    </>
  );
}

/**
 * The scripts and fragments a new server can boot with: Noust's examples, read-only, and the operator's own. What
 * Noust's own part of the document does, and that no token travels in it, is the rule behind the "?".
 */
export function ScriptsSection() {
  const t = useT();
  const canManage = useCan("fleet.manage");
  const query = useQuery(cloudScriptsQuery());
  const [writing, setWriting] = useState(false);
  const columns = columnsFor(t);
  return (
    <SurfaceSection
      title={t("cloud.scripts.title")}
      icon={OBJECT_ICONS.script}
      description={t("cloud.scripts.description")}
      help={t("cloud.scripts.note")}
      actions={
        canManage ? (
          <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setWriting(true)}>
            {t("cloud.scripts.add")}
          </Button>
        ) : undefined
      }
      flush
    >
      <QueryState
        query={query}
        label={t("cloud.scripts.loadingLabel")}
        skeleton={<DataTable embedded caption={t("cloud.scripts.caption")} columns={columns} rows={[]} getRowId={(script) => script.name} loading skeletonRows={3} />}
        isEmpty={(data) => data.length === 0}
        empty={<EmptyState variant="inline" title={t("cloud.scripts.empty")} className="px-5" />}
      >
        {(scripts) => (
          <DataTable
            embedded
            caption={t("cloud.scripts.caption")}
            columns={columns}
            rows={scripts}
            getRowId={(script) => script.name}
            {...(canManage ? { rowActions: (script: CloudScript) => <ScriptActions script={script} /> } : {})}
          />
        )}
      </QueryState>
      {writing && canManage ? <ScriptDialog open onOpenChange={setWriting} /> : null}
    </SurfaceSection>
  );
}
