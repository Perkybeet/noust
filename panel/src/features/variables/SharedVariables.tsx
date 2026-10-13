import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, Pencil, Users, Variable } from "lucide-react";
import { useMemo, useState } from "react";

import { sharedVariablesQuery, variableKeys, variableUsageQuery, variablesApi } from "../../api/queries/variables";
import type { SharedVariable, VariableScope } from "../../api/queries/variables";
import { audienceOf } from "../../api/queries/variables";
import { announce } from "../../app/Announcer";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { CopyButton } from "../../components/ui/CopyButton";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { audienceLabel } from "../app/environment/audience";
import { isMasked } from "../app/environment/draft";
import { reportActionError } from "../apps/useAppActions";
import { SettingsPrimaryAction } from "../settings/SettingsShell";
import { SharedVariableDialog } from "./SharedVariableDialog";
import type { SharedVariableDraft, SharedVariableTarget } from "./SharedVariableDialog";

/** How an application refers to a scope's variable, as the server reads it. */
export function referenceSyntax(scope: VariableScope): string {
  return scope.kind === "preview" ? "{{app.URL}}" : `{{${scope.kind}.NAME}}`;
}

/** The scope's own words: its section title, what it is for, and how it is named in a sentence. */
function words(t: T, scope: VariableScope, name: string): { title: string; description: string; of: string; command: string } {
  const syntax = referenceSyntax(scope);
  switch (scope.kind) {
    case "preview":
      return {
        title: t("variables.shared.title.preview"),
        description: t("variables.shared.description.preview", { example: syntax }),
        of: name,
        command: `noust preview vars list ${scope.domain}`,
      };
    case "server":
      return { title: t("variables.shared.title.server"), description: t("variables.shared.description.server", { syntax }), of: t("variables.shared.scopeNames.server"), command: "noust variables list server" };
    case "team":
      return { title: t("variables.shared.title.team"), description: t("variables.shared.description.team", { syntax }), of: name, command: `noust variables list team --team ${scope.team}` };
    case "project":
      return {
        title: t("variables.shared.title.project"),
        description: t("variables.shared.description.project", { syntax }),
        of: name,
        command: `noust variables list project --team ${scope.team} --project ${scope.project}`,
      };
    case "environment":
      return {
        title: t("variables.shared.title.environment", { name }),
        description: t("variables.shared.description.environment", { syntax }),
        of: name,
        command: `noust variables list environment --team ${scope.team} --project ${scope.project} --environment ${scope.environment}`,
      };
  }
}

function Masked() {
  const t = useT();
  return (
    <span className="text-fg-faint">
      <span aria-hidden="true" className="text-13 leading-none tracking-widest">
        ••••••••
      </span>
      <span className="sr-only">{t("variables.shared.hidden")}</span>
    </span>
  );
}

