import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ShieldPlus } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { internalCaQuery, networkKeys } from "../../api/queries/network";
import type { InternalCa, InternalCaAuthority } from "../../api/queries/network";
import { certKeys } from "../../api/queries/certs";
import { ErrorBlock } from "../../components/page/QueryState";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { downloadBase64, downloadText } from "../../lib/clipboard";
import { formatDate } from "../../lib/format";
import { reportActionError } from "../apps/useAppActions";
import { IssuedList, SignForm, TrustSteps, listOf } from "./InternalAuthorityPanels";
import { LoadingRegion } from "../../components/page";

/** The base64 of a PEM certificate is the certificate itself in the binary form (DER). */
function derOf(pem: string): string {
  return pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
}

function MakeForm({ onMade }: { onMade: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [dns, setDns] = useState("");
  const [networks, setNetworks] = useState("");
  const [only, setOnly] = useState(false);
  const [name, setName] = useState("");
  const [trustHere, setTrustHere] = useState(false);
  const make = useMutation({
    mutationFn: () =>
      request("post", "/api/certs/ca", {
        body: {
          days: 3650,
          permit_dns: listOf(dns),
          permit_networks: listOf(networks),
          only,
          trust_here: trustHere,
          ...(name.trim() !== "" ? { name: name.trim() } : {}),
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.internalCa });
      toast.success(t("internalCa.madeToast"));
      onMade();
    },
  });
  const fields = make.error && isApiError(make.error) ? make.error.fields : null;
  return (
    <form
      id={formId}
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
        event.preventDefault();
        make.mutate();
      }}
    >
      <Notice title={t("internalCa.noneTitle")}>{t("internalCa.noneNote")}</Notice>
      <Field label={t("internalCa.permitDnsLabel")} description={t("internalCa.permitDnsHelp")} optional error={fields?.["permit"] ?? null}>
        <Input mono value={dns} onValueChange={(next: string) => setDns(next)} autoComplete="off" spellCheck={false} placeholder="corp.example.com" />
      </Field>
      <Field label={t("internalCa.permitNetworksLabel")} description={t("internalCa.permitNetworksHelp")} optional>
        <Input mono value={networks} onValueChange={(next: string) => setNetworks(next)} autoComplete="off" spellCheck={false} placeholder="10.20.0.0/16" />
      </Field>
      <Checkbox label={t("internalCa.onlyLabel")} checked={only} onCheckedChange={setOnly} />
      <Field label={t("internalCa.nameLabel")} description={t("internalCa.nameHelp")} optional error={fields?.["name"] ?? null}>
        <Input value={name} onValueChange={(next: string) => setName(next)} autoComplete="off" />
      </Field>
      <Checkbox label={t("internalCa.trustHereLabel")} description={t("internalCa.trustHereHelp")} checked={trustHere} onCheckedChange={setTrustHere} />
      {make.isError && fields === null ? <ErrorBlock live compact error={make.error} title={t("internalCa.makeFailed")} /> : null}
      <div className="flex justify-end">
        <Button type="submit" form={formId} variant="primary" loading={make.isPending}>
          {t("internalCa.make")}
        </Button>
      </div>
    </form>
  );
}

