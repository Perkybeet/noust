import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Radio } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { networkKeys, streamsQuery } from "../../api/queries/network";
import type { StreamBody, StreamExposure, StreamPlan } from "../../api/queries/network";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { listOf } from "../app/settings/access";
import { DomainsListPage } from "./DomainsListPage";
import { ICONS } from "../../components/ui/icons";

const NAME = /^[a-z0-9][a-z0-9-]{0,39}$/;
const WHOLE = /^\d+$/;

type Protocol = "tcp" | "udp";

interface Draft {
  name: string;
  protocol: Protocol;
  listen: string;
  host: string;
  target: string;
  sources: string;
}

const EMPTY: Draft = { name: "", protocol: "tcp", listen: "", host: "127.0.0.1", target: "", sources: "" };

/** The request a draft asks for, or the field that is not acceptable and why. */
function bodyOf(draft: Draft): { body: StreamBody } | { field: keyof Draft; message: "name" | "listen" | "target" } {
  if (!NAME.test(draft.name)) return { field: "name", message: "name" };
  const listen = Number(draft.listen);
  if (!WHOLE.test(draft.listen) || listen < 1 || listen > 65535) return { field: "listen", message: "listen" };
  const target = Number(draft.target);
  if (!WHOLE.test(draft.target) || target < 1 || target > 65535) return { field: "target", message: "target" };
  return {
    body: {
      name: draft.name,
      protocol: draft.protocol,
      listen_port: listen,
      target_port: target,
      target_host: draft.host.trim() || "127.0.0.1",
      sources: listOf(draft.sources),
      timeout_seconds: 3600,
    },
  };
}

function PublishDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [problem, setProblem] = useState<{ field: keyof Draft; text: string } | null>(null);
  const [plan, setPlan] = useState<{ asked: string; plan: StreamPlan } | null>(null);

  const planning = useMutation({
    mutationFn: (body: StreamBody) => request("post", "/api/streams/plan", { body }),
    onSuccess: (answer, body) => setPlan({ asked: JSON.stringify(body), plan: answer }),
  });
  const publish = useMutation({
    mutationFn: (body: StreamBody) => request("post", "/api/streams", { body }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.streams });
      toast.success(t("network.ports.publishedToast", { name: result.exposure.name }), { description: result.firewall });
      close(true);
    },
  });

  function close(settled = false): void {
    if (publish.isPending && !settled) return;
    onClose();
    setDraft(EMPTY);
    setProblem(null);
    setPlan(null);
    planning.reset();
    publish.reset();
  }

  const edit = <K extends keyof Draft>(field: K, value: Draft[K]): void => {
    setDraft((previous) => ({ ...previous, [field]: value }));
    setPlan(null);
    setProblem(null);
    planning.reset();
    publish.reset();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const parsed = bodyOf(draft);
    if ("field" in parsed) {
      setProblem({ field: parsed.field, text: t(`network.ports.invalid.${parsed.message}`) });
      return;
    }
    // The first press asks what it would do; the second, on the same request, does it.
    if (plan !== null && plan.asked === JSON.stringify(parsed.body)) publish.mutate(parsed.body);
    else planning.mutate(parsed.body);
  };

  const refused = (planning.error ?? publish.error) as unknown;
  const fields = isApiError(refused) ? refused.fields : null;
  const fieldError = (field: keyof Draft, server: string): string | null => (problem?.field === field ? problem.text : (fields?.[server] ?? null));
  const ready = plan !== null && plan.asked === JSON.stringify((bodyOf(draft) as { body?: StreamBody }).body ?? null);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("network.ports.publishTitle")}
      description={t("network.ports.publishDescription")}
      size="lg"
      footer={
        <>
          <Button disabled={publish.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={planning.isPending || publish.isPending}>
            {ready ? t("network.ports.publish") : t("network.ports.check")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={t("common.words.name")} error={fieldError("name", "name")}>
            <Input mono value={draft.name} onValueChange={(value: string) => edit("name", value)} autoComplete="off" spellCheck={false} maxLength={40} />
          </Field>
          <Field label={t("network.ports.protocol")} nativeLabel={false}>
            <Select
              aria-label={t("network.ports.protocol")}
              value={draft.protocol}
              onValueChange={(value) => edit("protocol", value)}
              options={[
                { value: "tcp", label: "TCP" },
                { value: "udp", label: "UDP" },
              ]}
            />
          </Field>
          <Field label={t("network.ports.listen")} description={t("network.ports.listenHelp")} error={fieldError("listen", "listen_port")}>
            <Input mono inputMode="numeric" value={draft.listen} onValueChange={(value: string) => edit("listen", value)} autoComplete="off" />
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("network.ports.host")} description={t("network.ports.hostHelp")} error={fields?.["target_host"] ?? null}>
            <Input mono value={draft.host} onValueChange={(value: string) => edit("host", value)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label={t("network.ports.target")} error={fieldError("target", "target_port")}>
            <Input mono inputMode="numeric" value={draft.target} onValueChange={(value: string) => edit("target", value)} autoComplete="off" />
          </Field>
        </div>
        <Field label={t("network.ports.sources")} description={t("network.ports.sourcesHelp")} optional error={fields?.["sources"] ?? null}>
          <Textarea mono rows={3} wrap="off" value={draft.sources} onChange={(event) => edit("sources", event.target.value)} autoComplete="off" spellCheck={false} />
        </Field>

        {plan !== null ? (
          <div aria-live="polite" className="flex flex-col gap-3 rounded-card border border-border p-3">
            <p className="text-13 font-medium text-fg">{t("network.ports.planTitle")}</p>
            <KeyValueList
              items={[
                { label: t("network.ports.planFile"), value: plan.plan.file },
                {
                  label: t("network.ports.planFirewall"),
                  value: plan.plan.firewall.map((rule) => `${String(rule["proto"])} ${String(rule["port"])} <- ${String(rule["source"])}`).join(", "),
                  copy: false,
                },
              ]}
            />
            {plan.plan.loader === "write" ? <p className="text-13 text-fg-muted">{t("network.ports.planLoader")}</p> : null}
            {plan.plan.warnings.map((warning) => (
              <Notice key={warning} tone="warning">
                {warning}
              </Notice>
            ))}
          </div>
        ) : null}

        {(planning.isError || publish.isError) && fields === null ? (
          <ErrorBlock live compact error={refused} title={publish.isError ? t("network.ports.publishFailed") : t("network.ports.checkFailed")} />
        ) : null}
      </form>
    </Dialog>
  );
}

function stateOf(exposure: StreamExposure): "running" | "deploying" | "warning" {
  if (exposure.state === "open") return "running";
  return exposure.state === "opening" ? "deploying" : "warning";
}

/**
 * The Ports tab: the TCP and UDP ports nginx publishes in front of a backend, for the services
 * that are not HTTP (a game server, a broker, a WireGuard endpoint). Publishing asks first what it
 * would do - the file, the firewall rules, what to look out for - and only then does it; closing
 * removes the firewall rules Noust made and nothing else.
 */
export function StreamsPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  const streams = useQuery(streamsQuery());
  const [publishing, setPublishing] = useState(false);
  const [closing, setClosing] = useState<StreamExposure | null>(null);
  const rows = streams.data?.exposures ?? [];
  const support = streams.data?.support;
  const empty = streams.data !== undefined && rows.length === 0;

  const columns: Column<StreamExposure>[] = [
    { id: "name", header: t("common.words.name"), cell: (row) => <span translate="no">{row.name}</span>, sortValue: (row) => row.name },
    { id: "port", header: t("network.ports.portColumn"), cell: (row) => <Mono>{`${row.protocol.toUpperCase()} ${String(row.listen_port)}`}</Mono>, sortValue: (row) => row.listen_port },
    { id: "target", header: t("network.ports.targetColumn"), hideBelow: "sm", cell: (row) => <Mono>{row.target}</Mono> },
    {
      id: "sources",
      header: t("network.ports.sourcesColumn"),
      hideBelow: "md",
      cell: (row) => (row.sources.length === 0 ? t("network.ports.anyone") : row.sources.join(", ")),
    },
    {
      id: "state",
      header: t("network.ports.stateColumn"),
      card: "status",
      cell: (row) => <StatusPill state={stateOf(row)} label={t(`network.ports.state.${row.state === "open" ? "open" : row.state === "opening" ? "opening" : "closing"}`)} appearance="inline" size="sm" />,
    },
  ];

  return (
    <DomainsListPage
      primaryAction={
        <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setPublishing(true)} disabled={support !== undefined && !support.available}>
          {t("network.ports.publishAction")}
        </Button>
      }
      {...(support !== undefined && !support.available
        ? {
            notice: (
              <Notice tone="warning" title={support.problem ?? t("network.ports.unavailable")}>
                {support.hint ? <SystemOutput label={t("network.ports.unavailable")}>{support.hint}</SystemOutput> : null}
              </Notice>
            ),
          }
        : {})}
      footer={<CommandHint command="noust stream list" label={t("common.words.fromTerminal")} />}
    >
      {/* design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed */}
      {streams.isError && streams.data === undefined ? (
        <ErrorBlock error={streams.error} title={t("network.ports.listFailed")} onRetry={() => void streams.refetch()} retrying={streams.isRefetching} />
      ) : empty ? (
        <EmptyState
          variant="firstUse"
          icon={<Radio />}
          title={t("network.ports.emptyTitle")}
          description={t("network.ports.emptyDescription")}
          {...(support?.available === false
            ? {}
            : {
                action: (
                  <Button icon={<Plus aria-hidden="true" />} onClick={() => setPublishing(true)}>
                    {t("network.ports.publishAction")}
                  </Button>
                ),
              })}
          command="noust stream add minecraft --port 25565 --to 25565"
        />
      ) : (
        <DataTable
          caption={t("network.ports.tableCaption")}
          columns={columns}
          rows={rows}
          getRowId={(row) => row.name}
          loading={streams.isPending}
          rowActions={(row) => (
            <RowMenu name={row.name}>
              <MenuItem icon={<ICONS.delete />} destructive onClick={() => setClosing(row)}>
                {t("network.ports.close")}
              </MenuItem>
            </RowMenu>
          )}
        />
      )}
      <PublishDialog open={publishing} onClose={() => setPublishing(false)} />
      <ConfirmDialog
        open={closing !== null}
        onOpenChange={(open) => {
          if (!open) setClosing(null);
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.ports.closeTitle", { name: closing?.name ?? "" })}
        description={t("network.ports.closeDescription")}
        actionLabel={t("network.ports.close")}
        onConfirm={async () => {
          if (closing === null) return;
          await request("delete", "/api/streams/{name}", { params: { name: closing.name } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.streams });
          toast.success(t("network.ports.closedToast", { name: closing.name }));
        }}
      />
    </DomainsListPage>
  );
}
