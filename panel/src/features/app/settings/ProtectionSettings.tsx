import { useQuery } from "@tanstack/react-query";

import { appHttp3Query, appPolicyQuery } from "../../../api/queries/network";
import type { AppPolicy } from "../../../api/queries/network";
import { CommandHint } from "../../../components/page/CommandHint";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field, GroupError } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ClientCertsCard } from "./ClientCertsCard";
import { WafCard } from "./WafCard";
import { useSaveBar } from "./formParts";
import { useSettingsApp, subsectionIcon, useSubsectionTitle } from "./settingsApp";
import { AccessQuestions, useAccessForm } from "./useAccessForm";
import type { AccessForm } from "./useAccessForm";
import { SETTINGS_ICONS } from "./objectIcons";

function LimitsCard({ form, t }: { form: AccessForm; t: T }) {
  const { draft, edit } = form;
  return (
    <Card title={t("network.access.limits.title")} icon={SETTINGS_ICONS.limits} description={t("network.access.limits.description")}>
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

function HeadersCard({ form, policy, t }: { form: AccessForm; policy: AppPolicy; t: T }) {
  const { draft, edit } = form;
  const customised = draft.headers.trim() !== "" || draft.hidden_headers.trim() !== "";
  return (
    <Card title={t("network.access.headers.title")} icon={SETTINGS_ICONS.headers} description={t("network.access.headers.description")}>
      <div className="flex flex-col gap-4">
        <Checkbox
          label={t("network.access.headers.noindex")}
          description={t("network.access.headers.noindexHelp")}
          checked={draft.noindex}
          onCheckedChange={(checked) => edit("noindex", checked)}
        />
        <Checkbox
          label={t("network.access.headers.forceHttps")}
          description={t("network.access.headers.forceHttpsHelp")}
          checked={draft.force_https}
          onCheckedChange={(checked) => edit("force_https", checked)}
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
        <Disclosure label={t("network.access.headers.more")} defaultOpen={customised}>
          <div className="flex flex-col gap-4">
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
        </Disclosure>
      </div>
    </Card>
  );
}

/**
 * HTTP/3 (QUIC) beside HTTPS. The server says whether it can (nginx 1.25 or later with the module)
 * and whether the firewall lets UDP 443 in: a browser that cannot reach it falls back to HTTP/2,
 * so a closed port costs a failed attempt, not the site.
 */
function Http3Card({ form, domain, t }: { form: AccessForm; domain: string; t: T }) {
  const support = useQuery(appHttp3Query(domain));
  const { draft, edit } = form;
  const data = support.data;
  const cannot = data !== undefined && !data.available;
  const refused = form.errorOf("http3");
  // What stands in the way, one at a time: HTTP/3 cannot be served, or its UDP port is closed.
  const problem =
    data === undefined
      ? null
      : !data.available
        ? { title: data.problem, body: data.hint }
        : data.udp_allowed === false
          ? {
              title: t("network.access.http3.udpClosed"),
              body: (
                <>
                  {t("network.access.http3.udpClosedHelp")}
                  {data.firewall_hint !== "" ? (
                    <>
                      {" "}
                      <Mono>{data.firewall_hint}</Mono>
                    </>
                  ) : null}
                </>
              ),
            }
          : null;
  return (
    <Card title={t("network.access.http3.title")} icon={SETTINGS_ICONS.http3} description={t("network.access.http3.description")} loading={support.isPending}>
      <div className="flex flex-col gap-3">
        <Checkbox
          label={t("network.access.http3.enable")}
          description={t("network.access.http3.enableHelp")}
          checked={draft.http3}
          disabled={cannot && !draft.http3}
          onCheckedChange={(checked) => edit("http3", checked)}
        />
        {refused !== undefined ? <GroupError id="http3-refused">{refused}</GroupError> : null}
        {support.isError ? <ErrorBlock compact error={support.error} title={t("network.access.http3.readFailed")} /> : null}
        {problem !== null ? (
          <Notice tone="warning" title={problem.title}>
            {problem.body}
          </Notice>
        ) : null}
      </div>
    </Card>
  );
}

function ProtectionForm({ domain, policy }: { domain: string; policy: AppPolicy }) {
  const t = useT();
  const form = useAccessForm(domain, policy);
  const bar = useSaveBar([form.part], t("network.protection.notSaved", { domain }));
  const nginx = policy.webserver === "nginx";

  return (
    <div className="flex flex-col gap-4">
      {nginx ? <LimitsCard form={form} t={t} /> : null}
      <HeadersCard form={form} policy={policy} t={t} />
      {nginx ? <Http3Card form={form} domain={domain} t={t} /> : null}
      {nginx ? <WafCard form={form} domain={domain} /> : null}
      {nginx ? <ClientCertsCard form={form} domain={domain} /> : null}
      {form.failure !== null ? <ErrorBlock live compact error={form.failure} title={t("network.protection.notSaved", { domain })} /> : null}
      <CommandHint command={`noust app policy show ${domain}`} label={t("appSettings.fromTerminal")} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
      <AccessQuestions form={form} domain={domain} />
    </div>
  );
}

/**
 * Protection (app settings): what the site limits and sends back. Rate and connection limits, the
 * response headers, HTTP/3, the web application firewall and the client certificates a visitor must
 * present. It shares the policy Access shows, and one form and one save of its own: the server checks
 * the whole of it, has the web server test the new site, and puts the old one back if it refuses.
 */
export function ProtectionSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("network.protection.nav", domain);
  const policy = useQuery(appPolicyQuery(domain));

  return (
    <Section title={t("network.protection.title")} {...subsectionIcon("network.protection.nav")} description={t("network.protection.description", { domain })}>
      {policy.isPending ? (
        <LoadingRegion label={t("network.protection.loading")}>
          <SkeletonCard lines={3} />
        </LoadingRegion>
      ) : policy.isError ? (
        <ErrorBlock error={policy.error} title={t("network.protection.readFailed", { domain })} onRetry={() => void policy.refetch()} retrying={policy.isRefetching} />
      ) : (
        <ProtectionForm domain={domain} policy={policy.data} />
      )}
    </Section>
  );
}
