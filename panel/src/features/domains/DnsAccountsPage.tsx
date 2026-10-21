import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RotateCw, Settings2, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { dnsAccountsQuery, managedRecordsQuery, networkKeys } from "../../api/queries/network";
import type { DnsAccount, DnsKind } from "../../api/queries/network";
import { MenuItem } from "../../components/ui/Menu";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { RowMenu } from "../../components/page/RowMenu";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { Select } from "../../components/ui/Select";
import { StatusPill } from "../../components/ui/StatusPill";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { DomainsListPage } from "./DomainsListPage";

/** An account's name, as the server takes it: lower case letters, digits and dashes. */
const NAME = /^[a-z0-9][a-z0-9-]*$/;

function AddAccountDialog({ open, kinds, onClose }: { open: boolean; kinds: readonly DnsKind[]; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [name, setName] = useState("");
  const [kindName, setKindName] = useState("cloudflare");
  const [values, setValues] = useState<Record<string, string>>({});
  const [automatic, setAutomatic] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const kind = kinds.find((candidate) => candidate.kind === kindName) ?? kinds[0];

  const add = useMutation({
    mutationFn: () =>
      request("post", "/api/dns/providers", {
        body: { name, kind: kindName, credentials: values, automatic: automatic && kind?.native === true },
      }),
    onSuccess: (account) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.dnsAccounts });
      toast.success(t("network.dns.addedToast", { name: account.name }));
      close(true);
    },
  });

  function close(settled = false): void {
    if (add.isPending && !settled) return;
    onClose();
    setName("");
    setValues({});
    setAutomatic(false);
    setProblem(null);
    add.reset();
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!NAME.test(name)) {
      setProblem(t("network.dns.nameInvalid"));
      return;
    }
    setProblem(null);
    add.mutate();
  };

  const fields = add.error && isApiError(add.error) ? add.error.fields : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("network.dns.addTitle")}
      description={t("network.dns.addDescription")}
      footer={
        <>
          <Button disabled={add.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={add.isPending}>
            {t("network.dns.addAction")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("network.dns.nameLabel")} description={t("network.dns.nameHelp")} error={problem ?? fields?.["name"] ?? null}>
          <Input mono value={name} onValueChange={setName} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={40} />
        </Field>
        <Field label={t("network.dns.kindLabel")} nativeLabel={false} description={kind?.help}>
          <Select
            aria-label={t("network.dns.kindLabel")}
            value={kindName}
            onValueChange={(next) => {
              setKindName(next);
              setValues({});
            }}
            options={kinds.map((candidate) => ({ value: candidate.kind, label: candidate.label }))}
          />
        </Field>
        {(kind?.fields ?? []).map((field) => (
          <Field key={field.name} label={field.label} optional={!field.required} error={fields?.[field.name] ?? null}>
            <Input
              mono={!field.secret}
              type={field.secret ? "password" : "text"}
              value={values[field.name] ?? field.default}
              onValueChange={(next: string) => setValues((previous) => ({ ...previous, [field.name]: next }))}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
        ))}
        {kind !== undefined && !kind.native && kind.package ? <p className="text-13 text-fg-muted">{t.rich("network.dns.needsPackage", { package: <Mono>{kind.package}</Mono> })}</p> : null}
        {kind?.native === true && !kind.comments ? <p className="text-13 text-fg-muted">{t("network.dns.noNotes")}</p> : null}
        {kind?.native === true ? <Checkbox label={t("network.dns.automaticLabel")} description={t("network.dns.automaticHelp")} checked={automatic} onCheckedChange={setAutomatic} /> : null}
        {add.isError && fields === null ? <ErrorBlock live compact error={add.error} title={t("network.dns.addFailed")} /> : null}
      </form>
    </Dialog>
  );
}

