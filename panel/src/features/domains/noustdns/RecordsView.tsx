import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { noustDnsKeys, RECORD_TYPES } from "../../../api/queries/noustDns";
import type { NoustRecord, NoustZone, RecordType } from "../../../api/queries/noustDns";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RowMenu } from "../../../components/page/RowMenu";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import type { Column } from "../../../components/ui/DataTable";
import { DataTable } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { VERB_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";

/** Makes the lists the zone page reads fresh after a change of records: the zone, and the zones' record counts. */
function useRefreshZone(zone: string): () => void {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zone(zone) });
    void queryClient.invalidateQueries({ queryKey: noustDnsKeys.zones });
  };
}

/**
 * Adds a record, or edits the value and the TTL of one (its name and type are what it is: to change
 * them, delete it and add another). The server checks the whole zone before it serves it: a refusal
 * names the field when it is one, and is the name server's own words, verbatim, when it is Knot's.
 */
export function RecordDialog({ zone, record, open, onOpenChange }: { zone: string; record: NoustRecord | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const refresh = useRefreshZone(zone);
  const formId = useId();
  const editing = record !== null;
  const [type, setType] = useState<RecordType>("A");
  const [name, setName] = useState("");
  const [content, setContent] = useState<string | null>(null);
  const [ttl, setTtl] = useState<string | null>(null);
  const [zoneTtl, setZoneTtl] = useState(false);
  const [problem, setProblem] = useState<{ field: string; text: string } | null>(null);
  const shownContent = content ?? record?.content ?? "";
  const shownTtl = ttl ?? (record?.ttl != null ? String(record.ttl) : "");
  const shownType = (editing ? (record.type as RecordType) : type) satisfies RecordType;

  const save = useMutation({
    mutationFn: () => {
      const seconds = shownTtl.trim() === "" ? null : Number(shownTtl);
      const id = record?.id;
      if (id !== null && id !== undefined) {
        return request("patch", "/api/dns/noust/zones/{zone}/records/{record_id}", {
          params: { zone, record_id: id },
          body: { content: shownContent.trim(), default_ttl: zoneTtl, ...(!zoneTtl && seconds !== null ? { ttl: seconds } : {}) },
        });
      }
      return request("post", "/api/dns/noust/zones/{zone}/records", {
        params: { zone },
        body: { name: name.trim() === "" ? "@" : name.trim().toLowerCase(), type, content: shownContent.trim(), ...(seconds !== null ? { ttl: seconds } : {}) },
      });
    },
    onSuccess: () => {
      refresh();
      close(true);
    },
  });

  function close(settled = false): void {
    if (save.isPending && !settled) return;
    onOpenChange(false);
    setType("A");
    setName("");
    setContent(null);
    setTtl(null);
    setZoneTtl(false);
    setProblem(null);
    save.reset();
  }

  const fields = save.error && isApiError(save.error) ? save.error.fields : null;
  const fieldError = (field: string): string | null => (problem?.field === field ? problem.text : (fields?.[field] ?? null));
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (shownContent.trim() === "") {
      setProblem({ field: "content", text: t("noustDns.fieldRequired") });
      return;
    }
    if (shownTtl.trim() !== "" && !/^\d+$/.test(shownTtl.trim())) {
      setProblem({ field: "ttl", text: t("noustDns.recordDialog.ttlHelp") });
      return;
    }
    setProblem(null);
    save.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={editing ? t("noustDns.recordDialog.editTitle", { name: record.name, type: record.type }) : t("noustDns.recordDialog.addTitle")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {editing ? t("common.words.save") : t("noustDns.recordDialog.addAction")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        {editing ? null : (
          <>
            <Field label={t("noustDns.recordDialog.typeLabel")} nativeLabel={false}>
              <Select aria-label={t("noustDns.recordDialog.typeLabel")} value={type} onValueChange={setType} options={RECORD_TYPES.map((entry) => ({ value: entry, label: entry }))} />
            </Field>
            <Field label={t("common.words.name")} description={t("noustDns.recordDialog.nameHelp")} optional error={fieldError("name")}>
              <Input mono value={name} onValueChange={setName} placeholder="www" autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
          </>
        )}
        <Field label={t("noustDns.recordDialog.contentLabel")} description={t(`noustDns.recordDialog.valueHelp.${shownType}`)} error={fieldError("content")}>
          <Input mono value={shownContent} onValueChange={setContent} autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("noustDns.recordDialog.ttlLabel")} description={t("noustDns.recordDialog.ttlHelp")} optional error={fieldError("ttl")}>
          <Input mono inputMode="numeric" value={shownTtl} onValueChange={setTtl} disabled={zoneTtl} autoComplete="off" />
        </Field>
        {editing && record.ttl !== null && record.ttl !== undefined ? <Checkbox label={t("noustDns.recordDialog.ttlDefault")} checked={zoneTtl} onCheckedChange={setZoneTtl} /> : null}
        {save.isError && fields === null ? <ErrorBlock live compact error={save.error} title={editing ? t("noustDns.recordDialog.saveFailed") : t("noustDns.recordDialog.addFailed")} /> : null}
      </form>
    </Dialog>
  );
}

/** The zone's records as a table: the name, the type, the value and the TTL, with edit and delete in a menu. */
export function RecordsView({ zone, editable, onAdd }: { zone: NoustZone; editable: boolean; onAdd: () => void }) {
  const t = useT();
  const { node } = useNode();
  const refresh = useRefreshZone(zone.name);
  const [editing, setEditing] = useState<NoustRecord | null>(null);
  const [deleting, setDeleting] = useState<NoustRecord | null>(null);

  const columns: Column<NoustRecord>[] = [
    { id: "name", header: t("common.words.name"), cell: (record) => <Mono>{record.name}</Mono>, sortValue: (record) => record.name },
    { id: "type", header: t("noustDns.records.typeColumn"), width: "w-24", card: "status", cell: (record) => <Badge>{record.type}</Badge>, sortValue: (record) => record.type },
    { id: "content", header: t("noustDns.records.contentColumn"), card: "meta", cell: (record) => <Mono truncate>{record.content}</Mono> },
    {
      id: "ttl",
      header: t("noustDns.records.ttlColumn"),
      align: "end",
      width: "w-28",
      hideBelow: "sm",
      cell: (record) => (record.ttl === null || record.ttl === undefined ? <EmptyCell reason={t("noustDns.records.ttlDefault")} /> : <Mono className="tabular-nums">{record.ttl}</Mono>),
    },
  ];

  return (
    <div className="flex flex-col gap-4">
      <DataTable
        caption={t("noustDns.records.caption", { zone: zone.name })}
        columns={columns}
        rows={zone.records}
        getRowId={(record) => String(record.id ?? `${record.name}/${record.type}/${record.content}`)}
        {...(editable ? { onRowActivate: setEditing } : {})}
        empty={
          <EmptyState
            variant="inline"
            title={t("noustDns.records.emptyTitle")}
            description={t("noustDns.records.emptyDescription")}
            {...(editable
              ? {
                  action: (
                    <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={onAdd}>
                      {t("noustDns.records.add")}
                    </Button>
                  ),
                }
              : {})}
          />
        }
        {...(editable
          ? {
              rowActions: (record: NoustRecord) => (
                <RowMenu name={`${record.name} ${record.type}`}>
                  <MenuItem icon={<VERB_ICONS.edit />} onClick={() => setEditing(record)}>
                    {t("common.words.edit")}
                  </MenuItem>
                  <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(record)}>
                    {t("common.words.delete")}
                  </MenuItem>
                </RowMenu>
              ),
            }
          : {})}
      />
      <RecordDialog key={editing?.id ?? "none"} zone={zone.name} record={editing} open={editing !== null} onOpenChange={(open) => !open && setEditing(null)} />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(next) => {
          if (!next) setDeleting(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("noustDns.deleteRecord.title", { name: deleting?.name ?? "", type: deleting?.type ?? "" })}
        description={t("noustDns.deleteRecord.description")}
        actionLabel={t("noustDns.deleteRecord.action")}
        onConfirm={async () => {
          const id = deleting?.id;
          if (id === null || id === undefined) return;
          await request("delete", "/api/dns/noust/zones/{zone}/records/{record_id}", { params: { zone: zone.name, record_id: id } });
          refresh();
        }}
      />
    </div>
  );
}
