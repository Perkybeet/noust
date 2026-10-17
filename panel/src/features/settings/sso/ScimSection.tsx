import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Users } from "lucide-react";
import { useState } from "react";

import { removeScimSource, scimKeys, scimSourcesQuery } from "../../../api/queries/scim";
import type { ScimSource } from "../../../api/queries/scim";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { RowAction } from "../RowAction";
import { ScimDrawer } from "./ScimDrawer";

function columnsFor(t: T): Column<ScimSource>[] {
  return [
    {
      id: "source",
      header: t("scimAdmin.column.source"),
      card: "title",
      cell: (source) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <span className="text-13 font-medium text-fg">{source.label}</span>
          <Mono tone="faint">{source.name}</Mono>
        </span>
      ),
    },
    {
      id: "state",
      header: t("scimAdmin.column.state"),
      card: "status",
      width: "w-32",
      cell: (source) => (
        <StatusPill
          state={source.enabled ? "running" : "stopped"}
          label={source.enabled ? t("scimAdmin.state.enabled") : t("scimAdmin.state.disabled")}
          appearance="inline"
          size="sm"
        />
      ),
    },
    {
      id: "people",
      header: t("scimAdmin.column.people"),
      hideBelow: "sm",
      cell: (source) => <span className="text-13 text-fg-muted">{source.users}</span>,
    },
    {
      id: "starts",
      header: t("scimAdmin.column.starts"),
      hideBelow: "md",
      cell: (source) => <Mono tone="default">{source.default_role}</Mono>,
    },
    {
      id: "last-used",
      header: t("scimAdmin.column.lastUsed"),
      hideBelow: "lg",
      cell: (source) => <RelativeTime value={source.last_used_at} fallback={t("scimAdmin.neverUsed")} />,
    },
  ];
}

/**
 * Settings > Single sign-on > Provisioning: the identity providers that keep the roster of who
 * has an account. They create an account for a new person and switch it off when they leave; they
 * sign nobody in, which is the providers' above. Each authenticates with a token of its own, shown
 * once when it is issued.
 */
export function ScimSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(scimSourcesQuery());
  const [editing, setEditing] = useState<ScimSource | "new" | null>(null);
  const [removing, setRemoving] = useState<ScimSource | null>(null);
  const columns = columnsFor(t);
  const add = (
    <Button variant="secondary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing("new")}>
      {t("scimAdmin.add")}
    </Button>
  );

  return (
    <Section title={t("scimAdmin.title")} description={t("scimAdmin.description")} actions={query.data !== undefined && query.data.sources.length > 0 ? add : undefined}>
      <QueryState
        query={query}
        label={t("scimAdmin.loadingLabel")}
        skeleton={<DataTable caption={t("scimAdmin.tableCaption")} columns={columns} rows={[]} getRowId={(source) => source.name} loading mobile="cards" skeletonRows={1} />}
        isEmpty={(data) => data.sources.length === 0}
        empty={
          <EmptyState
            variant="firstUse"
            level={3}
            icon={<Users />}
            title={t("scimAdmin.emptyTitle")}
            description={t("scimAdmin.emptyDescription")}
            action={add}
            command="noust auth scim set entra --label 'Entra ID'"
          />
        }
      >
        {(data) => (
          <div className="flex flex-col gap-4">
            <DataTable
              caption={t("scimAdmin.tableCaption")}
              columns={columns}
              rows={data.sources}
              getRowId={(source) => source.name}
              mobile="cards"
              rowActions={(source) => (
                <span className="flex items-center gap-1">
                  <RowAction
                    label={t("scimAdmin.editLabel", { name: source.name })}
                    text={t("scimAdmin.editText")}
                    icon={<Pencil />}
                    onClick={() => {
                      setEditing(source);
                    }}
                  />
                  <RowAction
                    label={t("scimAdmin.removeLabel", { name: source.name })}
                    text={t("scimAdmin.removeText")}
                    icon={<ICONS.delete />}
                    onClick={() => {
                      setRemoving(source);
                    }}
                  />
                </span>
              )}
            />
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("scimAdmin.endpoint")}</p>
              <p className="max-w-measure text-13 text-fg-muted">{t("scimAdmin.endpointHelp")}</p>
              <CopyValue value={data.base_url} label={t("scimAdmin.endpoint")} name={t("scimAdmin.endpoint")} />
            </div>
          </div>
        )}
      </QueryState>

      {editing !== null ? (
        <ScimDrawer
          source={editing === "new" ? null : editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(next) => {
            if (!next) setRemoving(null);
          }}
          title={t("scimAdmin.remove.title", { name: removing.name })}
          description={t("scimAdmin.remove.description")}
          actionLabel={t("scimAdmin.remove.action")}
          destructive
          onConfirm={async () => {
            const gone = await removeScimSource(removing.name);
            toast.success(t("scimAdmin.remove.toast", { name: gone.removed }));
            void queryClient.invalidateQueries({ queryKey: scimKeys.all });
          }}
        />
      ) : null}
    </Section>
  );
}
