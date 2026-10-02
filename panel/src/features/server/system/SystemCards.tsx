/**
 * The four facts of the System tab, each a card with its own actions: the clock, the host name,
 * the operating system and where it is in its support, and the power (reboot and shutdown, now
 * or later, and what is scheduled).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Switch } from "../../../components/ui/Switch";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDate, formatDuration, formatLoad, formatMoment, parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock, explainServerError } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { dayOf, identityQuery, serverKeys, summaryQuery, timeQuery } from "../queries";
import type { Identity } from "../queries";
import { TimeZoneField } from "../TimeZoneField";

function Loading({ title }: { title: string }) {
  const t = useT();
  return (
    <Card level={2} title={title} padding="sm">
      <div aria-busy="true">
        <span className="sr-only">{t("server.system.loading")}</span>
        <KeyValueListSkeleton rows={4} />
      </div>
    </Card>
  );
}

export function ClockCard() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const clock = useQuery(timeQuery());
  const [zoneOpen, setZoneOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);
  const [zone, setZone] = useState("");
  const [install, setInstall] = useState(true);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: serverKeys.time });
    void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
  };
  const ntp = useMutation({
    mutationFn: (enabled: boolean) => request("put", "/api/server/time", { body: { ntp: enabled, install_ntp: false } }),
    onSuccess: refresh,
    onError: (error) => reportActionError(t("server.clock.ntpFailed"), explainServerError(t, error, node)),
  });
  if (clock.isError && clock.data === undefined) {
    return (
      <Card level={2} title={t("server.clock.title")} padding="sm">
        <ServerErrorBlock compact error={clock.error} title={t("server.clock.loadFailed")} onRetry={() => void clock.refetch()} />
      </Card>
    );
  }
  if (clock.data === undefined) return <Loading title={t("server.clock.title")} />;
  const data = clock.data;
  const items: KeyValueItem[] = [
    { label: t("server.clock.localTime"), value: data.local_time },
    { label: t("server.clock.timezone"), value: data.timezone },
    {
      label: t("server.clock.state"),
      value: (
        <StatusPill
          state={data.synchronized ? "running" : "warning"}
          label={data.synchronized ? t("server.clock.synced") : t("server.clock.unsynced")}
          appearance="inline"
          size="sm"
        />
      ),
      mono: false,
      copy: false,
    },
    ...(data.offset_seconds != null
      ? [{ label: t("server.clock.offset"), value: formatDuration(Math.abs(data.offset_seconds), t.locale), mono: false, copy: false as const }]
      : []),
  ];
  return (
    <Card level={2}
      title={t("server.clock.title")}
      padding="sm"
      actions={
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setZone(data.timezone);
            setZoneOpen(true);
          }}
        >
          {t("server.clock.changeZone")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <KeyValueList items={items} />
        <Switch
          label={t("server.clock.ntpLabel")}
          description={data.ntp_supported ? t("server.clock.ntpDescription") : t("server.clock.ntpMissing")}
          checked={data.ntp_enabled}
          disabled={ntp.isPending}
          onCheckedChange={(checked) => {
            if (checked && !data.ntp_supported) setInstallOpen(true);
            else ntp.mutate(checked);
          }}
        />
      </div>
      {zoneOpen ? (
        <ActionDialog
          title={t("server.clock.zoneTitle")}
          description={t("server.clock.zoneDescription")}
          actionLabel={t("server.clock.zoneAction")}
          onClose={() => setZoneOpen(false)}
          onConfirm={async () => {
            const result = await request("put", "/api/server/time", { body: { timezone: zone.trim(), install_ntp: false } });
            refresh();
            toast.success(t("server.clock.zoneChanged", { zone: result.time.timezone }), {
              ...((result.moved_timers ?? []).length > 0 ? { description: t("server.clock.timersMoved", { count: (result.moved_timers ?? []).length }) } : {}),
            });
          }}
        >
          <TimeZoneField value={zone} onValueChange={setZone} current={data.timezone} label={t("server.clock.zoneLabel")} />
        </ActionDialog>
      ) : null}
      {installOpen ? (
        <ActionDialog
          title={t("server.clock.installTitle")}
          description={t("server.clock.installDescription")}
          actionLabel={t("server.clock.installAction")}
          onClose={() => setInstallOpen(false)}
          onConfirm={async () => {
            await request("put", "/api/server/time", { body: { ntp: true, install_ntp: install } });
            refresh();
          }}
        >
          <Checkbox label={t("server.clock.installChrony")} description={t("server.clock.installChronyHelp")} checked={install} onCheckedChange={setInstall} />
        </ActionDialog>
      ) : null}
    </Card>
  );
}

export function HostnameCard() {
  const t = useT();
  const queryClient = useQueryClient();
  const identity = useQuery(identityQuery());
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [keep, setKeep] = useState(true);
  if (identity.isError && identity.data === undefined) {
    return (
      <Card level={2} title={t("server.hostname.title")} padding="sm">
        <ServerErrorBlock compact error={identity.error} title={t("server.hostname.loadFailed")} onRetry={() => void identity.refetch()} />
      </Card>
    );
  }
  if (identity.data === undefined) return <Loading title={t("server.hostname.title")} />;
  const host = identity.data.hostname;
  const items: KeyValueItem[] = [
    { label: t("server.hostname.hostname"), value: host.hostname },
    ...(host.static !== host.hostname ? [{ label: t("server.hostname.static"), value: host.static }] : []),
    ...(host.pretty ? [{ label: t("server.hostname.pretty"), value: host.pretty, mono: false }] : []),
    { label: t("server.hostname.machineId"), value: host.machine_id },
    ...(host.chassis ? [{ label: t("server.hostname.chassis"), value: host.chassis }] : []),
  ];
  return (
    <Card level={2}
      title={t("server.hostname.title")}
      padding="sm"
      actions={
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setName(host.hostname);
            setOpen(true);
          }}
        >
          {t("server.hostname.rename")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <KeyValueList items={items} />
        {host.cloud_init_resets ? <Notice tone="warning">{t("server.hostname.cloudInit")}</Notice> : null}
      </div>
      {open ? (
        <ActionDialog
          title={t("server.hostname.renameTitle")}
          description={t("server.hostname.renameDescription")}
          actionLabel={t("server.hostname.renameAction")}
          onClose={() => setOpen(false)}
          onConfirm={async () => {
            const result = await request("put", "/api/server/identity/hostname", { body: { hostname: name.trim(), keep_against_cloud_init: host.cloud_init && keep } });
            void queryClient.invalidateQueries({ queryKey: serverKeys.identity });
            void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
            toast.success(t("server.hostname.renamed", { name: name.trim() }), { description: result.steps.join(" ") });
          }}
        >
          <Field label={t("server.hostname.newName")} description={t("server.hostname.newNameHelp")}>
            <Input mono value={name} onValueChange={(value: string) => setName(value)} autoComplete="off" spellCheck={false} />
          </Field>
          {host.cloud_init ? <Checkbox label={t("server.hostname.keep")} description={t("server.hostname.keepHelp")} checked={keep} onCheckedChange={setKeep} /> : null}
        </ActionDialog>
      ) : null}
    </Card>
  );
}

function supportLine(t: T, identity: Identity): string {
  const eol = identity.eol;
  const date = dayOf(eol.end_date) ?? parseTimestamp(eol.end_date ?? null);
  const shown = date ? formatDate(date, {}, t.locale) : (eol.end_date ?? "");
  if (eol.status === "expired") return shown !== "" ? t("server.overview.supportEnded", { date: shown }) : t("server.overview.supportEndedNoDate");
  if (eol.status === "warn" && eol.days_left != null) return t("server.overview.supportEnding", { count: eol.days_left, date: shown });
  if (eol.status === "ok" && shown !== "") return t("server.overview.supportUntil", { date: shown });
  return t("server.overview.supportUnknown");
}

export function SystemCard() {
  const t = useT();
  const identity = useQuery(identityQuery());
  if (identity.isError && identity.data === undefined) {
    return (
      <Card level={2} title={t("server.os.title")} padding="sm">
        <ServerErrorBlock compact error={identity.error} title={t("server.os.loadFailed")} onRetry={() => void identity.refetch()} />
      </Card>
    );
  }
  if (identity.data === undefined) return <Loading title={t("server.os.title")} />;
  const data = identity.data;
  const eolState = data.eol.status === "expired" ? "failed" : data.eol.status === "warn" ? "warning" : null;
  const items: KeyValueItem[] = [
    { label: t("server.os.name"), value: data.os_name, mono: false },
    {
      label: t("server.os.support"),
      value: eolState !== null ? <StatusPill state={eolState} label={supportLine(t, data)} appearance="inline" size="sm" /> : supportLine(t, data),
      mono: false,
      copy: false,
    },
    { label: t("server.os.kernel"), value: data.kernel },
    { label: t("server.os.architecture"), value: data.architecture },
    ...(data.container ? [{ label: t("server.os.container"), value: data.container }] : []),
    {
      label: t("server.os.booted"),
      value: data.booted_at ? <RelativeTime value={data.booted_at} /> : null,
      mono: false,
      copy: false,
    },
    {
      label: t("server.os.load"),
      value: t("server.os.loadValue", { load: data.load.map((value) => formatLoad(value, t.locale)).join(" "), cpus: data.cpu_count }),
      mono: false,
      copy: false,
    },
  ];
  return (
    <Card level={2} title={t("server.os.title")} padding="sm">
      <KeyValueList items={items} />
    </Card>
  );
}

export function PowerCard() {
  const t = useT();
  const { node } = useNode();
  const power = usePowerDialog();
  const queryClient = useQueryClient();
  const summary = useQuery(summaryQuery());
  const cancel = useMutation({
    mutationFn: () => request("delete", "/api/server/power/scheduled"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
    },
    onError: (error) => reportActionError(t("server.power.cancelFailed"), explainServerError(t, error, node)),
  });
  const scheduled = summary.data?.power.scheduled ?? null;
  const due = parseTimestamp(scheduled?.scheduled_for ?? null);
  return (
    <Card level={2} title={t("server.power.cardTitle")} description={t("server.power.cardDescription")} padding="sm">
      <div className="flex flex-col gap-4">
        {scheduled !== null ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-13 text-fg">
              {scheduled.action === "reboot"
                ? t("server.power.bannerReboot", { when: due ? formatMoment(due, t.locale) : scheduled.scheduled_for })
                : t("server.power.bannerShutdown", { when: due ? formatMoment(due, t.locale) : scheduled.scheduled_for })}
            </span>
            <Button size="sm" loading={cancel.isPending} onClick={() => cancel.mutate()}>
              {scheduled.action === "reboot" ? t("server.power.cancelReboot") : t("server.power.cancelShutdown")}
            </Button>
          </div>
        ) : (
          <p className="text-13 text-fg-muted">{t("server.overview.nothingScheduled")}</p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => power.open("reboot")}>{t("server.power.rebootEllipsis")}</Button>
          <Button variant="ghost" onClick={() => power.open("shutdown")}>
            {t("server.power.shutdownEllipsis")}
          </Button>
        </div>
      </div>
    </Card>
  );
}

