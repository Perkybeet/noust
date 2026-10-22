import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { deployKeysQuery, pinnedHostsQuery, sourceKeys } from "../../api/queries/sources";
import type { DeployKey, PinnedHost } from "../../api/queries/sources";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useSudoFirst } from "../app/settings/formParts";
import { splitErrors } from "./formErrors";

/**
 * What the API takes to forget a host that listens on another port: `name:port`. The list shows
 * it the way `known_hosts` writes it, `[name]:port`, which the API refuses as a name.
 */
export function forgetTarget(host: string): string {
  const bracketed = /^\[(.+)\]:(\d+)$/.exec(host);
  return bracketed ? `${bracketed[1] ?? ""}:${bracketed[2] ?? ""}` : host;
}

/** How many applications a key's row names before "and N more". */
const APPS_SHOWN = 2;

function keyColumns(t: T): Column<DeployKey>[] {
  return [
    {
      id: "name",
      header: t("sources.server.columnName"),
      cell: (key) => (
        <span className="flex min-w-0 flex-col">
          <Mono truncate>{key.name}</Mono>
          <span className="truncate text-12 text-fg-muted">
            <Mono tone="muted">{key.key_type}</Mono> <Mono tone="faint">{key.fingerprint}</Mono>
          </span>
        </span>
      ),
      card: "title",
    },
    {
      id: "state",
      header: t("sources.server.columnState"),
      cell: (key) =>
        key.pending ? (
          <StatusPill state="queued" label={t("sources.server.rotating")} appearance="inline" size="sm" />
        ) : (
          <StatusPill state="running" label={t("sources.server.inUse")} appearance="inline" size="sm" />
        ),
      width: "w-36",
      card: "status",
    },
    {
      id: "repositories",
      header: t("sources.server.columnRepositories"),
      cell: (key) => {
        const repositories = key.repositories ?? [];
        return repositories.length === 0 ? <EmptyCell reason={t("sources.server.noApps")} /> : <Mono truncate>{repositories.join(", ")}</Mono>;
      },
      hideBelow: "md",
      card: "meta",
    },
    {
      id: "applications",
      header: t("sources.server.columnApps"),
      cell: (key) => {
        const apps = key.applications ?? [];
        if (apps.length === 0) return <EmptyCell reason={t("sources.server.noApps")} />;
        const shown = apps.slice(0, APPS_SHOWN);
        const links = shown.map((domain, index) => (
          <span key={domain}>
            {index > 0 ? ", " : null}
            <TextLink to="/apps/$domain/settings/source" params={{ domain }} size="ui">
              {domain}
            </TextLink>
          </span>
        ));
        return (
          <span className="truncate">
            {links}
            {apps.length > shown.length ? <span className="text-fg-muted"> {t("sources.server.andMore", { count: apps.length - shown.length })}</span> : null}
          </span>
        );
      },
      card: "meta",
    },
    {
      id: "used",
      header: t("sources.server.columnUsed"),
      cell: (key) => <RelativeTime value={key.last_used_at} fallback={t("sources.server.never")} />,
      width: "w-32",
      hideBelow: "lg",
      card: "hidden",
    },
  ];
}

function hostColumns(t: T): Column<PinnedHost>[] {
  return [
    { id: "host", header: t("sources.server.hosts.columnHost"), cell: (host) => <Mono truncate>{host.host}</Mono>, card: "title" },
    { id: "type", header: t("sources.server.hosts.columnType"), cell: (host) => <Mono>{host.key_type}</Mono>, width: "w-32", card: "meta" },
    { id: "fingerprint", header: t("sources.server.hosts.columnFingerprint"), cell: (host) => <Mono truncate>{host.fingerprint}</Mono>, card: "meta" },
  ];
}

/**
 * Pinning a host's key before Noust has connected to it: the host, and the fingerprint its code
 * host publishes. Noust asks the host for its keys and records only the one that matches, so the
 * first connection is not trust on first use. The same as `noust app deploy-key pin-host`.
 */
