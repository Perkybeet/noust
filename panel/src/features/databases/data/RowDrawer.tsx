import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { databaseKeys } from "../../../api/queries/databases";
import type { RelationColumn, RowChange, TableRow } from "../../../api/queries/databases";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { Cell, cellText } from "../grid/Cell";

export type RowDrawerMode = "view" | "edit" | "insert" | "delete";

interface Draft {
  value: string;
  isNull: boolean;
  touched: boolean;
}

export interface RowDrawerProps {
  engine: string;
  name: string;
  schema: string;
  relation: string;
  columns: readonly RelationColumn[];
  editable: boolean;
  mode: RowDrawerMode;
  row: TableRow | null;
  onModeChange: (mode: RowDrawerMode) => void;
  onClose: () => void;
}

/** A row's key as the operator reads it and types it to delete it: `id = 1024`. */
export function keyText(row: TableRow): string {
  return Object.entries(row.key ?? {})
    .map(([column, value]) => `${column} = ${value}`)
    .join(", ");
}

function initialDrafts(columns: readonly RelationColumn[], row: TableRow | null): Record<string, Draft> {
  return Object.fromEntries(
    columns.map((column, index) => {
      const value: unknown = row?.cells[index];
      const isNull = row !== null && (value === null || value === undefined);
      return [column.name, { value: row === null || isNull ? "" : cellText(value), isNull, touched: false }];
    }),
  );
}

/** What a value becomes in the request: NULL, a boolean, or the text the engine casts. */
function wire(column: RelationColumn, draft: Draft): unknown {
  if (draft.isNull) return null;
  if (column.kind === "boolean") return draft.value === "true";
  return draft.value;
}

/** The engine's rendering of a row, as column to value, whatever shape it came in. */
function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

