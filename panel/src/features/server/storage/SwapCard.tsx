/**
 * Swap: what the machine has, how eagerly the kernel uses it, and a swap file when a server with
 * little memory has none (a build killed for lack of memory is the usual way to find out).
 * Noust only ever removes the swap file it made: a partition, the installer's image and zram
 * are never touched.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock } from "../errors";
import { serverKeys, swapQuery } from "../queries";
import { useServerJob } from "../serverJob";

const MIB = 1024 ** 2;

type Opened = "create" | "remove" | "swappiness" | null;

export function SwapCard() {
  const t = useT();
  const jobs = useServerJob();
  const queryClient = useQueryClient();
  const swap = useQuery(swapQuery());
  const [opened, setOpened] = useState<Opened>(null);
  const [size, setSize] = useState("");
  const [swappiness, setSwappiness] = useState("");

  if (swap.isError && swap.data === undefined) {
    return (
      <Card level={2} title={t("server.swap.title")} padding="sm">
        <ServerErrorBlock compact error={swap.error} title={t("server.swap.loadFailed")} onRetry={() => void swap.refetch()} />
      </Card>
    );
  }
  if (swap.data === undefined) {
    return (
      <Card level={2} title={t("server.swap.title")} padding="sm">
        <Skeleton className="h-32 w-full" />
      </Card>
    );
  }
  const data = swap.data;
  const none = data.total_bytes === 0;
  const suggestedMb = Math.max(256, Math.round(data.suggested_bytes / MIB));
  const items: KeyValueItem[] = [
    { label: t("server.swap.memory"), value: formatBytes(data.memory_bytes, t.locale), mono: false, copy: false },
    {
      label: t("server.swap.swap"),
      value: none ? t("server.swap.none") : t("server.swap.used", { used: formatBytes(data.used_bytes, t.locale), total: formatBytes(data.total_bytes, t.locale) }),
      mono: false,
      copy: false,
    },
    ...data.devices.map(
      (device): KeyValueItem => ({
        label: device.kind === "file" ? t("server.swap.file") : t("server.swap.device"),
        value: <Mono>{device.name}</Mono>,
        mono: false,
        copy: false,
      }),
    ),
    { label: t("server.swap.swappiness"), value: data.swappiness != null ? String(data.swappiness) : null },
  ];
  return (
    <Card level={2}
      title={t("server.swap.title")}
      padding="sm"
      actions={
        data.supported && data.swappiness != null ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSwappiness(String(data.swappiness ?? 10));
              setOpened("swappiness");
            }}
          >
            {t("server.swap.changeSwappiness")}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3">
        <KeyValueList items={items} />
        {!data.supported ? <p className="text-13 text-fg-muted">{data.reason}</p> : null}
        {data.supported && none && data.recommended ? (
          <Notice tone="warning" title={t("server.swap.recommendedTitle")}>
            {t("server.swap.recommended", { memory: formatBytes(data.memory_bytes, t.locale) })}
          </Notice>
        ) : null}
        {data.supported && none ? (
          <div>
            <Button
              size="sm"
              disabled={jobs.busy}
              onClick={() => {
                setSize(String(suggestedMb));
                setOpened("create");
              }}
            >
              {t("server.swap.create")}
            </Button>
          </div>
        ) : null}
        {data.noust_swapfile ? (
          <div>
            <Button size="sm" variant="ghost" disabled={jobs.busy} onClick={() => setOpened("remove")}>
              {t("server.swap.remove")}
            </Button>
          </div>
        ) : null}
        {data.warnings.map((warning) => (
          <p key={warning} className="text-12 text-fg-muted">
            {warning}
          </p>
        ))}
      </div>
      {opened === "create" ? (
        <ActionDialog
          title={t("server.swap.createTitle")}
          description={t("server.swap.createDescription")}
          actionLabel={t("server.swap.createAction")}
          onClose={() => setOpened(null)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/swap", { body: { size_mb: Number(size) } });
            jobs.track(accepted.job_id, "swap");
          }}
        >
          <Field label={t("server.swap.sizeLabel")} description={t("server.swap.sizeHelp", { suggested: String(suggestedMb) })}>
            <Input mono inputMode="numeric" value={size} onValueChange={(value: string) => setSize(value)} suffix={<span className="px-2 text-12 text-fg-faint">MiB</span>} className="w-40" />
          </Field>
        </ActionDialog>
      ) : null}
      {opened === "remove" ? (
        <ActionDialog
          title={t("server.swap.removeTitle")}
          description={t("server.swap.removeDescription")}
          actionLabel={t("server.swap.removeAction")}
          destructive
          onClose={() => setOpened(null)}
          onConfirm={async () => {
            const accepted = await request("delete", "/api/server/swap");
            jobs.track(accepted.job_id, "swap");
          }}
        />
      ) : null}
      {opened === "swappiness" ? (
        <ActionDialog
          title={t("server.swap.swappinessTitle")}
          description={t("server.swap.swappinessDescription")}
          actionLabel={t("server.swap.swappinessAction")}
          size="sm"
          onClose={() => setOpened(null)}
          onConfirm={async () => {
            await request("put", "/api/server/swap/swappiness", { body: { value: Number(swappiness) } });
            void queryClient.invalidateQueries({ queryKey: serverKeys.swap });
          }}
        >
          <Field label={t("server.swap.swappiness")} description={t("server.swap.swappinessHelp")}>
            <Input mono inputMode="numeric" value={swappiness} onValueChange={(value: string) => setSwappiness(value)} className="w-24" />
          </Field>
        </ActionDialog>
      ) : null}
    </Card>
  );
}
