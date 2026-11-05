import { Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { deployKeysQuery, pinnedHostsQuery, sourceKeys } from "../../api/queries/sources";
import type { DeployKey, PinnedHost } from "../../api/queries/sources";
import { announce } from "../../app/Announcer";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { ReadOnlyLine, useCan } from "../../components/page/permissions";
import { Badge } from "../../components/ui/Badge";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { FirstUse } from "../../components/ui/FirstUse";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
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

/** How many applications a key's row names after its name before "+N". */
const APPS_SHOWN = 1;

/** What pinning and forgetting a host key take: it changes whom this server trusts with its keys (`routes_deploy_keys.py`). */
const HOST_ACCESS = "server.host_access";

function keyColumns(t: T): Column<DeployKey>[] {
  return [
    {
      id: "name",
      header: t("sources.server.columnName"),
      // The applications built from the key are tags of its name, on its line, each the link to where they set it.
      cell: (key) => (
        <NameWithTags
          name={key.name}
          mono
          tags={key.applications ?? []}
          shown={APPS_SHOWN}
          title={t("sources.server.appsOf", { name: key.name })}
          renderTag={(domain) => (
            <TextLink to="/apps/$domain/settings/source" params={{ domain }} size="ui">
              {domain}
            </TextLink>
          )}
        />
      ),
      card: "title",
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
      id: "fingerprint",
      header: t("sources.server.columnFingerprint"),
      cell: (key) => (
        <span className="flex min-w-0 items-center gap-2">
          <Badge mono>{key.key_type}</Badge>
          <Mono tone="muted" truncate>
            {key.fingerprint}
          </Mono>
        </span>
      ),
      hideBelow: "sm",
      card: "meta",
    },
    {
      id: "state",
      header: t("sources.server.columnState"),
      // A key in use is the common one and says nothing; the one being replaced is the one to see.
      cell: (key) => (key.pending ? <StatusPill state="queued" label={t("sources.server.rotating")} appearance="inline" size="sm" /> : null),
      width: "w-36",
      card: "status",
    },
    {
      id: "used",
      header: t("sources.server.columnUsed"),
      cell: (key) => <RelativeTime value={key.last_used_at} fallback={t("sources.server.never")} className="text-13 text-fg-muted" />,
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
    // design-exception: write-without-permission the form is drawn only for server.host_access, in the section that holds it
    if (host.trim() !== "" && fingerprint.trim() !== "" && !pin.isPending) pin.mutate();
  };
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <div className="flex flex-col gap-4 sm:flex-row">
        <Field label={t("sources.server.hosts.pinHost")} description={t("sources.server.hosts.pinHostHint")} error={split.fields.host} className="sm:w-72 sm:shrink-0">
          <Input mono value={host} placeholder="git.example.org:2222" onValueChange={setHost} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("sources.server.hosts.pinFingerprint")} description={t("sources.server.hosts.pinFingerprintHint")} error={split.fields.fingerprint} className="min-w-0 sm:flex-1">
          <Input mono value={fingerprint} placeholder="SHA256:..." onValueChange={setFingerprint} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
      </div>
      {pin.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("sources.server.hosts.notPinned", { host: host.trim() })} /> : null}
      <div>
        <Button type="submit" size="sm" loading={pin.isPending} disabled={host.trim() === "" || fingerprint.trim() === ""}>
          {t("sources.server.hosts.pinAction")}
        </Button>
      </div>
    </form>
  );
}

/** The code hosts whose key Noust pinned, and the one deliberate way to accept a key that changed. */
function TrustedHosts({ canManage }: { canManage: boolean }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const hosts = useQuery(pinnedHostsQuery());
  const [forgetting, setForgetting] = useState<PinnedHost | null>(null);

  return (
    <>
      <SurfaceSection
        title={t("sources.server.hosts.title")}
        icon={OBJECT_ICONS.certificate}
        description={t("sources.server.hosts.description")}
        help={t("sources.server.hosts.help")}
        flush
      >
        <QueryState
          query={hosts}
          label={t("sources.server.hosts.label")}
          skeleton={<Skeleton className="h-24 w-full rounded-card" />}
          isEmpty={(data) => data.hosts.length === 0}
          empty={<EmptyState variant="inline" title={t("sources.server.hosts.empty")} className="px-5" />}
        >
          {(data) => (
            <DataTable
              embedded
              caption={t("sources.server.hosts.caption")}
              columns={hostColumns(t)}
              rows={data.hosts}
              getRowId={(host) => host.host}
              density="compact"
              {...(canManage
                ? {
                    rowActions: (host: PinnedHost) => (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("sources.server.hosts.forgetLabel", { host: host.host })}
                        onClick={() => sudoFirst(() => setForgetting(host), t("sources.server.hosts.notForgotten", { host: host.host }))}
                      >
                        {t("sources.server.hosts.forget")}
                      </Button>
                    ),
                  }
                : {})}
            />
          )}
        </QueryState>
      </SurfaceSection>
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
    </>
  );
}

/**
 * Settings, Deploy keys: every deploy key this server holds, with the applications built from it, and the code hosts
 * it trusts, each in a slab that says what it is. Read only for the keys: a key is made, replaced and removed where the
 * repository is, in the application's Source settings, and the first time points there. Forgetting a trusted host
 * and adding one before the first connection are the changes made here, and they take `server.host_access`: a role
 * without it reads the page, sees no button and one line on top that says so.
 */
export function DeployKeysSection() {
  const t = useT();
  const canManage = useCan(HOST_ACCESS);
  const keys = useQuery(deployKeysQuery());
  const hosts = useQuery(pinnedHostsQuery());
  // The keys and the trusted hosts are one page and are answered apart: nothing is drawn until both are known, and what
  // follows them is drawn with them, so the one that arrives second does not move what is under the first.
  const waiting = keys.isPending || hosts.isPending;
  if (waiting) {
    return (
      <LoadingRegion label={t("sources.server.label")}>
        <Skeleton className="h-64 w-full rounded-card" />
      </LoadingRegion>
    );
  }
  const hasKeys = keys.data !== undefined && keys.data.keys.length > 0;
  return (
    <>
      <ReadOnlyLine permission={HOST_ACCESS} />
      <SurfaceSection title={t("sources.server.title")} icon={OBJECT_ICONS.key} description={t("sources.server.description")} flush={hasKeys}>
        <QueryState
          query={keys}
          label={t("sources.server.label")}
          skeleton={<Skeleton className="h-16 w-full rounded-card" />}
          isEmpty={(data) => data.keys.length === 0}
          empty={
            <FirstUse
              action={
                <Link to="/apps" className={buttonClassName("secondary", "md")}>
                  {t("sources.server.goToApps")}
                </Link>
              }
            >
              {t("sources.server.firstUse")}
            </FirstUse>
          }
        >
          {(data) => <DataTable embedded caption={t("sources.server.caption")} columns={keyColumns(t)} rows={data.keys} getRowId={(key) => key.name} density="compact" />}
        </QueryState>
      </SurfaceSection>
      <TrustedHosts canManage={canManage} />
      {canManage ? (
        <SurfaceSection title={t("sources.server.hosts.pinTitle")} icon={ICONS.add} description={t("sources.server.hosts.pinDescription")} help={t("sources.server.hosts.pinHelp")}>
          <PinHostForm />
        </SurfaceSection>
      ) : null}
    </>
  );
}
