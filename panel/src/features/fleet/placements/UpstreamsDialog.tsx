import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import { UPSTREAM_FORMATS, upstreamsQuery } from "../placements";
import type { UpstreamFormat } from "../placements";

export interface UpstreamsDialogProps {
  domain: string;
  onClose: () => void;
}

/** A port as typed, or null when it is not one. */
export function parsePort(value: string): number | null {
  const text = value.trim();
  if (!/^\d{1,5}$/.test(text)) return null;
  const port = Number(text);
  return port >= 1 && port <= 65535 ? port : null;
}

/**
 * The servers an application runs on, written for the load balancer in front of them. Noust routes
 * no traffic between servers: the balancer is the operator's, and this is what it is given.
 */
export function UpstreamsDialog({ domain, onClose }: UpstreamsDialogProps) {
  const t = useT();
  const [format, setFormat] = useState<UpstreamFormat>("nginx");
  const [portText, setPortText] = useState("443");
  const port = parsePort(portText);
  const written = useQuery({ ...upstreamsQuery(domain, format, port ?? 443), enabled: port !== null });

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="lg"
      title={t("placements.upstreams.title", { domain })}
      description={t("placements.upstreams.description")}
      footer={<Button onClick={onClose}>{t("placements.upstreams.close")}</Button>}
    >
      <div className="flex flex-col gap-5">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field label={t("placements.upstreams.formatLabel")} nativeLabel={false}>
            <Select
              value={format}
              onValueChange={setFormat}
              options={UPSTREAM_FORMATS.map((value) => ({ value, label: t(`placements.upstreams.format.${value}`) }))}
            />
          </Field>
          <Field label={t("placements.upstreams.portLabel")} error={port === null ? t("placements.upstreams.portInvalid") : undefined}>
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              value={portText}
              onValueChange={(value: string) => {
                setPortText(value);
              }}
            />
          </Field>
        </div>
        {written.isPending && port !== null ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">{t("placements.card.comparing", { domain })}</span>
            <Skeleton className="h-4 w-64 max-w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : null}
        {written.isError ? <ErrorBlock live error={written.error} title={t("placements.upstreams.failed", { domain })} onRetry={() => void written.refetch()} /> : null}
        {written.data !== undefined && port !== null ? (
          <>
            <div className="flex min-w-0 flex-col gap-1.5 rounded-control border border-border bg-bg-sunken p-3">
              <div className="flex items-center justify-end">
                <CopyButton value={written.data.text} label={t("placements.upstreams.copyLabel", { domain })} />
              </div>
              <SystemOutput label={t("placements.upstreams.outputLabel", { domain })} maxHeight="max-h-72">
                {written.data.text}
              </SystemOutput>
            </div>
            <ul aria-label={t("placements.upstreams.serversCaption")} className="flex flex-col gap-2">
              {written.data.servers.map((server) => (
                <li key={server.node} className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
                  <Mono className="font-medium">{server.node}</Mono>
                  <StatusPill
                    state={server.serving ? "running" : "failed"}
                    label={server.serving ? t("placements.upstreams.serving") : t("placements.upstreams.down")}
                    appearance="inline"
                    size="sm"
                  />
                  {server.note !== null && server.note !== undefined ? <span className="text-13 text-fg-muted">{server.note}</span> : null}
                </li>
              ))}
            </ul>
            <Notice>{t("placements.upstreams.addressNote")}</Notice>
          </>
        ) : null}
      </div>
    </Dialog>
  );
}