function SettingsDialog({ account, onClose }: { account: DnsAccount | null; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [automatic, setAutomatic] = useState<boolean | null>(null);
  const [seconds, setSeconds] = useState<string | null>(null);
  const shownAutomatic = automatic ?? account?.automatic ?? false;
  const shownSeconds = seconds ?? (account?.propagation_seconds != null ? String(account.propagation_seconds) : "");

  const save = useMutation({
    mutationFn: () => {
      const wait = shownSeconds.trim();
      return request("patch", "/api/dns/providers/{name}", {
        params: { name: account?.name ?? "" },
        body: { automatic: shownAutomatic, ...(wait === "" ? {} : { propagation_seconds: Number(wait) }) },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.dnsAccounts });
      toast.success(t("network.dns.savedToast", { name: account?.name ?? "" }));
      close(true);
    },
  });

  function close(settled = false): void {
    if (save.isPending && !settled) return;
    onClose();
    setAutomatic(null);
    setSeconds(null);
    save.reset();
  }

  const fields = save.error && isApiError(save.error) ? save.error.fields : null;

  return (
    <Dialog
      open={account !== null}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("network.dns.settingsTitle", { name: account?.name ?? "" })}
      description={t("network.dns.settingsDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("network.dns.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
        noValidate
        className="flex flex-col gap-4"
      >
        {account?.native === true ? (
          <Checkbox label={t("network.dns.automaticLabel")} description={t("network.dns.automaticHelp")} checked={shownAutomatic} onCheckedChange={setAutomatic} />
        ) : null}
        <Field label={t("network.dns.propagationLabel")} description={t("network.dns.propagationHelp")} optional error={fields?.["propagation_seconds"] ?? null}>
          <Input mono inputMode="numeric" value={shownSeconds} onValueChange={setSeconds} autoComplete="off" />
        </Field>
        {save.isError && fields === null ? <ErrorBlock live compact error={save.error} title={t("network.dns.saveFailed", { name: account?.name ?? "" })} /> : null}
      </form>
    </Dialog>
  );
}

function RecordsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const records = useQuery({ ...managedRecordsQuery(), enabled: open });
  const sweep = useMutation({
    mutationFn: () => request("post", "/api/dns/sweep"),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.dnsRecords });
      toast.success(t("network.dns.sweptToast", { count: result.released.length }));
    },
    onError: (error) => {
      reportActionError(t("network.dns.sweepFailed"), error);
    },
  });
  return (
    <Drawer
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={t("network.dns.recordsTitle")}
      description={t("network.dns.recordsDescription")}
      footer={
        <Button icon={<RotateCw aria-hidden="true" />} loading={sweep.isPending} onClick={() => sweep.mutate()}>
          {t("network.dns.sweep")}
        </Button>
      }
    >
      {records.isError ? (
        <ErrorBlock compact error={records.error} title={t("network.dns.recordsFailed")} onRetry={() => void records.refetch()} retrying={records.isRefetching} />
      ) : records.data === undefined ? null : records.data.records.length === 0 ? (
        <EmptyState variant="inline" title={t("network.dns.recordsEmpty")} />
      ) : (
        <RowList aria-label={t("network.dns.recordsTitle")}>
          {records.data.records.map((record) => (
            <RowList.Item key={record.id} className="items-start">
              <div className="flex min-w-0 flex-col gap-1">
                <Mono truncate>{`${record.type} ${record.hostname}`}</Mono>
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-12 text-fg-muted">
                  <Mono truncate>{record.content}</Mono>
                  <Badge>{record.owned ? t("network.appDns.madeByNoust") : t("network.appDns.adopted")}</Badge>
                  <span>{t("network.dns.recordIn", { account: record.provider })}</span>
                </span>
                <span className="text-12 text-fg-muted">
                  {(record.applications ?? []).length > 0 ? t("network.dns.usedBy", { names: (record.applications ?? []).join(", ") }) : t("network.dns.usedByNobody")}
                </span>
              </div>
            </RowList.Item>
          ))}
        </RowList>
      )}
    </Drawer>
  );
}

/**
 * The DNS tab: the accounts Noust holds at DNS providers. A Cloudflare account makes the
 * records of a domain and answers the challenge a wildcard certificate is proven with; the other
 * providers answer the challenge through certbot's plugin. The credential is proven when the
 * account is added and kept only in the secret store; it is never shown again.
 */