function Made({ overview, authority, onRemoved }: { overview: InternalCa; authority: InternalCaAuthority; onRemoved: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const [force, setForce] = useState(false);
  const pem = overview.root_pem ?? "";
  const trust = useMutation({
    mutationFn: (on: boolean) => (on ? request("post", "/api/certs/ca/trust-here") : request("delete", "/api/certs/ca/trust-here")),
    onSuccess: (next, on) => {
      queryClient.setQueryData(networkKeys.internalCa, next);
      toast.success(on ? t("internalCa.trustedToast") : t("internalCa.untrustedToast"));
    },
    onError: (error) => {
      reportActionError(t("internalCa.trustFailed"), error);
    },
  });
  return (
    <div className="flex flex-col gap-6">
      {authority.warnings.map((warning) => (
        <Notice key={warning} tone="warning">
          <span translate="no">{warning}</span>
        </Notice>
      ))}
      <KeyValueList
        items={[
          { label: t("internalCa.fingerprintLabel"), value: authority.fingerprint },
          {
            label: t("internalCa.validUntilLabel"),
            value: t("internalCa.validUntilValue", { date: formatDate(new Date(authority.not_after), {}, t.locale), days: authority.days_left }),
            mono: false,
            copy: false,
          },
          { label: t("internalCa.signsForLabel"), value: authority.permitted_dns.join(", ") },
          { label: t("internalCa.signsRangesLabel"), value: authority.permitted_networks.join(", ") },
          { label: t("internalCa.keptLabel"), value: String(authority.issued), copy: false },
          { label: t("internalCa.trustedHereLabel"), value: overview.trusted_here ? t("internalCa.yes") : t("internalCa.no"), mono: false, copy: false },
        ]}
      />
      <div className="flex flex-wrap gap-2">
        <Button icon={<Download aria-hidden="true" />} onClick={() => downloadText("ca.crt", pem, "application/x-pem-file")}>{t("internalCa.downloadPem")}</Button>
        <Button icon={<Download aria-hidden="true" />} onClick={() => downloadBase64("ca.cer", derOf(pem), "application/x-x509-ca-cert")}>{t("internalCa.downloadDer")}</Button>
        {overview.system_store !== null ? (
          <Button icon={<ShieldPlus aria-hidden="true" />} loading={trust.isPending} onClick={() => trust.mutate(!overview.trusted_here)}>
            {overview.trusted_here ? t("internalCa.untrustHere") : t("internalCa.trustHere")}
          </Button>
        ) : null}
      </div>
      {overview.system_store === null ? <p className="text-13 text-fg-muted">{t("internalCa.noSystemStore")}</p> : null}
      <Subsection title={t("internalCa.devicesTitle")} description={t("internalCa.devicesHelp")}>
        <TrustSteps enabled />
      </Subsection>
      <Subsection title={t("internalCa.certsTitle")}>
        <IssuedList issued={overview.issued} />
      </Subsection>
      <Subsection title={t("internalCa.signTitle")}>
        <SignForm authority={authority} />
      </Subsection>
      <div className="flex justify-start">
        <Button icon={<ICONS.delete aria-hidden="true" />} onClick={() => setRemoving(true)}>
          {t("internalCa.remove")}
        </Button>
      </div>
      <ConfirmDialog
        open={removing}
        onOpenChange={(next) => {
          setRemoving(next);
          if (!next) setForce(false);
        }}
        friction="simple"
        destructive
        title={t("internalCa.removeTitle")}
        description={t("internalCa.removeDescription")}
        actionLabel={t("internalCa.remove")}
        onConfirm={async () => {
          await request("delete", "/api/certs/ca", { query: { force } });
          void queryClient.invalidateQueries({ queryKey: networkKeys.internalCa });
          void queryClient.invalidateQueries({ queryKey: certKeys.all });
          toast.success(t("internalCa.removedToast"));
          onRemoved();
        }}
      >
        {authority.issued > 0 ? <Checkbox label={t("internalCa.removeForce")} checked={force} onCheckedChange={setForce} /> : null}
      </ConfirmDialog>
    </div>
  );
}

/**
 * The server's own certificate authority, for names that only resolve inside a private network.
 * Making it and deleting it ask for sudo mode; the certificate is public and offered in both
 * forms, the key never reaches the console. How to trust it on each kind of device is the
 * server's text, with the fingerprint to compare.
 */
export function InternalAuthorityDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const overview = useQuery({ ...internalCaQuery(), enabled: open });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="lg"
      title={t("internalCa.title")}
      description={t("internalCa.description")}
    >
      {overview.isError ? (
        <ErrorBlock compact error={overview.error} title={t("internalCa.readFailed")} onRetry={() => void overview.refetch()} retrying={overview.isRefetching} />
      ) : overview.data === undefined ? (
        <LoadingRegion label={t("internalCa.loading")}>
          <Skeleton className="h-40 rounded-card" />
        </LoadingRegion>
      ) : overview.data.authority === null ? (
        <MakeForm onMade={() => void overview.refetch()} />
      ) : (
        <Made overview={overview.data} authority={overview.data.authority} onRemoved={() => void overview.refetch()} />
      )}
    </Dialog>
  );
}