/** Before and after, only where they differ (every column for an insert). */
function ChangeView({ change, t }: { change: RowChange; t: T }) {
  const before = asRecord(change.before);
  const after = asRecord(change.after);
  const names = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  const changed = change.action === "update" ? names.filter((column) => JSON.stringify(before[column]) !== JSON.stringify(after[column])) : names;
  return (
    <div className="flex flex-col gap-3">
      <Notice tone="success" live title={t(change.action === "insert" ? "databases.row.inserted" : change.action === "delete" ? "databases.row.deleted" : "databases.row.updated")}>
        {t("databases.row.auditNote")}
      </Notice>
      <dl className="flex flex-col divide-y divide-border rounded-card border border-border">
        {changed.map((column) => (
          <div key={column} className="flex min-w-0 flex-col gap-1 px-3 py-2">
            <dt>
              <Mono tone="default">{column}</Mono>
            </dt>
            <dd className="flex min-w-0 flex-col gap-0.5 text-12">
              {change.action !== "insert" ? (
                <span className="flex min-w-0 items-start gap-2">
                  <span className="w-12 shrink-0 text-fg-faint">{t("databases.row.before")}</span>
                  <Cell value={before[column]} inline={false} />
                </span>
              ) : null}
              {change.action !== "delete" ? (
                <span className="flex min-w-0 items-start gap-2">
                  <span className="w-12 shrink-0 text-fg-faint">{t("databases.row.after")}</span>
                  <Cell value={after[column]} inline={false} />
                </span>
              ) : null}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * One row, with the page behind it: every value in full; and, for a table with a primary key,
 * its editor. Only what changed is sent, for exactly that row (its whole key), in sudo mode;
 * the engine's own before and after come back and are shown. A value the browser only has cut
 * cannot be edited here, since saving would write the cut text over the real one.
 */
export function RowDrawer({ engine, name, schema, relation, columns, editable, mode, row, onModeChange, onClose }: RowDrawerProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, Draft>>(() => initialDrafts(columns, mode === "insert" ? null : row));
  const [result, setResult] = useState<RowChange | null>(null);
  const cut = new Set(row?.truncated ?? []);

  const settle = (change: RowChange): void => {
    setResult(change);
    void queryClient.invalidateQueries({ queryKey: [...databaseKeys.database(engine, name), "rows"] });
  };

  const save = useMutation({
    mutationFn: async () => {
      const values: Record<string, unknown> = {};
      columns.forEach((column) => {
        const draft = drafts[column.name];
        if (!draft?.touched) return;
        values[column.name] = wire(column, draft);
      });
      if (mode === "insert") {
        return request("post", "/api/databases/databases/{engine}/{name}/rows", { params: { engine, name }, body: { schema, relation, values } });
      }
      return request("patch", "/api/databases/databases/{engine}/{name}/rows", { params: { engine, name }, body: { schema, relation, key: row?.key ?? {}, values } });
    },
    onSuccess: settle,
  });

  const update = (column: string, patch: Partial<Draft>): void => {
    setDrafts((previous) => ({ ...previous, [column]: { ...(previous[column] ?? { value: "", isNull: false, touched: false }), ...patch, touched: true } }));
  };

  const changes = Object.values(drafts).filter((draft) => draft.touched).length;
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (changes > 0 || mode === "insert") save.mutate();
  };

  if (mode === "delete" && row !== null) {
    const typed = Object.values(row.key ?? {}).join(", ");
    return (
      <ConfirmDialog
        open
        onOpenChange={(open) => (open ? undefined : onClose())}
        title={t("databases.row.deleteTitle", { name: `${schema}.${relation}` })}
        description={t("databases.row.deleteDescription", { key: keyText(row), name: `${schema}.${relation}` })}
        actionLabel={t("databases.row.deleteAction")}
        confirmText={typed}
        server={node}
        onConfirm={async () => {
          const change = await request("post", "/api/databases/databases/{engine}/{name}/rows/delete", {
            params: { engine, name },
            body: { schema, relation, key: row.key ?? {} },
          });
          settle(change);
        }}
      />
    );
  }

  const formId = "row-editor-form";
  const editing = (mode === "edit" || mode === "insert") && result === null;
  const title =
    mode === "insert" ? t("databases.row.insertTitle", { name: `${schema}.${relation}` }) : mode === "edit" ? t("databases.row.editTitle") : t("databases.row.viewTitle");

  return (
    <Drawer
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      title={title}
      description={row !== null && Object.keys(row.key ?? {}).length > 0 ? <Mono>{keyText(row)}</Mono> : <Mono>{`${schema}.${relation}`}</Mono>}
      footer={
        result !== null ? (
          <Button variant="primary" onClick={onClose}>
            {t("databases.row.done")}
          </Button>
        ) : editing ? (
          <>
            <Button onClick={() => (mode === "edit" ? onModeChange("view") : onClose())}>{t("databases.common.cancel")}</Button>
            <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={mode === "edit" && changes === 0}>
              {mode === "insert" ? t("databases.row.insertAction") : t("databases.row.saveAction")}
            </Button>
          </>
        ) : (
          <>
            <Button onClick={onClose}>{t("databases.row.close")}</Button>
            {editable ? (
              <Button variant="primary" onClick={() => onModeChange("edit")}>
                {t("databases.row.edit")}
              </Button>
            ) : null}
          </>
        )
      }
    >
      {result !== null ? (
        <ChangeView change={result} t={t} />
      ) : editing ? (
        <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
          <p className="text-13 text-fg-muted">{mode === "insert" ? t("databases.row.insertIntro") : t("databases.row.editIntro")}</p>
          {columns.map((column, index) => {
            const draft = drafts[column.name] ?? { value: "", isNull: false, touched: false };
            const label = <Mono tone="default">{column.name}</Mono>;
            const help = column.type + (column.primary_key != null ? ` · ${t("databases.row.keyPart")}` : "");
            if (column.kind === "binary" || (mode === "edit" && cut.has(index))) {
              return (
                <Field key={column.name} label={label} description={column.kind === "binary" ? t("databases.row.binaryReadOnly") : t("databases.row.cutReadOnly")} disabled>
                  <Input mono disabled value={row !== null ? cellText(row.cells[index]) : ""} />
                </Field>
              );
            }
            const nullToggle = column.nullable ? (
              <Checkbox checked={draft.isNull} onCheckedChange={(checked) => update(column.name, { isNull: checked })} label="NULL" />
            ) : undefined;
            const placeholder = mode === "insert" && (column.generated || column.default != null) ? t("databases.row.defaultPlaceholder") : undefined;
            return (
              <Field key={column.name} label={label} description={help} optional={mode === "insert" && (column.generated || column.default != null || column.nullable)} {...(nullToggle !== undefined ? { action: nullToggle } : {})}>
                {column.kind === "boolean" ? (
                  <Select
                    value={draft.value === "" ? null : draft.value}
                    placeholder={placeholder ?? t("databases.row.chooseBoolean")}
                    disabled={draft.isNull}
                    onValueChange={(value) => update(column.name, { value, isNull: false })}
                    options={[
                      { value: "true", label: "true" },
                      { value: "false", label: "false" },
                    ]}
                    mono
                  />
                ) : column.kind === "json" || draft.value.length > 80 ? (
                  <Textarea mono rows={4} value={draft.isNull ? "" : draft.value} disabled={draft.isNull} placeholder={placeholder} onChange={(event) => update(column.name, { value: event.target.value })} />
                ) : (
                  <Input
                    mono
                    value={draft.isNull ? "" : draft.value}
                    disabled={draft.isNull}
                    placeholder={placeholder}
                    onValueChange={(value: string) => update(column.name, { value })}
                    autoComplete="off"
                    spellCheck={false}
                  />
                )}
              </Field>
            );
          })}
          {save.isError ? <ErrorBlock live compact error={save.error} title={mode === "insert" ? t("databases.row.insertFailed") : t("databases.row.saveFailed")} /> : null}
        </form>
      ) : row !== null ? (
        <dl className="flex flex-col divide-y divide-border">
          {columns.map((column, index) => (
            <div key={column.name} className="flex min-w-0 flex-col gap-1 py-2.5 first:pt-0">
              <dt className="flex items-baseline gap-2 text-12">
                <Mono tone="default">{column.name}</Mono>
                <Mono tone="faint">{column.type}</Mono>
              </dt>
              <dd className="min-w-0 text-13">
                <Cell value={row.cells[index]} kind={column.kind} truncated={cut.has(index)} inline={false} />
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </Drawer>
  );
}
