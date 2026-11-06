/**
 * This machine: what it is called and what time it lives in, what it is made of, and its power. Three slabs, each
 * saying what it is for before a word of it is read. What it was in 4.1 (four cards of a System page) is the same
 * facts and the same actions: the rename and the time zone are one press from the value they change, and the zone
 * says what depends on it, with the way to those pages.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Power, RotateCw } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import { machineQuery } from "../../../api/queries/system";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Can, ReadOnlyLine } from "../../../components/page/permissions";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Setting } from "../../../components/ui/Setting";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { textLinkClassName } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatDate, formatDuration, formatLoad, formatMoment, parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { explainServerError, useServerConfirm, ServerQueryState } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { dayOf, identityQuery, serverKeys, summaryQuery, timeQuery } from "../queries";
import type { Identity, ServerClock } from "../queries";
import { TimeZoneField } from "../TimeZoneField";

/** The slab's shape while its read is in flight, inside the loading region that waits for the three together. */
function SlabSkeleton({ title, rows }: { title: string; rows: number }) {
  return (
    <SurfaceSection title={title} loading flush={false}>
      <div className="flex flex-col gap-3">
        {Array.from({ length: rows }, (_, index) => (
          <Skeleton key={index} className="h-5 w-full" />
        ))}
      </div>
    </SurfaceSection>
  );
}

