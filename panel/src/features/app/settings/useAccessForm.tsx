import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { networkKeys, policyOf } from "../../../api/queries/network";
import type { AppPolicy, PolicyBody } from "../../../api/queries/network";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { changesOf, draftOf, errorsOf, lockoutAddress, noValidCertificate, unreachableAuth } from "./access";
import type { AccessDraft, AccessField } from "./access";
import type { FormPart } from "./formParts";

/** The server's name for each field of the form, which is where a refusal of it lands. */
const SERVER_FIELD: Record<AccessField, string> = {
  basic_auth: "basic_auth",
  realm: "realm",
  access: "access",
  allow: "allow_ips",
  deny: "deny_ips",
  real_ip: "real_ip",
  real_ip_header: "real_ip_header",
  trusted_proxies: "trusted_proxies",
  rate_amount: "rate_limit",
  rate_unit: "rate_limit",
  rate_burst: "rate_burst",
  connection_limit: "connection_limit",
  hsts: "hsts",
  noindex: "noindex",
  force_https: "force_https",
  headers: "headers",
  hidden_headers: "hidden_headers",
  routes: "routes",
  http3: "http3",
  client_certs: "client_certs",
  waf: "waf",
  waf_exclude: "waf_exclusions",
  forward_url: "forward_auth",
  forward_sign_in: "forward_auth",
  forward_headers: "forward_auth",
};

const SERVER_FIELDS = [...new Set(Object.values(SERVER_FIELD)), "canonical"] as const;

/**
 * The state of the form of the site's policy: the draft, what differs from the stored policy and what
 * the server refused. Access and Protection each hold one: they share the policy the server stores,
 * and each sends only the fields that changed in its own page.
 */
export function useAccessForm(domain: string, policy: AppPolicy) {
  const t = useT();
  const queryClient = useQueryClient();
  const stored = policy.policy;
  const current = draftOf(stored);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState<AccessDraft>(current);
  const [submitted, setSubmitted] = useState(false);
  const [lockout, setLockout] = useState<{ body: PolicyBody; address: string } | null>(null);
  // The authentication service does not answer: asked about, because with it down everyone is refused.
  const [unreachable, setUnreachable] = useState<{ body: PolicyBody; message: string } | null>(null);
  // The site would ask for a certificate and none is valid: asked about, because nobody could open it.
  const [noCertificate, setNoCertificate] = useState<{ body: PolicyBody } | null>(null);

  // The policy changed underneath an untouched form (saved here, or from a terminal): follow it.
  if (JSON.stringify(current) !== JSON.stringify(baseline)) {
    setBaseline(current);
    if (JSON.stringify(draft) === JSON.stringify(baseline)) setDraft(current);
  }

  const save = useMutation({
    mutationFn: async (body: PolicyBody & { accept_lockout?: boolean; accept_unreachable_auth?: boolean }) =>
      policyOf(await request("put", "/api/apps/{domain}/policy", { params: { domain }, body: { accept_lockout: false, accept_unreachable_auth: false, ...body } })),
    onSuccess: (next) => {
      queryClient.setQueryData(networkKeys.policy(domain), next);
      const fresh = draftOf(next.policy);
      setDraft(fresh);
      setBaseline(fresh);
      setSubmitted(false);
      setLockout(null);
      setUnreachable(null);
      setNoCertificate(null);
    },
  });

  const { count, body } = changesOf(draft, stored);
  const errors = errorsOf(draft);
  const split = splitErrors(save.error, SERVER_FIELDS);
  // A list that would refuse the operator's own address is asked about, not shown as an error.
  const asking = lockoutAddress(split.fields["allow_ips"]) !== null;
  const askingAuth = unreachableAuth(split.fields["forward_auth"]);
  const askingCertificate = noValidCertificate(split.fields["client_certs"]);

  const part: FormPart = {
    changes: count,
    elevated: false,
    check: () => {
      setSubmitted(true);
      return Object.keys(errors).length === 0;
    },
    save: async () => {
      try {
        await save.mutateAsync(body);
      } catch (error) {
        const refused = splitErrors(error, SERVER_FIELDS).fields;
        const address = lockoutAddress(refused["allow_ips"]);
        if (address !== null) setLockout({ body, address });
        const unanswered = refused["forward_auth"];
        if (unreachableAuth(unanswered)) setUnreachable({ body, message: unanswered ?? "" });
        if (noValidCertificate(refused["client_certs"])) setNoCertificate({ body });
        throw error;
      }
    },
    discard: () => {
      setDraft(current);
      setSubmitted(false);
      setLockout(null);
      setUnreachable(null);
      setNoCertificate(null);
      save.reset();
    },
  };

  return {
    part,
    draft,
    edit: <K extends AccessField>(field: K, value: AccessDraft[K]): void => {
      setDraft((previous) => ({ ...previous, [field]: value }));
      if (save.isError) save.reset();
    },
    errorOf: (field: AccessField): string | undefined => {
      const hidden = (asking && field === "allow") || (askingAuth && SERVER_FIELD[field] === "forward_auth") || (askingCertificate && field === "client_certs");
      const refused = hidden ? undefined : split.fields[SERVER_FIELD[field]];
      const quick = submitted ? errors[field] : undefined;
      return refused ?? (quick === undefined ? undefined : t(quick));
    },
    failure: save.isError ? split.form : null,
    lockout,
    accept: async (): Promise<void> => {
      if (lockout === null) return;
      await save.mutateAsync({ ...lockout.body, accept_lockout: true });
    },
    dismissLockout: () => {
      setLockout(null);
      save.reset();
    },
    unreachable,
    acceptUnreachable: async (): Promise<void> => {
      if (unreachable === null) return;
      await save.mutateAsync({ ...unreachable.body, accept_unreachable_auth: true });
    },
    dismissUnreachable: () => {
      setUnreachable(null);
      save.reset();
    },
    noCertificate,
    acceptNoCertificate: async (): Promise<void> => {
      if (noCertificate === null) return;
      await save.mutateAsync({ ...noCertificate.body, accept_lockout: true });
    },
    dismissNoCertificate: () => {
      setNoCertificate(null);
      save.reset();
    },
  };
}

