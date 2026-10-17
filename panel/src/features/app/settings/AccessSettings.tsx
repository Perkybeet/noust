import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { appHttp3Query, appPolicyQuery, forwardAuthPresetsQuery, networkKeys, policyOf } from "../../../api/queries/network";
import type { AppPolicy, PolicyBody } from "../../../api/queries/network";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { useOrganization } from "../../teams/useOrganization";
import { splitErrors } from "../../settings/formErrors";
import { AccessUsers } from "./AccessUsers";
import { ClientCertsCard } from "./ClientCertsCard";
import { PrivateCard } from "./PrivateCard";
import { WafCard } from "./WafCard";
import { RoutesCard } from "./RoutesCard";
import { changesOf, draftOf, errorsOf, lockoutAddress, noValidCertificate, unreachableAuth } from "./access";
import type { AccessDraft, AccessField } from "./access";
import { useSaveBar } from "./formParts";
import type { FormPart } from "./formParts";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

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

function Skeletons() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-3">
      <span className="sr-only">{t("network.access.loading")}</span>
      <Skeleton className="h-28 w-full rounded-card" />
      <Skeleton className="h-28 w-full rounded-card" />
    </div>
  );
}

/** The form's state: the draft, what differs from the stored policy and what the server refused. */
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

function PasswordCard({ form, policy, t }: { form: ReturnType<typeof useAccessForm>; policy: AppPolicy; t: T }) {
  const { draft, edit } = form;
  const noAccounts = policy.users.length === 0;
  return (
    <Card title={t("network.access.password.title")} description={t("network.access.password.description")}>
      <div className="flex flex-col gap-4">
        <Checkbox
          label={t("network.access.password.enable")}
          description={noAccounts ? t("network.access.password.enableHelp") : undefined}
          checked={draft.basic_auth}
          onCheckedChange={(checked) => edit("basic_auth", checked)}
        />
        <Field label={t("network.access.password.realm")} description={t("network.access.password.realmHelp")} error={form.errorOf("realm")}>
          <Input value={draft.realm} onValueChange={(value: string) => edit("realm", value)} autoComplete="off" maxLength={64} />
        </Field>
        <ChoiceCards
          legend={t("network.access.password.mode")}
          options={[
            { value: "all", label: t("network.access.password.modeAll"), description: t("network.access.password.modeAllHelp") },
            { value: "any", label: t("network.access.password.modeAny"), description: t("network.access.password.modeAnyHelp") },
          ]}
          value={draft.access}
          onValueChange={(value) => edit("access", value)}
        />
      </div>
    </Card>
  );
}