export function MachineSkeleton() {
  const t = useT();
  return (
    <LoadingRegion label={t("common.queryState.loading", { label: t("server.system.views.machine") })} className="flex min-w-0 flex-col gap-6">
      <SlabSkeleton title={t("server.machine.thisTitle")} rows={4} />
      <SlabSkeleton title={t("server.machine.hardwareTitle")} rows={5} />
      <SlabSkeleton title={t("server.power.cardTitle")} rows={2} />
    </LoadingRegion>
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

/** The name and the zone, each with the press that changes it beside the value. */
function ThisMachine({ clock, host }: { clock: ServerClock; host: Identity["hostname"] }) {
  const t = useT();
  const confirmation = useServerConfirm();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [zoneOpen, setZoneOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);
  const [zone, setZone] = useState("");
  const [name, setName] = useState("");
  const [install, setInstall] = useState(true);
  const [keep, setKeep] = useState(true);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: serverKeys.time });
    void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
  };
  const ntp = useMutation({
    mutationFn: (enabled: boolean) => request("put", "/api/server/time", { body: { ntp: enabled, install_ntp: false } }),
    onSuccess: refresh,
    onError: (error) => reportActionError(t("server.clock.ntpFailed"), explainServerError(t, error, node)),
  });
  const items: KeyValueItem[] = [
    {
      label: t("server.hostname.hostname"),
      value: (
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Mono className="text-12">{host.hostname}</Mono>
          <Can permission="server.manage">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setName(host.hostname);
                setRenameOpen(true);
              }}
            >
              {t("server.hostname.rename")}
            </Button>
          </Can>
        </span>
      ),
      mono: false,
      copy: host.hostname,
    },
    {
      label: t("server.clock.timezone"),
      value: (
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Mono className="text-12">{clock.timezone}</Mono>
          <Can permission="server.manage">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setZone(clock.timezone);
                setZoneOpen(true);
              }}
            >
              {t("server.clock.changeZone")}
            </Button>
          </Can>
        </span>
      ),
      mono: false,
      copy: false,
      hint: t.rich("server.machine.zoneUsedBy", {
        cron: (
          <Link key="cron" to="/cron" className={textLinkClassName("inline")}>
            {t("server.machine.zoneCron")}
          </Link>
        ),
        backups: (
          <Link key="backups" to="/backups" className={textLinkClassName("inline")}>
            {t("server.machine.zoneBackups")}
          </Link>
        ),
      }),
    },
    { label: t("server.clock.localTime"), value: clock.local_time },
    {
      label: t("server.clock.state"),
      value: (
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <StatusPill state={clock.synchronized ? "running" : "warning"} label={clock.synchronized ? t("server.clock.synced") : t("server.clock.unsynced")} appearance="inline" size="sm" />
          {clock.offset_seconds != null ? <span className="text-12 text-fg-muted">{t("server.machine.offset", { duration: formatDuration(Math.abs(clock.offset_seconds), t.locale) })}</span> : null}
        </span>
      ),
      mono: false,
      copy: false,
    },
  ];
  return (
    <SurfaceSection title={t("server.machine.thisTitle")} icon={OBJECT_ICONS.server} description={t("server.machine.thisDescription")}>
      <div className="flex min-w-0 flex-col gap-3">
        <KeyValueList items={items} />
        {host.cloud_init_resets ? <Notice tone="warning">{t("server.hostname.cloudInit")}</Notice> : null}
        <Can permission="server.manage">
          <Setting
            label={t("server.clock.ntpLabel")}
            help={clock.ntp_supported ? t("server.clock.ntpDescription") : t("server.clock.ntpMissing")}
            checked={clock.ntp_enabled}
            busy={ntp.isPending}
            onCheckedChange={(checked) => {
              if (checked && !clock.ntp_supported) setInstallOpen(true);
              else ntp.mutate(checked);
            }}
          />
        </Can>
      </div>
      {zoneOpen ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(open) => {
            if (!open) setZoneOpen(false);
          }}
          friction="simple"
          destructive={false}
          size="md"
          title={t("server.clock.zoneTitle")}
          description={t("server.clock.zoneDescription")}
          actionLabel={t("server.clock.zoneAction")}
          onConfirm={async () => {
            // design-exception: write-without-permission opened from the zone's button, drawn only for server.manage
            const result = await request("put", "/api/server/time", { body: { timezone: zone.trim(), install_ntp: false } });
            refresh();
            const moved = (result.moved_timers ?? []).length;
            // design-exception: toast-success the zone is on screen, what moved with it is not
            if (moved > 0) toast.success(t("server.clock.zoneChanged", { zone: result.time.timezone }), { description: t("server.clock.timersMoved", { count: moved }) });
          }}
        >
          <TimeZoneField value={zone} onValueChange={setZone} current={clock.timezone} label={t("server.clock.zoneLabel")} />
        </ConfirmDialog>
      ) : null}
      {installOpen ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(open) => {
            if (!open) setInstallOpen(false);
          }}
          friction="simple"
          destructive={false}
          size="md"
          title={t("server.clock.installTitle")}
          description={t("server.clock.installDescription")}
          actionLabel={t("server.clock.installAction")}
          onConfirm={async () => {
            await request("put", "/api/server/time", { body: { ntp: true, install_ntp: install } });
            refresh();
          }}
        >
          <Checkbox label={t("server.clock.installChrony")} description={t("server.clock.installChronyHelp")} checked={install} onCheckedChange={setInstall} />
        </ConfirmDialog>
      ) : null}
      {renameOpen ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(next) => {
            if (!next) setRenameOpen(false);
          }}
          friction="simple"
          destructive={false}
          size="md"
          title={t("server.hostname.renameTitle")}
          description={t("server.hostname.renameDescription")}
          actionLabel={t("server.hostname.renameAction")}
          onConfirm={async () => {
            // design-exception: write-without-permission opened from the name's button, drawn only for server.manage
            const result = await request("put", "/api/server/identity/hostname", { body: { hostname: name.trim(), keep_against_cloud_init: host.cloud_init && keep } });
            void queryClient.invalidateQueries({ queryKey: serverKeys.identity });
            void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
            // design-exception: toast-success the name is on screen, the steps it took to change it are not
            toast.success(t("server.hostname.renamed", { name: name.trim() }), { description: result.steps.join(" ") });
          }}
        >
          <Field label={t("server.hostname.newName")} description={t("server.hostname.newNameHelp")}>
            <Input mono value={name} onValueChange={(value: string) => setName(value)} autoComplete="off" spellCheck={false} />
          </Field>
          {host.cloud_init ? <Checkbox label={t("server.hostname.keep")} description={t("server.hostname.keepHelp")} checked={keep} onCheckedChange={setKeep} /> : null}
        </ConfirmDialog>
      ) : null}
    </SurfaceSection>
  );
}

