import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { appWafEventsQuery, appWafQuery, networkKeys } from "../../../api/queries/network";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { RowList } from "../../../components/ui/RowList";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { useOrganization } from "../../teams/useOrganization";
import { listOf } from "./access";
import type { AccessForm } from "./useAccessForm";
import { SETTINGS_ICONS } from "./objectIcons";

/**
 * The web application firewall (ModSecurity with the OWASP Core Rule Set): off, writing down what
 * the rules see, or refusing what they score too high. A site starts in detect mode: what the rules
 * saw lately is listed with the rules that matched most, and one that is wrong for this application
 * is added to the rules left out, which is saved with the rest of the form.
 */
export function WafCard({ form, domain }: { form: AccessForm; domain: string }) {
  const t = useT();
  const org = useOrganization();
  const queryClient = useQueryClient();
  const support = useQuery(appWafQuery(domain));
  const { draft, edit } = form;
  const data = support.data;
  const on = draft.waf !== "off";
  const events = useQuery({ ...appWafEventsQuery(domain), enabled: on && data?.available === true });

  const install = useMutation({
    mutationFn: () => request("post", "/api/waf/install"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: networkKeys.appWaf(domain) });
      announce(t("network.access.waf.installedToast"));
    },
  });

  const unavailable = data !== undefined && !data.available;
  const leftOut = new Set(listOf(draft.waf_exclude));
  const leaveOut = (rule: string): void => {
    edit("waf_exclude", [...listOf(draft.waf_exclude), rule].join("\n"));
  };

  return (
    <Card title={t("network.access.waf.title")} icon={SETTINGS_ICONS.firewall} description={t("network.access.waf.description")} loading={support.isPending}>
      <div className="flex flex-col gap-4">
        {support.isError ? <ErrorBlock compact error={support.error} title={t("network.access.waf.readFailed")} /> : null}
        <ChoiceCards
          legend={t("network.access.waf.title")}
          options={[
            { value: "off", label: t("network.access.waf.off"), description: t("network.access.waf.offHelp") },
            { value: "detect", label: t("network.access.waf.detect"), description: t("network.access.waf.detectHelp") },
            { value: "block", label: t("network.access.waf.block"), description: t("network.access.waf.blockHelp") },
          ]}
          value={draft.waf}
          onValueChange={(value) => edit("waf", value)}
          error={form.errorOf("waf")}
        />
        {unavailable ? (
          <Notice
            tone="warning"
            title={data.problem}
            action={
              data.installable && org.can("server.manage") ? (
                <Button icon={<SETTINGS_ICONS.install aria-hidden="true" />} loading={install.isPending} onClick={() => install.mutate()}>
                  {t("network.access.waf.install")}
                </Button>
              ) : undefined
            }
          >
            {data.hint}
          </Notice>
        ) : null}
        {install.isError ? <ErrorBlock live compact error={install.error} title={t("network.access.waf.installFailed")} /> : null}
        {on ? (
          <Field label={t("network.access.waf.exclude")} description={t("network.access.waf.excludeHelp")} error={form.errorOf("waf_exclude")}>
            <Textarea mono rows={3} wrap="off" value={draft.waf_exclude} onChange={(event) => edit("waf_exclude", event.target.value)} autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        {on && events.isError ? <ErrorBlock compact error={events.error} title={t("network.access.waf.eventsFailed")} /> : null}
        {on && events.data !== undefined ? (
          events.data.events.length === 0 ? (
            <p className="text-13 text-fg-muted">{t("network.access.waf.noEvents")}</p>
          ) : (
            <div className="flex flex-col gap-3">
              <RowList aria-label={t("network.access.waf.recent")}>
                {events.data.events.slice(0, 10).map((event, index) => (
                  <RowList.Item key={`${event.time}-${String(index)}`}>
                    <div className="flex flex-wrap items-center gap-2 text-13">
                      <Mono>{`${event.method} ${event.uri}`}</Mono>
                      <span className="text-fg-muted">{event.client}</span>
                      {event.blocked ? <StatusPill state="failed" size="sm" label={t("network.access.waf.blocked")} /> : null}
                      {event.would_block ? <StatusPill state="warning" size="sm" label={t("network.access.waf.wouldBlock")} /> : null}
                      <span className="text-fg-muted">{event.rules.filter((rule) => rule.id !== "949110").map((rule) => rule.id).join(", ")}</span>
                    </div>
                  </RowList.Item>
                ))}
              </RowList>
              {events.data.noisy.length > 0 ? (
                <div className="flex flex-col gap-2">
                  <p className="text-13 text-fg-muted">{t("network.access.waf.noisy")}</p>
                  <ul aria-label={t("network.access.waf.noisyList")} className="flex flex-col gap-1">
                    {events.data.noisy.slice(0, 6).map((rule) => (
                      <li key={rule.id} className="flex flex-wrap items-center gap-2 text-13">
                        <Mono>{rule.id}</Mono>
                        <span>{rule.message}</span>
                        <Badge>{t("network.access.waf.times", { count: rule.count })}</Badge>
                        {leftOut.has(rule.id) ? (
                          <span className="text-fg-muted">{t("network.access.waf.leftOut")}</span>
                        ) : (
                          <Button size="sm" onClick={() => leaveOut(rule.id)}>
                            {t("network.access.waf.leaveOut", { rule: rule.id })}
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          )
        ) : null}
      </div>
    </Card>
  );
}
