import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { isApiError, request } from "../../../api/client";
import { diskKey, diskQuery } from "../../../api/queries/disk";
import type { AppDisk, DiskPart } from "../../../api/queries/disk";
import type { App } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { ResourceMeter } from "../../../components/page/ResourceMeter";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { KernelQuota } from "./KernelQuota";
import { useSaveBar } from "./formParts";
import type { FormPart } from "./formParts";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

/** The least a limit can be (MB), the backend's own floor: a release has to fit in it. */
const MIN_LIMIT_MB = 64;

const PART_KEYS = {
  tree: "runtime.disk.part.tree",
  current: "runtime.disk.part.current",
  releases: "runtime.disk.part.releases",
  shared: "runtime.disk.part.shared",
  repo: "runtime.disk.part.repo",
} as const;

/** The limit as typed, read as megabytes; null for empty (no limit) and undefined for a mistake. */
export function parseDiskLimit(text: string): number | null | undefined {
  const typed = text.trim();
  if (typed === "") return null;
  if (!/^\d+$/.test(typed)) return undefined;
  const value = Number(typed);
  return value >= MIN_LIMIT_MB ? value : undefined;
}

function partName(part: DiskPart, t: T): string {
  const known: Readonly<Record<string, (typeof PART_KEYS)[keyof typeof PART_KEYS]>> = PART_KEYS;
  const key = known[part.name];
  return key !== undefined ? t(key) : part.name;
}

