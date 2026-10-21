import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { appPolicyQuery, forwardAuthPresetsQuery } from "../../../api/queries/network";
import type { AppPolicy } from "../../../api/queries/network";
import { CommandHint } from "../../../components/page/CommandHint";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useOrganization } from "../../teams/useOrganization";
import { AccessUsers } from "./AccessUsers";
import { PrivateCard } from "./PrivateCard";
import { RoutesCard } from "./RoutesCard";
import { useSaveBar } from "./formParts";
import { useSettingsApp, subsectionIcon, useSubsectionTitle } from "./settingsApp";
import { AccessQuestions, useAccessForm } from "./useAccessForm";
import type { AccessForm } from "./useAccessForm";
import { SETTINGS_ICONS } from "./objectIcons";

function PasswordCard({ form, policy, t }: { form: AccessForm; policy: AppPolicy; t: T }) {
  const { draft, edit } = form;
  const noAccounts = policy.users.length === 0;
  return (
    <Card title={t("network.access.password.title")} icon={SETTINGS_ICONS.account} description={t("network.access.password.description")}>
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

function AddressesCard({ form, t }: { form: AccessForm; t: T }) {
  const { draft, edit } = form;
  return (
    <Card title={t("network.access.addresses.title")} icon={SETTINGS_ICONS.addresses} description={t("network.access.addresses.description")}>
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

/** Which address the lists above see when a CDN or a proxy stands in front of the site: folded until one is set. */
function VisitorFold({ form, policy, t }: { form: AccessForm; policy: AppPolicy; t: T }) {
  const { draft, edit } = form;
  const source = policy.real_ip_source === "fetched" ? t("network.access.visitor.sourceFetched") : t("network.access.visitor.sourceBuiltIn");
  const summary = draft.real_ip === "off" ? t("network.access.visitor.off") : draft.real_ip === "cloudflare" ? t("network.access.visitor.cloudflare") : t("network.access.visitor.custom");
  return (
    <Disclosure variant="card" label={t("network.access.visitor.title")} summary={summary} defaultOpen={policy.policy.real_ip === "cloudflare" || policy.policy.real_ip === "custom"}>
      <div className="flex flex-col gap-4">
        <p className="text-13 text-pretty text-fg-muted">{t("network.access.visitor.description")}</p>
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
    </Disclosure>
  );
}

/**
 * An authentication service every request is asked about (nginx's auth_request): oauth2-proxy,
 * authentik, or any service that answers 2xx for a signed-in visitor and 401 for one who is not.
 * It is an address nginx will call from this machine, so it is the server's to name. Folded until
 * one is set.
 */
function ForwardAuthFold({ form, t }: { form: AccessForm; t: T }) {
  const presets = useQuery(forwardAuthPresetsQuery());
  const { draft, edit } = form;
  const [chosen, setChosen] = useState("");
  const [openAtStart] = useState(() => draft.forward_url.trim() !== "");
  const preset = presets.data?.presets.find((candidate) => candidate.name === chosen);
  return (
    <Disclosure
      variant="card"
      label={t("network.access.forward.title")}
      summary={draft.forward_url.trim() === "" ? t("network.access.forward.none") : draft.forward_url.trim()}
      defaultOpen={openAtStart}
    >
      <div className="flex flex-col gap-4">
        <p className="text-13 text-pretty text-fg-muted">{t("network.access.forward.description")}</p>
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
        {preset !== undefined ? <InlineMessage tone="info">{preset.note}</InlineMessage> : null}
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
    </Disclosure>
  );
}

function PolicyForm({ domain, policy }: { domain: string; policy: AppPolicy }) {
  const t = useT();
  const org = useOrganization();
  const form = useAccessForm(domain, policy);
  const bar = useSaveBar([form.part], t("network.access.notSaved", { domain }));
  const nginx = policy.webserver === "nginx";

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
      <VisitorFold form={form} policy={policy} t={t} />
      <PrivateCard domain={domain} />
      {nginx && org.can("server.manage") ? <ForwardAuthFold form={form} t={t} /> : null}
      <RoutesCard domain={domain} routes={form.draft.routes} error={form.errorOf("routes")} onChange={(routes) => form.edit("routes", routes)} />
      {form.failure !== null ? <ErrorBlock live compact error={form.failure} title={t("network.access.notSaved", { domain })} /> : null}
      <CommandHint command={`noust app policy show ${domain}`} label={t("appSettings.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
      <AccessQuestions form={form} domain={domain} />
    </div>
  );
}

/**
 * Access (app settings): who gets in. A password and the accounts that can sign in, the addresses
 * allowed and refused, the header that names the visitor behind a CDN, a private network only, an
 * authentication service in front of the site, and what happens at prefixes of it. One form, one
 * save: the server checks the whole of it, has the web server test the new site, and puts the old
 * one back if it refuses. A list that would refuse the operator's own address is asked about before
 * it is kept. What is limited and sent back (rates, headers, the firewall, client certificates) is
 * Protection.
 */
export function AccessSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("network.access.nav", domain);
  const policy = useQuery(appPolicyQuery(domain));

  return (
    <Section title={t("network.access.title")} {...subsectionIcon("network.access.nav")} description={t("network.access.description", { domain })}>
      {policy.isPending ? (
        <LoadingRegion label={t("network.access.loading")}>
          <SkeletonCard lines={3} />
        </LoadingRegion>
      ) : policy.isError ? (
        <ErrorBlock error={policy.error} title={t("network.access.readFailed", { domain })} onRetry={() => void policy.refetch()} retrying={policy.isRefetching} />
      ) : (
        <PolicyForm domain={domain} policy={policy.data} />
      )}
    </Section>
  );
}
