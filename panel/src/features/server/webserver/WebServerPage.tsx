/**
 * Web server (4.1, item 105): the one that answers on ports 80 and 443 and what is behind each of
 * the two, which one new applications are put behind (and why that is only about new ones), and
 * the way to move the applications that are already there. nginx or Apache, never both on the
 * same ports; a move says what it would do before it asks for anything.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { saveWebserver, webserverQuery } from "../../../api/queries/config";
import { configKeys } from "../../../api/queries/config";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { ServerQueryState } from "../errors";
import { TabToolbar } from "../TabToolbar";
import { display, hasSomethingToMove, movingFrom, otherThan } from "./data";
import { MoveDialog } from "./MoveDialog";
import { WEBSERVER_CAPABILITY, webserverKeys, webserverStatusQuery } from "./queries";
import type { WebServerName, WebserverStatus } from "./queries";

type ServerRow = WebserverStatus["servers"][string];

/** One of the two web servers: whether it answers, its version and what is behind it. */
function ServerCard({ t, name, row, holds }: { t: T; name: WebServerName; row: ServerRow | undefined; holds: boolean }) {
  const installed = row?.installed ?? false;
  const serving = row?.running ?? false;
  const state = serving ? "running" : "stopped";
  return (
    <Card
      level={2}
      title={display(name)}
      icon={OBJECT_ICONS.site}
      padding="sm"
      status={
        <StatusPill
          state={state}
          size="sm"
          label={!installed ? t("serverWeb.state.notInstalled") : serving ? t("serverWeb.state.serving") : t("serverWeb.state.stopped")}
        />
      }
      description={holds ? t("serverWeb.holdsPorts") : undefined}
    >
      {row !== undefined && installed ? (
        <KeyValueList
          items={[
            { label: t("serverWeb.version"), value: row.version ?? null, copy: false },
            { label: t("serverWeb.applications"), value: String(row.applications), mono: false, copy: false },
            { label: t("serverWeb.sites"), value: String(row.sites), mono: false, copy: false },
            { label: t("serverWeb.atBoot"), value: row.starts_at_boot ? t("common.words.yes") : t("common.words.no"), mono: false, copy: false },
          ]}
        />
      ) : (
        <p className="text-13 text-fg-muted">{t("serverWeb.notInstalled", { name: display(name) })}</p>
      )}
    </Card>
  );
}

/** Which one new applications are put behind. It moves none of the ones already here, and is refused while they are behind the other. */
function NewApplications({ t, status }: { t: T; status: WebserverStatus }) {
  const queryClient = useQueryClient();
  const setting = useQuery(webserverQuery());
  const choose = useMutation({
    mutationFn: (value: string) => saveWebserver({ webserver: value }),
    onSuccess: (_saved, value) => {
      void queryClient.invalidateQueries({ queryKey: configKeys.section("webserver") });
      void queryClient.invalidateQueries({ queryKey: webserverKeys.status });
      toast.success(t("serverWeb.newApps.saved", { name: display(value) }));
    },
  });
  const current = setting.data?.webserver ?? status.webserver;
  return (
    <Card level={2} title={t("serverWeb.newApps.title")} description={t("serverWeb.newApps.description")} padding="sm">
      <div className="flex min-w-0 flex-col gap-3">
        <Field label={t("serverWeb.newApps.label")} nativeLabel={false} className="max-w-xs">
          <Select
            value={current}
            onValueChange={(value) => {
              if (value !== current) choose.mutate(value);
            }}
            options={[
              { value: "nginx", label: "nginx" },
              { value: "apache", label: "Apache" },
            ]}
          />
        </Field>
        {choose.isError ? <ErrorBlock live compact error={choose.error} title={t("serverWeb.newApps.failed")} /> : null}
      </div>
    </Card>
  );
}

function WebServer() {
  const t = useT();
  const status = useQuery(webserverStatusQuery());
  const [moving, setMoving] = useState<WebServerName | null>(null);
  return (
    <ServerQueryState query={status} label={t("serverWeb.label")} skeleton={<SkeletonCard lines={4} />}>
      {(data) => {
        const from = movingFrom(data);
        const to = otherThan(from);
        return (
          <div className="flex min-w-0 flex-col gap-6">
            <TabToolbar
              summary={t.rich("serverWeb.summary", { name: <Mono key="name">{display(from)}</Mono> })}
              {...(hasSomethingToMove(data)
                ? {
                    actions: (
                      <Button variant="primary" onClick={() => setMoving(to)}>
                        {t("serverWeb.moveTo", { target: display(to) })}
                      </Button>
                    ),
                  }
                : {})}
            />
            <div className="grid min-w-0 items-start gap-6 md:grid-cols-2">
              <ServerCard t={t} name="nginx" row={data.servers["nginx"]} holds={data.holds_ports === "nginx"} />
              <ServerCard t={t} name="apache" row={data.servers["apache"]} holds={data.holds_ports === "apache"} />
            </div>
            <NewApplications t={t} status={data} />
            <CommandHint command="noust webserver status" label={t("common.words.fromTerminal")} />
            {moving !== null ? <MoveDialog to={moving} onClose={() => setMoving(null)} /> : null}
          </div>
        );
      }}
    </ServerQueryState>
  );
}

/** The Web server section. */
export function WebServerPage() {
  return (
    <NodeCapabilityGate capability={WEBSERVER_CAPABILITY}>
      <WebServer />
    </NodeCapabilityGate>
  );
}