export function DnsAccountsPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  const accounts = useQuery(dnsAccountsQuery());
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<DnsAccount | null>(null);
  const [removing, setRemoving] = useState<DnsAccount | null>(null);
  const [showRecords, setShowRecords] = useState(false);

  const verify = useMutation({
    mutationFn: (account: DnsAccount) => request("post", "/api/dns/providers/{name}/verify", { params: { name: account.name } }),
    onSuccess: (zones, account) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.dnsAccounts });
      toast.success(t("network.dns.verifiedToast", { name: account.name, count: zones.length }));
    },
    onError: (error, account) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.dnsAccounts });
      reportActionError(t("network.dns.verifyFailed", { name: account.name }), error);
    },
  });

  const columns: Column<DnsAccount>[] = [
    { id: "name", header: t("network.dns.accountColumn"), cell: (account) => <span translate="no">{account.name}</span>, sortValue: (account) => account.name },
    { id: "kind", header: t("network.dns.providerColumn"), hideBelow: "sm", cell: (account) => account.label },
    {
      id: "records",
      header: t("network.dns.recordsColumn"),
      hideBelow: "md",
      cell: (account) => (account.native ? (account.automatic ? t("network.dns.automatic") : t("network.dns.asksFirst")) : t("network.dns.challengeOnly")),
    },
    {
      id: "state",
      header: t("network.dns.workedColumn"),
      card: "status",
      cell: (account) =>
        account.last_error ? (
          <StatusPill state="failed" label={t("network.dns.failing")} appearance="inline" size="sm" />
        ) : account.verified_at ? (
          <span className="inline-flex items-center gap-1.5">
            <StatusPill state="running" label={t("network.dns.works")} appearance="inline" size="sm" />
            <RelativeTime value={account.verified_at} className="text-fg-muted" />
          </span>
        ) : (
          <StatusPill state="unknown" label={t("network.dns.notChecked")} appearance="inline" size="sm" />
        ),
    },
  ];

  const empty = accounts.data?.accounts.length === 0;

  return (
    <DomainsListPage
      primaryAction={
        <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setAdding(true)}>
          {t("network.dns.add")}
        </Button>
      }
      {...(empty ? {} : { secondaryActions: <Button onClick={() => setShowRecords(true)}>{t("network.dns.managed")}</Button> })}
      footer={<CommandHint command="noust dns provider list" label={t("domains.fromTerminal")} />}
    >
      {/* design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed */}
      {accounts.isError && accounts.data === undefined ? (
        <ErrorBlock error={accounts.error} title={t("network.dns.listFailed")} onRetry={() => void accounts.refetch()} retrying={accounts.isRefetching} />
      ) : empty ? (
        <EmptyState
          variant="firstUse"
          icon={<Settings2 />}
          title={t("network.dns.emptyTitle")}
          description={t("network.dns.emptyDescription")}
          action={
            <Button icon={<Plus aria-hidden="true" />} onClick={() => setAdding(true)}>
              {t("network.dns.add")}
            </Button>
          }
          command="noust dns provider add cloudflare --kind cloudflare --token-stdin"
        />
      ) : (
        <DataTable
          mobile="cards"
          caption={t("network.dns.tableCaption")}
          columns={columns}
          rows={accounts.data?.accounts ?? []}
          getRowId={(account) => account.name}
          loading={accounts.isPending}
          rowActions={(account) => (
            <RowMenu name={account.name}>
              <MenuItem icon={<RotateCw />} disabled={verify.isPending} onClick={() => verify.mutate(account)}>
                {t("network.dns.verify")}
              </MenuItem>
              <MenuItem icon={<Settings2 />} onClick={() => setEditing(account)}>
                {t("network.dns.settings")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem icon={<Trash2 />} destructive onClick={() => setRemoving(account)}>
                {t("network.dns.remove")}
              </MenuItem>
            </RowMenu>
          )}
        />
      )}
      <AddAccountDialog open={adding} kinds={accounts.data?.kinds ?? []} onClose={() => setAdding(false)} />
      <SettingsDialog key={editing?.name ?? "none"} account={editing} onClose={() => setEditing(null)} />
      <RecordsDrawer open={showRecords} onClose={() => setShowRecords(false)} />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.dns.removeTitle", { name: removing?.name ?? "" })}
        description={t("network.dns.removeDescription")}
        actionLabel={t("network.dns.remove")}
        onConfirm={async () => {
          if (removing === null) return;
          await request("delete", "/api/dns/providers/{name}", { params: { name: removing.name } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.dnsAccounts });
          toast.success(t("network.dns.removedToast", { name: removing.name }));
        }}
      />
    </DomainsListPage>
  );
}
