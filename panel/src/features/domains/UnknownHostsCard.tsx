import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Pencil, X } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { networkKeys, unknownHostsQuery } from "../../api/queries/network";
import type { UnknownHosts } from "../../api/queries/network";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { LoadingRegion } from "../../components/page/LoadingRegion";

type Action = "close" | "refuse" | "redirect";

/** What the web server answers, in one sentence. */
function answer(status: UnknownHosts, t: T): string {
  if (!status.enabled) return t("network.unknownHosts.firstSite");
  if (status.action === "redirect") return t("network.unknownHosts.redirects", { to: status.redirect_to });
  return status.action === "refuse" ? t("network.unknownHosts.refuses") : t("network.unknownHosts.closes");
}

function ChangeDialog({ status, onClose }: { status: UnknownHosts; onClose: () => void }) {
  const t = useT();
  const formId = useId();
  const queryClient = useQueryClient();
  const [action, setAction] = useState<Action>(status.enabled && status.action !== "" ? (status.action as Action) : "close");
  const [to, setTo] = useState(status.redirect_to);
  const [switchOff, setSwitchOff] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const holders = status.holders ?? [];
  const distribution = holders.filter((holder) => holder.distribution);

  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/unknown-hosts", {
        body: { action, redirect_to: action === "redirect" ? to.trim() : "", switch_off_distribution_site: switchOff, webserver: status.webserver },
      }),
    onSuccess: (next) => {
      queryClient.setQueryData(networkKeys.unknownHosts, next);
      toast.success(t("network.unknownHosts.saved"));
      onClose();
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (action === "redirect" && !/^https?:\/\/\S+$/.test(to.trim())) {
      setProblem(t("network.unknownHosts.errors.address"));
      return;
    }
    setProblem(null);
    save.mutate();
  };

  const field = save.error && isApiError(save.error) ? save.error.fields : undefined;
  const addressError = problem ?? field?.["redirect_to"] ?? null;
  const placed = addressError !== null || field?.["switch_off_distribution_site"] !== undefined;
  const options = (["close", "refuse", "redirect"] as const).map((value) => ({
    value,
    label: t(`network.unknownHosts.actions.${value}`),
    description: t(`network.unknownHosts.actions.${value}Help`),
  }));

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !save.isPending) onClose();
      }}
      size="md"
      title={t("network.unknownHosts.dialogTitle")}
      description={t("network.unknownHosts.dialogDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("network.unknownHosts.apply")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <ChoiceCards<Action> legend={t("network.unknownHosts.legend")} options={options} value={action} onValueChange={setAction} />
        {action === "redirect" ? (
          <Field label={t("network.unknownHosts.address")} description={t("network.unknownHosts.addressHelp")} error={addressError}>
            <Input mono value={to} onValueChange={setTo} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
        ) : null}
        {distribution.length > 0 ? (
          <Checkbox
            label={t("network.unknownHosts.switchOff", { file: distribution[0]?.file ?? "" })}
            description={t("network.unknownHosts.switchOffHelp")}
            checked={switchOff}
            onCheckedChange={setSwitchOff}
          />
        ) : null}
        {save.isError && !placed ? <ErrorBlock live compact error={save.error} title={t("network.unknownHosts.notSaved")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * What answers a request for a name no site serves. Without a site of its own the web server
 * answers it with the first site it loaded, which shows an application to anyone who points a
 * name at this address. The card says what happens now, what else claims to be the default
 * (and so stands in the way), and changes it; the web server tests the new site before it is
 * reloaded and its own words are the error when it refuses.
 */
export function UnknownHostsCard() {
  const t = useT();
  const queryClient = useQueryClient();
  const status = useQuery(unknownHostsQuery());
  const [changing, setChanging] = useState(false);

  const reset = useMutation({
    mutationFn: () => request("delete", "/api/unknown-hosts", {}),
    onSuccess: (next) => {
      queryClient.setQueryData(networkKeys.unknownHosts, next);
      toast.success(t("network.unknownHosts.removed"));
    },
  });

  // design-exception: hand-error-ternary the card keeps its frame and a skeleton of its action while it loads, so the page below does not move
  if (status.isError && status.data === undefined) {
    return <ErrorBlock error={status.error} title={t("network.unknownHosts.readFailed")} onRetry={() => void status.refetch()} retrying={status.isRefetching} />;
  }
  const data = status.data;
  const holders = data?.holders ?? [];
  const foreign = holders.filter((holder) => !holder.distribution);

  return (
    <Card
      icon={Globe}
      loading={data === undefined}
      level={2}
      title={t("network.unknownHosts.title")}
      description={t("network.unknownHosts.description")}
      actions={
        data === undefined ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <span className="flex items-center gap-2">
            {data.enabled ? (
              <Button icon={<X aria-hidden="true" />} size="sm" loading={reset.isPending} onClick={() => reset.mutate()}>
                {t("network.unknownHosts.remove")}
              </Button>
            ) : null}
            <Button icon={<Pencil aria-hidden="true" />} size="sm" onClick={() => setChanging(true)}>
              {data.enabled ? t("network.unknownHosts.change") : t("network.unknownHosts.set")}
            </Button>
          </span>
        )
      }
    >
      {data === undefined ? (
        <LoadingRegion label={t("network.unknownHosts.loading")} className="flex flex-col gap-2">
          <Skeleton className="h-5 w-2/3" />
        </LoadingRegion>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-13 text-fg">{answer(data, t)}</p>
          {data.enabled ? (
            <KeyValueList
              items={[
                { label: t("network.unknownHosts.overTls"), value: data.tls ? t("network.unknownHosts.tlsRefused") : t("network.unknownHosts.tlsNotCovered"), mono: false, copy: false },
                { label: t("network.unknownHosts.file"), value: data.file },
                ...(data.disabled_distribution_site !== "" ? [{ label: t("network.unknownHosts.switchedOff"), value: data.disabled_distribution_site }] : []),
              ]}
            />
          ) : null}
          {foreign.length > 0 ? (
            <Notice tone="warning" title={t("network.unknownHosts.heldTitle")}>
              {t("network.unknownHosts.held", { file: foreign[0]?.file ?? "", port: foreign[0]?.port ?? 0 })}
            </Notice>
          ) : null}
          {(data.notes ?? []).map((note) => (
            <p key={note} className="text-13 text-fg-muted">
              {note}
            </p>
          ))}
          {reset.isError ? <ErrorBlock live compact error={reset.error} title={t("network.unknownHosts.notRemoved")} /> : null}
        </div>
      )}
      {changing && data !== undefined ? <ChangeDialog status={data} onClose={() => setChanging(false)} /> : null}
    </Card>
  );
}