function PinHostForm() {
  const t = useT();
  const queryClient = useQueryClient();
  const [host, setHost] = useState("");
  const [fingerprint, setFingerprint] = useState("");
  const pin = useMutation({
    mutationFn: () => request("post", "/api/deploy-keys/hosts", { body: { host: host.trim(), fingerprint: fingerprint.trim() } }),
    onSuccess: (pinned) => {
      void queryClient.invalidateQueries({ queryKey: sourceKeys.pinnedHosts });
      announce(t("sources.server.hosts.pinned", { host: pinned.host }));
      setHost("");
      setFingerprint("");
    },
  });
  const split = splitErrors(pin.error, ["host", "fingerprint"] as const);
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (host.trim() !== "" && fingerprint.trim() !== "" && !pin.isPending) pin.mutate();
  };
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-3">
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("sources.server.hosts.pinDescription")}</p>
      <Field label={t("sources.server.hosts.pinHost")} description={t("sources.server.hosts.pinHostHint")} error={split.fields.host} className="sm:max-w-80">
        <Input mono value={host} placeholder="github.com" onValueChange={setHost} autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      <Field label={t("sources.server.hosts.pinFingerprint")} description={t("sources.server.hosts.pinFingerprintHint")} error={split.fields.fingerprint}>
        <Input mono value={fingerprint} placeholder="SHA256:..." onValueChange={setFingerprint} autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      {pin.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("sources.server.hosts.notPinned", { host: host.trim() })} /> : null}
      <div>
        <Button type="submit" size="sm" loading={pin.isPending} disabled={host.trim() === "" || fingerprint.trim() === ""}>
          {t("sources.server.hosts.pinAction")}
        </Button>
      </div>
    </form>
  );
}

/** The host keys Noust pinned, and the one deliberate way to accept a key that changed. */
function PinnedHosts() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const hosts = useQuery(pinnedHostsQuery());
  const [forgetting, setForgetting] = useState<PinnedHost | null>(null);

  return (
    <Subsection level={3} title={t("sources.server.hosts.title")} description={t("sources.server.hosts.description")}>
      <QueryState
        query={hosts}
        label={t("sources.server.hosts.label")}
        skeleton={<Skeleton className="h-16 w-full rounded-card" />}
        isEmpty={(data) => data.hosts.length === 0}
        empty={<EmptyState variant="inline" title={t("sources.server.hosts.empty")} />}
      >
        {(data) => (
          <DataTable
            caption={t("sources.server.hosts.caption")}
            columns={hostColumns(t)}
            rows={data.hosts}
            getRowId={(host) => host.host}
            density="compact"
            mobile="cards"
            rowActions={(host) => (
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("sources.server.hosts.forgetLabel", { host: host.host })}
                onClick={() => sudoFirst(() => setForgetting(host), t("sources.server.hosts.notForgotten", { host: host.host }))}
              >
                {t("sources.server.hosts.forget")}
              </Button>
            )}
          />
        )}
      </QueryState>
      <Subsection level={4} title={t("sources.server.hosts.pinTitle")}>
        <PinHostForm />
      </Subsection>
      {forgetting !== null ? (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) setForgetting(null);
          }}
          friction="simple"
          server={node}
          title={t("sources.server.hosts.forgetTitle", { host: forgetting.host })}
          description={t("sources.server.hosts.forgetDescription")}
          actionLabel={t("sources.server.hosts.forgetAction")}
          onConfirm={async () => {
            const host = forgetting.host;
            const result = await request("delete", "/api/deploy-keys/hosts/{host}", { params: { host: forgetTarget(host) } });
            void queryClient.invalidateQueries({ queryKey: sourceKeys.pinnedHosts });
            announce(result.forgotten ? t("sources.server.hosts.forgotten", { host }) : t("sources.server.hosts.nothingToForget", { host }));
          }}
        />
      ) : null}
    </Subsection>
  );
}

/**
 * Settings, Integrations: every deploy key this server holds, with the repositories it reaches
 * and the applications built from them, and the host keys it has pinned. Read only here: a key
 * is made, replaced and removed where the repository is, in the application's Source settings.
 * Forgetting a pinned host key is the one change made on this page, and asks first.
 */
export function DeployKeysSection() {
  const t = useT();
  const keys = useQuery(deployKeysQuery());
  return (
    <Section title={t("sources.server.title")} description={t("sources.server.description")}>
      <QueryState
        query={keys}
        label={t("sources.server.label")}
        skeleton={<Skeleton className="h-24 w-full rounded-card" />}
        isEmpty={(data) => data.keys.length === 0}
        empty={<EmptyState variant="inline" title={t("sources.server.emptyTitle")} description={t("sources.server.emptyDescription")} />}
      >
        {(data) => (
          <DataTable
            caption={t("sources.server.caption")}
            columns={keyColumns(t)}
            rows={data.keys}
            getRowId={(key) => key.name}
            density="compact"
            mobile="cards"
          />
        )}
      </QueryState>
      <PinnedHosts />
      <CommandHint command="noust app deploy-key list" label={t("common.words.sameFromTerminal")} />
    </Section>
  );
}