/** Who refers to a variable, as a dialog: a list of the applications, each a link to its page. */
function UsageDialog({ scope, name, onClose }: { scope: VariableScope; name: string | null; onClose: () => void }) {
  const t = useT();
  const usage = useQuery({ ...variableUsageQuery(scope, name ?? ""), enabled: name !== null });
  return (
    <Dialog
      open={name !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="sm"
      title={t("variables.shared.usageDialog.title", { name: name ?? "" })}
      description={t("variables.shared.usageDialog.description")}
      footer={<Button onClick={onClose}>{t("variables.shared.usageDialog.close")}</Button>}
    >
      <QueryState
        query={usage}
        label={t("variables.shared.queryLabel")}
        skeleton={<div className="h-10" />}
        isEmpty={(data) => data.applications.length === 0}
        empty={<p className="text-13 text-fg-muted">{t("variables.shared.usageDialog.none")}</p>}
      >
        {(data) => (
          <ul aria-label={t("variables.shared.usageDialog.list", { name: name ?? "" })} className="flex flex-col gap-1.5">
            {data.applications.map((domain) => (
              <li key={domain}>
                <TextLink to="/apps/$domain" params={{ domain }} translate="no" size="ui" className="break-all">
                  {domain}
                </TextLink>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Dialog>
  );
}

export interface SharedVariablesProps {
  scope: VariableScope;
  /**
   * What the scope is called in a sentence: the team's, the project's or the environment's
   * name. Not needed for the server.
   */
  name?: string;
  /** Whether the operator may add, change and delete. The server decides again on every call. */
  canManage: boolean;
  /** Draw "Add variable" as the settings page's one primary action, in its header. */
  primary?: boolean;
  /** Heading level of the section: 2 under a page's h1, 3 inside another section. */
  level?: 2 | 3;
  /** Whether the empty state says how to do this from a terminal. */
  command?: boolean;
  /**
   * Draw "nothing yet" as one line, not as the page's first-use block: for a page that holds
   * more (a project's page), where a second first-use block would be one too many.
   */
  inlineEmpty?: boolean;
}

/**
 * The shared variables of one scope: a table of what is defined once and referred to from the
 * applications ("Add variable", edit and delete from a row, who uses each). A change is saved at
 * once and reaches an application at its next deploy; a secret stays hidden until revealed, and
 * revealing it (or editing it, which needs its value) asks to confirm it's you.
 */
export function SharedVariables({ scope, name = "", canManage, primary = false, level = 2, command = true, inlineEmpty = false }: SharedVariablesProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(sharedVariablesQuery(scope));
  const api = variablesApi(scope);
  const text = words(t, scope, name);

  const [editing, setEditing] = useState<SharedVariableTarget | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [using, setUsing] = useState<string | null>(null);
  const [clear, setClear] = useState<ReadonlyMap<string, string>>(new Map());
  const [revealed, setRevealed] = useState<ReadonlySet<string>>(new Set());

  const variables = query.data?.variables;
  const names = useMemo(() => new Set((variables ?? []).map((variable) => variable.name)), [variables]);

  // The scope's whole prefix: the list, and who uses each variable, are both stale after a change.
  const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: variableKeys.scope(scope) });

  const unmask = useMutation({
    mutationFn: () => api.list(true),
    onSuccess: (result) => {
      setClear(new Map(result.variables.map((variable) => [variable.name, variable.value])));
    },
  });

  /** The values in clear, read once and kept on this page only, never in the shared query cache. */
  const readClear = async (): Promise<ReadonlyMap<string, string> | null> => {
    if (clear.size > 0) return clear;
    try {
      const result = await unmask.mutateAsync();
      return new Map(result.variables.map((variable) => [variable.name, variable.value]));
    } catch (error: unknown) {
      reportActionError(t("variables.shared.readClearFailed"), error);
      return null;
    }
  };

  const valueOf = (variable: SharedVariable): string | null => {
    if (!isMasked(variable.value) && !variable.secrecy.secret) return variable.value;
    return clear.get(variable.name) ?? null;
  };

  const toggleReveal = async (variable: SharedVariable): Promise<void> => {
    if (revealed.has(variable.name)) {
      setRevealed((current) => {
        const next = new Set(current);
        next.delete(variable.name);
        return next;
      });
      return;
    }
    if (valueOf(variable) === null && (await readClear()) === null) return;
    setRevealed((current) => new Set(current).add(variable.name));
  };

  const edit = async (variable: SharedVariable): Promise<void> => {
    let value = valueOf(variable);
    if (value === null) {
      const values = await readClear();
      if (values === null) return;
      value = values.get(variable.name) ?? "";
    }
    setEditing({ mode: "edit", name: variable.name, value, secret: variable.secrecy.marked ? variable.secrecy.secret : null, audience: audienceOf(variable.audience) });
  };

  const save = async (draft: SharedVariableDraft): Promise<void> => {
    await api.set(draft.name, { value: draft.value, secret: draft.secret, audience: draft.audience });
    setClear(new Map());
    setRevealed(new Set());
    await refresh();
    setEditing(null);
    announce(t("variables.shared.saved", { name: draft.name }));
  };

  const columns: Column<SharedVariable>[] = [
    {
      id: "name",
      header: t("variables.shared.nameHeader"),
      width: "w-2/5",
      card: "title",
      sortValue: (variable) => variable.name,
      cell: (variable) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono truncate title={variable.name}>
            {variable.name}
          </Mono>
          {audienceOf(variable.audience) !== "both" ? <Badge>{audienceLabel(audienceOf(variable.audience), t.locale)}</Badge> : null}
          {variable.secrecy.secret ? <ICONS.locked aria-label={t("variables.shared.secretLabel")} role="img" className="size-icon-sm shrink-0 text-fg-muted" /> : null}
        </span>
      ),
    },
    {
      id: "value",
      header: t("variables.shared.valueHeader"),
      cell: (variable) => {
        const shown = revealed.has(variable.name) || (!isMasked(variable.value) && !variable.secrecy.secret);
        const value = shown ? valueOf(variable) : null;
        const hideable = variable.secrecy.secret || isMasked(variable.value);
        const reveal = hideable ? (
          <IconButton
            size="sm"
            label={revealed.has(variable.name) ? t("variables.shared.hide", { name: variable.name }) : t("variables.shared.reveal", { name: variable.name })}
            icon={revealed.has(variable.name) ? <EyeOff /> : <Eye />}
            disabled={unmask.isPending}
            onClick={() => void toggleReveal(variable)}
          />
        ) : null;
        if (value === null) {
          return (
            <span className="inline-flex max-w-full min-w-0 items-center gap-1 align-middle">
              <Masked />
              {reveal}
            </span>
          );
        }
        return (
          <span className="inline-flex max-w-full min-w-0 items-center gap-1 align-middle">
            <Mono truncate title={value} className="max-w-72">
              {value}
            </Mono>
            {reveal}
            <CopyButton value={value} label={t("variables.shared.copyValue", { name: variable.name })} />
          </span>
        );
      },
    },
    {
      id: "updated",
      header: t("variables.shared.updatedHeader"),
      width: "w-48",
      hideBelow: "md",
      sortValue: (variable) => variable.updated_at,
      cell: (variable) => (
        <span className="flex min-w-0 flex-col text-13">
          <RelativeTime value={variable.updated_at} className="text-fg" />
          {variable.updated_by ? <span className="truncate text-12 text-fg-muted">{t("variables.shared.changedBy", { actor: variable.updated_by })}</span> : null}
        </span>
      ),
    },
  ];

  const rowActions = (variable: SharedVariable) => (
    <Menu align="end" trigger={<IconButton size="sm" label={t("variables.shared.actions", { name: variable.name })} icon={<ICONS.more />} tooltip={false} />}>
      {canManage ? (
        <MenuItem icon={<Pencil />} disabled={unmask.isPending} onClick={() => void edit(variable)}>
          {t("variables.shared.edit")}
        </MenuItem>
      ) : null}
      {scope.kind === "preview" ? null : (
        <MenuItem icon={<Users />} onClick={() => setUsing(variable.name)}>
          {t("variables.shared.usage")}
        </MenuItem>
      )}
      {canManage ? (
        <>
          <MenuSeparator />
          <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(variable.name)}>
            {t("variables.shared.remove")}
          </MenuItem>
        </>
      ) : null}
    </Menu>
  );

  const addButton = canManage ? (
    <Button variant={primary ? "primary" : "secondary"} icon={<ICONS.add aria-hidden="true" />} onClick={() => setEditing({ mode: "add" })}>
      {t("variables.shared.add")}
    </Button>
  ) : null;

  // The first-use block carries its own action; the header's would be the same button twice.
  const firstUse = !inlineEmpty && query.data?.variables.length === 0;
  const caption = scope.kind === "preview" ? t("variables.shared.tableCaptionPreview", { scope: text.of }) : t("variables.shared.tableCaption", { scope: text.of });

  return (
    <Section title={text.title} description={text.description} level={level} {...(!primary && !firstUse && addButton !== null ? { actions: addButton } : {})}>
      {primary && addButton !== null ? <SettingsPrimaryAction>{addButton}</SettingsPrimaryAction> : null}
      <QueryState
        query={query}
        label={t("variables.shared.queryLabel")}
        skeleton={<DataTable caption={caption} columns={columns} rows={[]} getRowId={(variable) => variable.name} density="compact" mobile="cards" skeletonRows={3} loading />}
        isEmpty={(data) => data.variables.length === 0}
        empty={
          inlineEmpty ? (
            <EmptyState variant="inline" title={t("variables.shared.emptyInline")} />
          ) : (
            <EmptyState
              variant="firstUse"
              level={level === 2 ? 3 : 4}
              icon={<Variable />}
              title={t("variables.shared.emptyTitle")}
              description={t("variables.shared.emptyDescription")}
              {...(addButton !== null && !primary ? { action: addButton } : {})}
              {...(command ? { command: text.command } : {})}
            />
          )
        }
      >
        {(data) => (
          <>
            <DataTable
              caption={caption}
              columns={columns}
              rows={data.variables}
              getRowId={(variable) => variable.name}
              rowActions={rowActions}
              density="compact"
              mobile="cards"
              defaultSort={{ column: "name", direction: "ascending" }}
            />
            <p className="max-w-measure text-12 text-pretty text-fg-muted">{scope.kind === "preview" ? t("variables.shared.notePreview") : t("variables.shared.note")}</p>
          </>
        )}
      </QueryState>

      <SharedVariableDialog target={editing} existing={names} onClose={() => setEditing(null)} onSubmit={save} />
      <UsageDialog scope={scope} name={using} onClose={() => setUsing(null)} />
      <DeleteDialog
        scope={scope}
        name={deleting}
        of={text.of}
        server={node}
        onClose={() => setDeleting(null)}
        onDeleted={(removed) => {
          void refresh();
          announce(t("variables.shared.deleted", { name: removed }));
        }}
      />
    </Section>
  );
}

/** Deleting asks once, and says which applications still refer to the variable. */
function DeleteDialog({
  scope,
  name,
  of,
  server,
  onClose,
  onDeleted,
}: {
  scope: VariableScope;
  name: string | null;
  of: string;
  server: string | null;
  onClose: () => void;
  onDeleted: (name: string) => void;
}) {
  const t = useT();
  const usage = useQuery({ ...variableUsageQuery(scope, name ?? ""), enabled: name !== null && scope.kind !== "preview" });
  const users = usage.data?.applications ?? [];
  const label = name ?? "";
  return (
    <ConfirmDialog
      open={name !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      friction="simple"
      server={server}
      title={t("variables.shared.delete.title", { name: label })}
      description={
        scope.kind === "preview"
          ? t("variables.shared.delete.descriptionPreview", { scope: of })
          : users.length === 0
            ? t("variables.shared.delete.description", { scope: of })
            : t("variables.shared.delete.descriptionUsed", { count: users.length, scope: of, list: users.join(", ") })
      }
      actionLabel={t("variables.shared.delete.action")}
      destructive
      onConfirm={async () => {
        if (name === null) return;
        await variablesApi(scope).remove(name);
        onDeleted(name);
      }}
    />
  );
}