function AddressesCard({ form, t }: { form: ReturnType<typeof useAccessForm>; t: T }) {
  const { draft, edit } = form;
  return (
    <Card title={t("network.access.addresses.title")} description={t("network.access.addresses.description")}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("network.access.addresses.allow")} description={t("network.access.addresses.allowHelp")} error={form.errorOf("allow")}>
          <Textarea mono rows={5} wrap="off" value={draft.allow} onChange={(event) => edit("allow", event.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("network.access.addresses.deny")} description={t("network.access.addresses.denyHelp")} error={form.errorOf("deny")}>
          <Textarea mono rows={5} wrap="off" value={draft.deny} onChange={(event) => edit("deny", event.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
      </div>
    </Card>
  );
}

function VisitorCard({ form, policy, t }: { form: ReturnType<typeof useAccessForm>; policy: AppPolicy; t: T }) {
  const { draft, edit } = form;
  const source = policy.real_ip_source === "fetched" ? t("network.access.visitor.sourceFetched") : t("network.access.visitor.sourceBuiltIn");
  return (
    <Card title={t("network.access.visitor.title")} description={t("network.access.visitor.description")}>
      <div className="flex flex-col gap-4">
        <ChoiceCards
          legend={t("network.access.visitor.title")}
          options={[
            { value: "off", label: t("network.access.visitor.off"), description: "" },
            {
              value: "cloudflare",
              label: t("network.access.visitor.cloudflare"),
              description: t("network.access.visitor.cloudflareHelp", { count: policy.real_ip_networks.length, source }),
            },
            { value: "custom", label: t("network.access.visitor.custom"), description: t("network.access.visitor.customHelp") },
          ]}
          value={draft.real_ip}
          onValueChange={(value) => edit("real_ip", value)}
        />
        {draft.real_ip === "custom" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("network.access.visitor.header")} description={t("network.access.visitor.headerHelp")} error={form.errorOf("real_ip_header")}>
              <Input mono value={draft.real_ip_header} onValueChange={(value: string) => edit("real_ip_header", value)} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label={t("network.access.visitor.trusted")} description={t("network.access.visitor.trustedHelp")} error={form.errorOf("trusted_proxies")}>
              <Textarea mono rows={4} wrap="off" value={draft.trusted_proxies} onChange={(event) => edit("trusted_proxies", event.target.value)} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

function LimitsCard({ form, t }: { form: ReturnType<typeof useAccessForm>; t: T }) {
  const { draft, edit } = form;
  return (
    <Card title={t("network.access.limits.title")} description={t("network.access.limits.description")}>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex items-start gap-2">
          <Field className="flex-1" label={t("network.access.limits.rate")} description={t("network.access.limits.rateHelp")} error={form.errorOf("rate_amount")}>
            <Input mono inputMode="numeric" value={draft.rate_amount} onValueChange={(value: string) => edit("rate_amount", value)} autoComplete="off" />
          </Field>
          <Field label={t("network.access.limits.rateUnit")} nativeLabel={false}>
            <Select
              compact
              aria-label={t("network.access.limits.rateUnit")}
              value={draft.rate_unit}
              onValueChange={(value) => edit("rate_unit", value)}
              options={[
                { value: "s", label: t("network.access.limits.perSecond") },
                { value: "m", label: t("network.access.limits.perMinute") },
              ]}
            />
          </Field>
        </div>
        <Field label={t("network.access.limits.burst")} description={t("network.access.limits.burstHelp")} error={form.errorOf("rate_burst")}>
          <Input mono inputMode="numeric" value={draft.rate_burst} onValueChange={(value: string) => edit("rate_burst", value)} autoComplete="off" />
        </Field>
        <Field label={t("network.access.limits.connections")} description={t("network.access.limits.connectionsHelp")} error={form.errorOf("connection_limit")}>
          <Input mono inputMode="numeric" value={draft.connection_limit} onValueChange={(value: string) => edit("connection_limit", value)} autoComplete="off" />
        </Field>
      </div>
    </Card>
  );
}

function HeadersCard({ form, policy, t }: { form: ReturnType<typeof useAccessForm>; policy: AppPolicy; t: T }) {
  const { draft, edit } = form;
  return (
    <Card title={t("network.access.headers.title")} description={t("network.access.headers.description")}>
      <div className="flex flex-col gap-4">
        <Checkbox
          label={t("network.access.headers.noindex")}
          description={t("network.access.headers.noindexHelp")}
          checked={draft.noindex}
          onCheckedChange={(checked) => edit("noindex", checked)}
        />
        <Field label={t("network.access.headers.hsts")} nativeLabel={false}>
          <Select
            aria-label={t("network.access.headers.hsts")}
            value={draft.hsts}
            onValueChange={(value) => edit("hsts", value)}
            options={[
              { value: "default", label: t("network.access.headers.hstsDefault") },
              { value: "on", label: t("network.access.headers.hstsOn") },
              { value: "off", label: t("network.access.headers.hstsOff") },
            ]}
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("network.access.headers.custom")} description={t("network.access.headers.customHelp")} error={form.errorOf("headers")}>
            <Textarea mono rows={4} wrap="off" value={draft.headers} onChange={(event) => edit("headers", event.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label={t("network.access.headers.hidden")} description={t("network.access.headers.hiddenHelp")} error={form.errorOf("hidden_headers")}>
            <Textarea mono rows={4} wrap="off" value={draft.hidden_headers} onChange={(event) => edit("hidden_headers", event.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
        </div>
        <div className="flex flex-col gap-1.5">
          <p className="text-13 font-medium text-fg">{t("network.access.headers.sentNow")}</p>
          {policy.headers.length === 0 ? (
            <p className="text-13 text-fg-muted">{t("network.access.headers.sentNone")}</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {policy.headers.map((line) => (
                <li key={line.name} className="min-w-0">
                  <Mono truncate title={`${line.name}: ${line.value}`}>{`${line.name}: ${line.value}`}</Mono>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

/**
 * An authentication service every request is asked about (nginx's auth_request): oauth2-proxy,
 * authentik, or any service that answers 2xx for a signed-in visitor and 401 for one who is not.
 * It is an address nginx will call from this machine, so it is the server's to name.
 */
function ForwardAuthCard({ form, t }: { form: ReturnType<typeof useAccessForm>; t: T }) {
  const presets = useQuery(forwardAuthPresetsQuery());
  const { draft, edit } = form;
  const [chosen, setChosen] = useState("");
  const preset = presets.data?.presets.find((candidate) => candidate.name === chosen);
  return (
    <Card title={t("network.access.forward.title")} description={t("network.access.forward.description")}>
      <div className="flex flex-col gap-4">
        <Field label={t("network.access.forward.preset")} description={t("network.access.forward.presetHelp")} nativeLabel={false}>
          <Select
            aria-label={t("network.access.forward.preset")}
            value={chosen}
            onValueChange={(name) => {
              setChosen(name);
              const found = presets.data?.presets.find((candidate) => candidate.name === name);
              if (found === undefined) return;
              edit("forward_url", found.url);
              edit("forward_sign_in", found.sign_in);
              edit("forward_headers", found.headers.join("\n"));
            }}
            options={[{ value: "", label: t("network.access.forward.custom") }, ...(presets.data?.presets ?? []).map((item) => ({ value: item.name, label: item.name }))]}
          />
        </Field>
        {preset !== undefined ? <Notice title={preset.name}>{preset.note}</Notice> : null}
        <Field label={t("network.access.forward.url")} description={t("network.access.forward.urlHelp")} error={form.errorOf("forward_url")}>
          <Input mono value={draft.forward_url} onValueChange={(value: string) => edit("forward_url", value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("network.access.forward.signIn")} description={t("network.access.forward.signInHelp")}>
            <Input mono value={draft.forward_sign_in} onValueChange={(value: string) => edit("forward_sign_in", value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
          <Field label={t("network.access.forward.headers")} description={t("network.access.forward.headersHelp")}>
            <Textarea mono rows={3} wrap="off" value={draft.forward_headers} onChange={(event) => edit("forward_headers", event.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
        </div>
      </div>
    </Card>
  );
}

/**
 * HTTP/3 (QUIC) beside HTTPS. The server says whether it can (nginx 1.25 or later with the module)
 * and whether the firewall lets UDP 443 in: a browser that cannot reach it falls back to HTTP/2,
 * so a closed port costs a failed attempt, not the site.
 */
function Http3Card({ form, domain, t }: { form: ReturnType<typeof useAccessForm>; domain: string; t: T }) {
  const support = useQuery(appHttp3Query(domain));
  const { draft, edit } = form;
  const data = support.data;
  const cannot = data !== undefined && !data.available;
  return (
    <Card title={t("network.access.http3.title")} description={t("network.access.http3.description")} loading={support.isPending}>
      <div className="flex flex-col gap-3">
        <Checkbox
          label={t("network.access.http3.enable")}
          description={t("network.access.http3.enableHelp")}
          checked={draft.http3}
          disabled={cannot && !draft.http3}
          onCheckedChange={(checked) => edit("http3", checked)}
        />
        {form.errorOf("http3") !== undefined ? <p className="text-13 text-fail">{form.errorOf("http3")}</p> : null}
        {support.isError ? <ErrorBlock compact error={support.error} title={t("network.access.http3.readFailed")} /> : null}
        {data !== undefined && !data.available ? (
          <Notice tone="warning" title={data.problem}>
            {data.hint}
          </Notice>
        ) : null}
        {data !== undefined && data.available && data.udp_allowed === false ? (
          <Notice tone="warning" title={t("network.access.http3.udpClosed")}>
            {t("network.access.http3.udpClosedHelp")}
            {data.firewall_hint !== "" ? (
              <>
                {" "}
                <Mono>{data.firewall_hint}</Mono>
              </>
            ) : null}
          </Notice>
        ) : null}
      </div>
    </Card>
  );
}

function AccessForm({ domain, policy }: { domain: string; policy: AppPolicy }) {
  const t = useT();
  const { node } = useNode();
  const org = useOrganization();
  const form = useAccessForm(domain, policy);
  const bar = useSaveBar([form.part], t("network.access.notSaved", { domain }));

  return (
    <div className="flex flex-col gap-4">
      {!policy.applicable ? (
        <Notice tone="warning" title={t("network.access.notApplicable")}>
          {policy.reason}
        </Notice>
      ) : null}
      <PasswordCard form={form} policy={policy} t={t} />
      <AccessUsers domain={domain} policy={policy} />
      <AddressesCard form={form} t={t} />
      <PrivateCard domain={domain} />
      {policy.webserver === "nginx" ? <ClientCertsCard form={form} domain={domain} /> : null}
      <VisitorCard form={form} policy={policy} t={t} />
      {policy.webserver === "nginx" ? <LimitsCard form={form} t={t} /> : null}
      <HeadersCard form={form} policy={policy} t={t} />
      {policy.webserver === "nginx" ? <Http3Card form={form} domain={domain} t={t} /> : null}
      {policy.webserver === "nginx" ? <WafCard form={form} domain={domain} /> : null}
      {policy.webserver === "nginx" && org.can("server.manage") ? <ForwardAuthCard form={form} t={t} /> : null}
      <RoutesCard domain={domain} routes={form.draft.routes} error={form.errorOf("routes")} onChange={(routes) => form.edit("routes", routes)} />
      {form.failure !== null ? <ErrorBlock live compact error={form.failure} title={t("network.access.notSaved", { domain })} /> : null}
      <CommandHint command={`noust app policy show ${domain}`} label={t("appSettings.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
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
    </div>
  );
}

/**
 * Access (app settings): a password and the accounts that can sign in, the addresses allowed
 * and refused, the header that names the visitor behind a CDN, rate and connection limits,
 * and the response headers. One form, one save: the server checks the whole of it, has the
 * web server test the new site, and puts the old one back if it refuses. A list that would
 * refuse the operator's own address is asked about before it is kept.
 */
export function AccessSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("network.access.nav", domain);
  const policy = useQuery(appPolicyQuery(domain));

  return (
    <Section title={t("network.access.title")} description={t("network.access.description", { domain })}>
      {policy.isPending ? (
        <Skeletons />
      ) : policy.isError ? (
        <ErrorBlock error={policy.error} title={t("network.access.readFailed", { domain })} onRetry={() => void policy.refetch()} retrying={policy.isRefetching} />
      ) : (
        <AccessForm domain={domain} policy={policy.data} />
      )}
    </Section>
  );
}
