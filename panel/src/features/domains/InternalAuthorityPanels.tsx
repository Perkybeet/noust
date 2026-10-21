import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { internalCaTrustQuery, networkKeys } from "../../api/queries/network";
import type { InternalCaAuthority, InternalCaIssued } from "../../api/queries/network";
import { certKeys } from "../../api/queries/certs";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { CopyValue } from "../../components/ui/CopyValue";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { formatDate } from "../../lib/format";
import { reportActionError } from "../apps/useAppActions";

/** Names typed in one box: separated by commas or by lines, empty ones dropped. */
export function listOf(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter((item) => item !== "");
}

/**
 * How to trust the authority on a kind of device: the steps are the server's text (system
 * commands, not translated), with the fingerprint written in. Nothing is changed on the device.
 */
export function TrustSteps({ enabled }: { enabled: boolean }) {
  const t = useT();
  const guides = useQuery({ ...internalCaTrustQuery(), enabled });
  const [chosen, setChosen] = useState<string | null>(null);
  if (guides.isError) {
    return <ErrorBlock compact error={guides.error} title={t("internalCa.readFailed")} onRetry={() => void guides.refetch()} retrying={guides.isRefetching} />;
  }
  if (guides.data === undefined) return <Skeleton className="h-10 rounded-card" />;
  const guide = guides.data.guides.find((item) => item.id === chosen);
  return (
    <div className="flex flex-col gap-3">
      <Field label={t("internalCa.deviceLabel")} nativeLabel={false}>
        <Select
          aria-label={t("internalCa.deviceLabel")}
          value={chosen}
          placeholder={t("internalCa.devicePlaceholder")}
          onValueChange={setChosen}
          options={guides.data.guides.map((item) => ({ value: item.id, label: item.title }))}
        />
      </Field>
      {guide !== undefined ? (
        <div className="flex flex-col gap-2">
          <p className="text-13 text-fg-muted">{t("internalCa.deviceFile", { file: guide.file === "der" ? "ca.cer" : "ca.crt" })}</p>
          <ol className="flex list-decimal flex-col gap-2 pl-5 text-13">
            {guide.steps.map((step) => (
              <li key={step}>
                {step.startsWith("$ ") || step.startsWith("PS> ") ? <CopyValue value={step.replace(/^(\$ |PS> )/, "")} label={step} /> : <span translate="no">{step}</span>}
              </li>
            ))}
          </ol>
          {guide.note !== "" ? <p className="text-13 text-fg-muted" translate="no">{guide.note}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** The certificates the authority signed: what each covers, when it ends, who serves it, and signing it again. */
export function IssuedList({ issued }: { issued: readonly InternalCaIssued[] }) {
  const t = useT();
  const queryClient = useQueryClient();
  const renew = useMutation({
    mutationFn: (name: string) => request("post", "/api/certs/ca/issued/{name}/renew", { params: { name } }),
    onSuccess: (_, name) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.internalCa });
      void queryClient.invalidateQueries({ queryKey: certKeys.all });
      toast.success(t("internalCa.renewedToast", { name }));
    },
    onError: (error, name) => {
      reportActionError(t("internalCa.renewFailed", { name }), error);
    },
  });
  if (issued.length === 0) return <p className="text-13 text-fg-muted">{t("internalCa.certsNone")}</p>;
  return (
    <ul className="flex flex-col divide-y divide-border">
      {issued.map((item) => (
        <li key={item.name} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2">
          <div className="flex min-w-0 flex-col gap-0.5">
            <Mono>{item.name}</Mono>
            <span className="text-13 text-fg-muted">
              {t("internalCa.certCovers")}: <Mono>{item.names.join(", ")}</Mono>
            </span>
            <span className="text-13 text-fg-muted">
              {t("internalCa.certExpires")}: {formatDate(new Date(item.not_after), {}, t.locale)} (
              {item.expiring ? t("internalCa.certRenewing", { days: item.days_left }) : t("internalCa.certDaysLeft", { days: item.days_left })})
              {" · "}
              {t("internalCa.certServedBy")}: {item.apps.length === 0 ? t("internalCa.certNobody") : <Mono>{item.apps.join(", ")}</Mono>}
            </span>
          </div>
          <Button size="sm" loading={renew.isPending && renew.variables === item.name} aria-label={t("internalCa.renewLabel", { name: item.name })} onClick={() => renew.mutate(item.name)}>
            {t("internalCa.renew")}
          </Button>
        </li>
      ))}
    </ul>
  );
}

/** Signs a certificate by hand for names the authority may sign for. */
export function SignForm({ authority }: { authority: InternalCaAuthority }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [label, setLabel] = useState("");
  const [names, setNames] = useState("");
  const [days, setDays] = useState("90");
  const [pressed, setPressed] = useState(false);
  const sign = useMutation({
    mutationFn: () => request("post", "/api/certs/ca/issued", { body: { name: label.trim(), names: listOf(names), days: Number(days) || 90 } }),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.internalCa });
      void queryClient.invalidateQueries({ queryKey: certKeys.all });
      void queryClient.invalidateQueries({ queryKey: networkKeys.uploaded });
      toast.success(t("internalCa.signedToast", { name: made.name }));
      setLabel("");
      setNames("");
      sign.reset();
    },
  });
  const fields = sign.error && isApiError(sign.error) ? sign.error.fields : null;
  return (
    <form
      id={formId}
      noValidate
      className="flex flex-col gap-3"
      onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (label.trim() === "" || listOf(names).length === 0) setPressed(true);
        else sign.mutate();
      }}
    >
      <Field label={t("internalCa.signNameLabel")} description={t("internalCa.signNameHelp")} error={fields?.["name"] ?? (pressed && label.trim() === "" ? t("internalCa.required") : null)}>
        <Input mono value={label} onValueChange={(next: string) => setLabel(next)} autoComplete="off" spellCheck={false} placeholder="wiki" />
      </Field>
      <Field
        label={t("internalCa.signNamesLabel")}
        description={`${t("internalCa.signNamesHelp")} ${[...authority.permitted_dns].slice(0, 4).join(", ")}`}
        error={fields?.["names"] ?? (pressed && listOf(names).length === 0 ? t("internalCa.required") : null)}
      >
        <Input mono value={names} onValueChange={(next: string) => setNames(next)} autoComplete="off" spellCheck={false} placeholder="wiki.home.arpa" />
      </Field>
      <Field label={t("internalCa.signDaysLabel")} error={fields?.["days"] ?? null}>
        <Input mono inputMode="numeric" value={days} onValueChange={(next: string) => setDays(next)} autoComplete="off" />
      </Field>
      {sign.isError && fields === null ? <ErrorBlock live compact error={sign.error} title={t("internalCa.signFailed")} /> : null}
      <div className="flex justify-end">
        <Button type="submit" form={formId} variant="primary" loading={sign.isPending}>
          {t("internalCa.sign")}
        </Button>
      </div>
    </form>
  );
}