export type AccessForm = ReturnType<typeof useAccessForm>;

/**
 * The three questions a save of the policy can come back with: a list that would refuse the
 * operator's own address, an authentication service that does not answer, and a client certificate
 * required when none is valid. Each is the server's refusal, accepted on purpose or dropped.
 */
export function AccessQuestions({ form, domain }: { form: AccessForm; domain: string }) {
  const t = useT();
  const { node } = useNode();
  return (
    <>
      <ConfirmDialog
        open={form.lockout !== null}
        onOpenChange={(open) => {
          if (!open) form.dismissLockout();
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.access.save.lockoutTitle")}
        description={t("network.access.save.lockoutDescription", { address: form.lockout?.address ?? "", domain })}
        actionLabel={t("network.access.save.lockoutAction")}
        onConfirm={form.accept}
      />
      <ConfirmDialog
        open={form.unreachable !== null}
        onOpenChange={(open) => {
          if (!open) form.dismissUnreachable();
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.access.save.unreachableTitle")}
        description={
          <>
            {t("network.access.save.unreachableDescription")}
            <SystemOutput label={t("network.access.save.unreachableLabel")} maxHeight="max-h-28">
              {form.unreachable?.message ?? ""}
            </SystemOutput>
          </>
        }
        actionLabel={t("network.access.save.unreachableAction")}
        onConfirm={form.acceptUnreachable}
      />
      <ConfirmDialog
        open={form.noCertificate !== null}
        onOpenChange={(open) => {
          if (!open) form.dismissNoCertificate();
        }}
        friction="simple"
        server={node}
        destructive
        title={t("network.access.save.noCertificateTitle")}
        description={t("network.access.save.noCertificateDescription", { domain })}
        actionLabel={t("network.access.save.noCertificateAction")}
        onConfirm={form.acceptNoCertificate}
      />
    </>
  );
}