/** What the machine is made of, read from the machine itself. */
function Hardware({ identity }: { identity: Identity }) {
  const t = useT();
  const machine = useQuery(machineQuery());
  const summary = useQuery(summaryQuery());
  const eolState = identity.eol.status === "expired" ? "failed" : identity.eol.status === "warn" ? "warning" : null;
  const disk = summary.data?.disk;
  const items: KeyValueItem[] = [
    { label: t("server.machine.processor"), value: t("server.machine.cores", { count: identity.cpu_count }), mono: false, copy: false },
    ...(machine.data !== undefined ? [{ label: t("server.machine.memory"), value: formatBytes(machine.data.memory.total, t.locale), mono: false, copy: false as const }] : []),
    ...(machine.data !== undefined && disk?.worst_percent != null
      ? [{ label: t("server.machine.disk"), value: t("server.machine.diskValue", { total: formatBytes(machine.data.disk.total, t.locale), percent: Math.round(disk.worst_percent) }), mono: false, copy: false as const }]
      : []),
    {
      label: t("server.os.name"),
      value: (
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-13 text-fg">{identity.os_name}</span>
          {eolState !== null ? <StatusPill state={eolState} label={supportLine(t, identity)} appearance="inline" size="sm" /> : <span className="text-12 text-fg-muted">{supportLine(t, identity)}</span>}
        </span>
      ),
      mono: false,
      copy: false,
    },
    { label: t("server.os.kernel"), value: identity.kernel },
    { label: t("server.os.architecture"), value: identity.architecture },
    ...(identity.container ? [{ label: t("server.os.container"), value: identity.container }] : []),
    { label: t("server.os.booted"), value: identity.booted_at ? <RelativeTime value={identity.booted_at} /> : null, mono: false, copy: false },
    {
      label: t("server.os.load"),
      value: t("server.os.loadValue", { load: identity.load.map((value) => formatLoad(value, t.locale)).join(" "), cpus: identity.cpu_count }),
      mono: false,
      copy: false,
    },
    { label: t("server.hostname.machineId"), value: identity.hostname.machine_id },
  ];
  return (
    <SurfaceSection title={t("server.machine.hardwareTitle")} icon={OBJECT_ICONS.processes} description={t("server.machine.hardwareDescription")}>
      <KeyValueList items={items} />
    </SurfaceSection>
  );
}

/** Reboot or shut down, now or later, and what is already scheduled; a reboot the system asked for is said first. */
function PowerSection() {
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
  const rebootDue = summary.data?.reboot.required === true;
  const when = scheduled === null ? "" : due ? formatMoment(due, t.locale) : scheduled.scheduled_for;
  return (
    <SurfaceSection
      title={t("server.power.cardTitle")}
      icon={Power}
      description={rebootDue ? t("server.machine.powerNeedsReboot") : t("server.power.cardDescription")}
      {...(rebootDue ? { status: <StatusPill state="warning" label={t("server.machine.rebootPending")} appearance="inline" size="sm" /> } : {})}
      loading={summary.data === undefined}
    >
      <div className="flex flex-col gap-4">
        {scheduled !== null ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-13 text-fg">{scheduled.action === "reboot" ? t("server.power.bannerReboot", { when }) : t("server.power.bannerShutdown", { when })}</span>
            <Can permission="server.manage">
              <Button size="sm" loading={cancel.isPending} onClick={() => cancel.mutate()}>
                {scheduled.action === "reboot" ? t("server.power.cancelReboot") : t("server.power.cancelShutdown")}
              </Button>
            </Can>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Can permission="server.manage">
            <Button icon={<RotateCw aria-hidden="true" />} onClick={() => power.open("reboot")}>
              {t("server.power.rebootEllipsis")}
            </Button>
          </Can>
          <Can permission="server.host_access">
            <Button variant="ghost" onClick={() => power.open("shutdown")}>
              {t("server.power.shutdownEllipsis")}
            </Button>
          </Can>
        </div>
      </div>
    </SurfaceSection>
  );
}

/** The Machine section: the three slabs, drawn together once the machine has answered. */
export function MachineSections() {
  const t = useT();
  const clock = useQuery(timeQuery());
  const identity = useQuery(identityQuery());
  const summary = useQuery(summaryQuery());
  // The slabs answer apart and one is as tall as its rows: nothing is drawn until the three are known, so the page
  // never grows under a person's eyes.
  if ([clock, identity, summary].some((query) => query.isPending)) return <MachineSkeleton />;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ReadOnlyLine permission="server.manage" />
      <ServerQueryState query={clock} label={t("server.clock.label")} skeleton={<SlabSkeleton title={t("server.machine.thisTitle")} rows={4} />}>
        {(clockData) => (
          <ServerQueryState query={identity} label={t("server.hostname.label")} skeleton={<SlabSkeleton title={t("server.machine.thisTitle")} rows={4} />}>
            {(identityData) => (
              <>
                <ThisMachine clock={clockData} host={identityData.hostname} />
                <Hardware identity={identityData} />
              </>
            )}
          </ServerQueryState>
        )}
      </ServerQueryState>
      <PowerSection />
    </div>
  );
}
