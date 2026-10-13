import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { acmeQuery, appCertificateQuery, dnsAccountsQuery, networkKeys, uploadedQuery } from "../../api/queries/network";
import type { AppCertificate } from "../../api/queries/network";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";

type Source = "http" | "dns" | "upload";

/** Which of the three ways a certificate comes to be an application's choice is. */
export function sourceOf(certificate: Pick<AppCertificate, "source" | "challenge">): Source {
  if (certificate.source === "custom") return "upload";
  return certificate.challenge === "dns-01" ? "dns" : "http";
}

/** The choice in a sentence: "Ordered from Let's Encrypt over HTTP". */
export function sourceWords(t: T, certificate: AppCertificate): string {
  const source = sourceOf(certificate);
  if (source === "upload") return t("network.certSource.uploaded", { name: certificate.name ?? "" });
  if (certificate.source === "lineage") return t("network.certSource.lineage", { name: certificate.name ?? "" });
  if (source === "dns") return t("network.certSource.orderedDns", { authority: certificate.authority, account: certificate.dns_provider ?? "" });
  return t("network.certSource.orderedHttp", { authority: certificate.authority });
}

function ChooseDialog({
  domain,
  certificate,
  open,
  onClose,
  onChosen,
}: {
  domain: string;
  certificate: AppCertificate;
  open: boolean;
  onClose: () => void;
  onChosen: (next: AppCertificate) => void;
}) {
  const t = useT();
  const formId = useId();
  const accounts = useQuery({ ...dnsAccountsQuery(), enabled: open });
  const uploads = useQuery({ ...uploadedQuery(), enabled: open });
  const [source, setSource] = useState<Source>(sourceOf(certificate));
  const [account, setAccount] = useState(certificate.dns_provider ?? "");
  const [upload, setUpload] = useState(certificate.name ?? "");
  const [problem, setProblem] = useState<string | null>(null);

  const accountNames = (accounts.data?.accounts ?? []).map((item) => item.name);
  const uploaded = uploads.data?.certificates ?? [];

  const choose = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/certificate", {
        params: { domain },
        body:
          source === "upload"
            ? { source: "custom", name: upload, challenge: "http-01", order: true }
            : { source: "acme", challenge: source === "dns" ? "dns-01" : "http-01", dns_provider: source === "dns" ? account : null, order: true },
      }),
    onSuccess: (next) => {
      onChosen(next);
      close(true);
    },
  });

  function close(settled = false): void {
    if (choose.isPending && !settled) return;
    onClose();
    setProblem(null);
    choose.reset();
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (source === "dns" && account === "") {
      setProblem(t("network.certSource.needAccount"));
      return;
    }
    if (source === "upload" && upload === "") {
      setProblem(t("network.certSource.needUpload"));
      return;
    }
    setProblem(null);
    choose.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      title={t("network.certSource.dialogTitle", { domain })}
      description={t("network.certSource.dialogDescription")}
      footer={
        <>
          <Button disabled={choose.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={choose.isPending}>
            {t("network.certSource.apply")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <ChoiceCards<Source>
          legend={t("network.certSource.legend")}
          options={[
            { value: "http", label: t("network.certSource.http"), description: t("network.certSource.httpHelp") },
            { value: "dns", label: t("network.certSource.dns"), description: t("network.certSource.dnsHelp") },
            ...(uploaded.length > 0 ? [{ value: "upload" as const, label: t("network.certSource.upload"), description: t("network.certSource.uploadHelp") }] : []),
          ]}
          value={source}
          onValueChange={setSource}
        />
        {source === "dns" ? (
          accountNames.length === 0 ? (
            <p className="text-13 text-fg-muted">
              {t("network.certSource.noAccounts")}{" "}
              <TextLink to="/domains/dns" size="ui">
                {t("network.appDns.addAccount")}
              </TextLink>
            </p>
          ) : (
            <Field label={t("network.certSource.account")} description={t("network.certSource.accountHelp")} error={problem} nativeLabel={false}>
              <Select
                aria-label={t("network.certSource.account")}
                value={account === "" ? null : account}
                placeholder={t("network.certSource.accountPlaceholder")}
                onValueChange={setAccount}
                options={accountNames.map((name) => ({ value: name, label: name }))}
              />
            </Field>
          )
        ) : null}
        {source === "upload" ? (
          <Field label={t("network.certSource.uploadLabel")} error={problem} nativeLabel={false}>
            <Select
              aria-label={t("network.certSource.uploadLabel")}
              value={upload === "" ? null : upload}
              placeholder={t("network.certSource.uploadPlaceholder")}
              onValueChange={setUpload}
              options={uploaded.map((item) => ({ value: item.name, label: `${item.name} (${item.names.join(", ")})` }))}
            />
          </Field>
        ) : null}
        {choose.isError ? <ErrorBlock live compact error={choose.error} title={t("network.certSource.failed", { domain })} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * Where an application's certificate comes from: ordered from the authority over HTTP, ordered
 * over DNS (a wildcard needs it, and port 80 can stay closed), an upload, or another
 * certificate that already covers the name. Changing how a certificate that exists is proven
 * orders it again, once, as a job the page follows; a failed order puts the choice back and the
 * certificate that served keeps serving.
 */
export function CertificateSourceCard({ domain, onChosen }: { domain: string; onChosen: (jobId: string | null) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const certificate = useQuery(appCertificateQuery(domain));
  const acme = useQuery(acmeQuery());
  const [choosing, setChoosing] = useState(false);

  if (certificate.isPending) return <Skeleton className="h-16 rounded-card" />;
  if (certificate.isError) {
    return <ErrorBlock compact error={certificate.error} title={t("network.certSource.readFailed", { domain })} onRetry={() => void certificate.refetch()} retrying={certificate.isRefetching} />;
  }
  const current = certificate.data;
  return (
    <Card
      as="div"
      padding="sm"
      title={t("network.certSource.title")}
      level={3}
      actions={
        <Button size="sm" onClick={() => setChoosing(true)}>
          {t("network.certSource.change")}
        </Button>
      }
    >
      <p className="text-13 text-pretty text-fg-muted">
        <Mono>{sourceWords(t, current)}</Mono>
        {acme.data !== undefined && !acme.data.trusted && current.source !== "custom" ? ` ${t("network.certSource.staging")}` : ""}
      </p>
      <ChooseDialog
        key={`${current.source}-${current.challenge}-${current.name ?? ""}-${current.dns_provider ?? ""}`}
        domain={domain}
        certificate={current}
        open={choosing}
        onClose={() => setChoosing(false)}
        onChosen={(next) => {
          queryClient.setQueryData(networkKeys.appCertificate(domain), next);
          onChosen(next.job_id ?? null);
        }}
      />
    </Card>
  );
}