function Usage({ disk, t }: { disk: AppDisk; t: T }) {
  const usage = disk.usage;
  if (usage === null) return null;
  return (
    <div className="flex flex-col gap-3">
      {disk.verdict ? (
        <ResourceMeter label={t("runtime.disk.usedLabel")} value={disk.verdict.used_bytes} limit={disk.verdict.limit_bytes} format={(value) => formatBytes(value, t.locale)} />
      ) : null}
      <ul aria-label={t("runtime.disk.breakdown")} className="flex flex-col divide-y divide-border">
        {usage.parts.map((part) => (
          <li key={part.name} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-1.5">
            <span className="text-13 text-fg">{partName(part, t)}</span>
            <span className="flex items-baseline gap-3">
              {part.reclaimable_bytes ? (
                <span className="text-12 text-fg-muted">{t("runtime.disk.reclaimable", { size: formatBytes(part.reclaimable_bytes, t.locale) })}</span>
              ) : null}
              <Mono>{formatBytes(part.bytes, t.locale)}</Mono>
            </span>
          </li>
        ))}
        <li className="flex items-baseline justify-between gap-4 py-1.5">
          <span className="text-13 font-medium text-fg">{t("runtime.disk.total")}</span>
          <Mono>{formatBytes(usage.total_bytes, t.locale)}</Mono>
        </li>
      </ul>
      <p className="text-12 text-fg-muted">
        {usage.backups_bytes > 0 ? <span>{t("runtime.disk.backups", { size: formatBytes(usage.backups_bytes, t.locale) })} </span> : null}
        <span>{t.rich("runtime.disk.measured", { time: <RelativeTime value={usage.measured_at} /> })}</span>
      </p>
      {usage.errors.length > 0 ? (
        <Notice tone="warning" title={t("runtime.disk.errorsTitle")}>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {usage.errors.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}

export interface DiskLimitPart {
  /** The save bar's part; null until the disk is read. */
  part: FormPart | null;
  /** The limit as typed, or as stored while untouched. */
  shown: string;
  edit: (value: string) => void;
  error: string | undefined;
  unplaced: unknown;
}

/**
 * The limit its deploys are held to, as a part of the page's form: an empty field is no limit, and
 * the server decides what it accepts. Saved from the save bar with the page's one Save.
 */
export function useDiskLimit(domain: string, disk: AppDisk | undefined): DiskLimitPart {
  const t = useT();
  const queryClient = useQueryClient();
  const [text, setText] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const stored = disk === undefined ? null : disk.soft_mb === null ? "" : String(disk.soft_mb);
  const shown = text ?? stored ?? "";
  const parsed = parseDiskLimit(shown);

  const save = useMutation({
    mutationFn: (softMb: number | null) => request("put", "/api/apps/{domain}/disk", { params: { domain }, body: { soft_mb: softMb } }),
    onSuccess: (answer) => {
      queryClient.setQueryData(diskKey(domain), answer);
      setText(null);
      setSubmitted(false);
      announce(answer.soft_mb === null ? t("runtime.disk.limitRemoved") : t("runtime.disk.limitSaved", { limit: String(answer.soft_mb) }));
    },
  });
  const fieldError = isApiError(save.error) ? save.error.fields?.["soft_mb"] : undefined;
  const clientError = submitted && parsed === undefined ? t("runtime.disk.limitInvalid", { min: MIN_LIMIT_MB }) : undefined;

  return {
    part:
      stored === null
        ? null
        : {
            changes: shown.trim() !== stored ? 1 : 0,
            elevated: false,
            check: () => {
              setSubmitted(true);
              return parsed !== undefined;
            },
            save: async () => {
              if (parsed === undefined) return;
              await save.mutateAsync(parsed);
            },
            discard: () => {
              setText(null);
              setSubmitted(false);
              save.reset();
            },
          },
    shown,
    edit: (value) => {
      setText(value);
      if (save.isError) save.reset();
    },
    error: clientError ?? fieldError,
    unplaced: save.isError && fieldError === undefined ? save.error : null,
  };
}

/**
 * What the app takes of the disk and the limit its deploys are held to (checklist B-63). The
 * measurement is a job, since walking a release tree takes a while: the figures are the last
 * one, with how old it is. The limit is Noust's and not the kernel's, and the section says so
 * and says whether the filesystem could hold a kernel quota.
 */
function DiskCard({ app, disk, limit }: { app: App; disk: UseQueryResult<AppDisk>; limit: DiskLimitPart }) {
  const t = useT();
  const domain = app.domain;
  const queryClient = useQueryClient();
  const followed = useFollowedJob();

  const measure = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/disk/measure", { params: { domain } }),
    onSuccess: (queued) => {
      followed.follow(queued.job_id);
      announce(t("runtime.disk.measuring"));
    },
  });

  // The measurement ends in the job; the page then reads what it kept.
  const finished = followed.job !== null && isJobFinished(followed.job);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({ queryKey: diskKey(domain) });
      followed.dismiss();
    }
  }, [finished, domain, queryClient, followed]);

  if (disk.isPending) {
    return (
      <LoadingRegion label={t("runtime.disk.loading")}>
        <SkeletonCard lines={4} />
      </LoadingRegion>
    );
  }
  if (disk.isError) return <ErrorBlock compact error={disk.error} title={t("runtime.disk.loadFailed")} onRetry={() => void disk.refetch()} retrying={disk.isRefetching} />;

  const measuring = measure.isPending || (followed.job !== null && !finished);
  const filesystem = disk.data.filesystem;
  return (
    <>
      <Card>
        <div className="flex flex-col gap-5">
          {disk.data.usage ? (
            <Usage disk={disk.data} t={t} />
          ) : (
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("runtime.disk.neverMeasured")}</p>
          )}
          <div>
            <Button variant="secondary" icon={<OBJECT_ICONS.disk aria-hidden="true" />} loading={measuring} onClick={() => measure.mutate()}>
              {disk.data.usage ? t("runtime.disk.measureAgain") : t("runtime.disk.measure")}
            </Button>
          </div>
          {measure.isError ? <ErrorBlock live compact error={measure.error} title={t("runtime.disk.measureFailed")} /> : null}
          <div className="flex flex-col gap-3 border-t border-border pt-5">
            <Field label={t("runtime.disk.limitLabel")} description={t("runtime.disk.limitDescription")} error={limit.error}>
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                placeholder={t("appSettings.limits.noLimitPlaceholder")}
                suffix={t("runtime.disk.limitSuffix")}
                value={limit.shown}
                onValueChange={limit.edit}
              />
            </Field>
            {limit.unplaced !== null ? <ErrorBlock live compact error={limit.unplaced} title={t("runtime.disk.saveFailed")} /> : null}
          </div>
          {/* What the limit is and what the filesystem could add to it are one note, not two blocks of the card. */}
          <div className="flex flex-col gap-1">
            <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("runtime.disk.softNote")}</p>
            <p className="max-w-measure text-12 text-pretty text-fg-muted">
              {filesystem.fstype === null
                ? t("runtime.disk.quota.unknown")
                : t(`runtime.disk.quota.${filesystem.project_quota}`, { fstype: filesystem.fstype, mount: filesystem.mount ?? "/" })}
            </p>
          </div>
          <KernelQuota disk={disk.data} domain={domain} />
        </div>
      </Card>
      <CommandHint command={`noust app disk ${domain} --measure --limit 2048`} label={t("common.words.fromTerminal")} />
    </>
  );
}

/** Disk: what the app takes of it and the limit its deploys are held to, a subsection of its own with its save bar. */
export function DiskSettings() {
  const t = useT();
  const app = useSettingsApp();
  useSubsectionTitle("runtime.nav.disk", app.domain);
  const headerIcon = useSubsectionIcon("runtime.nav.disk");
  const disk = useQuery(diskQuery(app.domain));
  const limit = useDiskLimit(app.domain, disk.data);
  const bar = useSaveBar([limit.part], t("runtime.disk.saveFailed"));
  return (
    <>
      <Section title={t("runtime.disk.title")} {...headerIcon} description={t("runtime.disk.description")}>
        <DiskCard app={app} disk={disk} limit={limit} />
      </Section>
      {disk.data !== undefined ? <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} /> : null}
    </>
  );
}
