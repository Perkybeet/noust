import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Database as DatabaseIcon, KeyRound, Link2, Unlink } from "lucide-react";
import { useEffect, useState } from "react";

import { request } from "../../../api/client";
import { appDatabasesQuery, databaseKeys, enginesQuery } from "../../../api/queries/databases";
import type { DatabaseLink } from "../../../api/queries/databases";
import { activeJobsQuery, isJobFinished, jobKeys, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { sizeWords } from "../../databases/DatabasesTable";
import { engineName } from "../../databases/engines";
import { DatabaseJobProgress } from "../../databases/jobs";
import type { Accepted } from "../../databases/jobs";
import { LinkDialog } from "../../databases/LinkDialog";
import { SecretShown } from "../../databases/users/SecretShown";
import type { SecretShownProps } from "../../databases/users/SecretShown";
import { TEXT_LINK } from "../../databases/ui";
import { forgetPending, pendingFor } from "../../databases/wizard/pending";
import { CreateLinkDialog } from "./CreateLinkDialog";

type Kind = "provision" | "link" | "unlink" | "rotate" | "working";

function words(t: T, kind: Kind, label: string, domain: string) {
  switch (kind) {
    case "provision":
      return { running: t("databases.appTab.job.provision.running", { domain }), succeeded: t("databases.appTab.job.provision.succeeded", { domain }), failed: t("databases.appTab.job.provision.failed", { domain }) };
    case "link":
      return { running: t("databases.appTab.job.link.running", { name: label }), succeeded: t("databases.appTab.job.link.succeeded", { name: label }), failed: t("databases.appTab.job.link.failed", { name: label }) };
    case "unlink":
      return { running: t("databases.appTab.job.unlink.running", { name: label }), succeeded: t("databases.appTab.job.unlink.succeeded", { name: label }), failed: t("databases.appTab.job.unlink.failed", { name: label }) };
    case "rotate":
      return { running: t("databases.job.rotate.running", { name: label }), succeeded: t("databases.job.rotate.succeeded", { name: label }), failed: t("databases.job.rotate.failed", { name: label }) };
    case "working":
      return { running: t("databases.appTab.job.working.running", { domain }), succeeded: t("databases.appTab.job.working.succeeded", { domain }), failed: t("databases.appTab.job.working.failed", { domain }) };
  }
}

const RUNNING = new Set(["pending", "running"]);

/** One linked database: what it is, the variable carrying it, and what can be done to the link. */
function LinkCard({
  link,
  t,
  busy,
  engines,
  onShow,
  onRotate,
  onUnlink,
}: {
  link: DatabaseLink;
  t: T;
  busy: boolean;
  engines: Parameters<typeof engineName>[1];
  onShow: () => void;
  onRotate: () => void;
  onUnlink: () => void;
}) {
  const size = sizeWords(link.size, t);
  return (
    <Card
      padding="sm"
      as="li"
      level={2}
      title={
        <Link to="/databases/$engine/$name" params={{ engine: link.engine, name: link.database }} translate="no" className={TEXT_LINK}>
          {link.database}
        </Link>
      }
      description={[`${engineName(link.engine, engines)}${link.engine_version ? ` ${link.engine_version}` : ""}`, size].filter((part) => part !== null && part !== "").join(" · ")}
      actions={
        <>
          {link.username ? (
            <Button size="sm" variant="ghost" icon={<KeyRound aria-hidden="true" />} disabled={busy} onClick={onRotate}>
              {t("databases.appTab.rotate")}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" icon={<Unlink aria-hidden="true" />} disabled={busy} onClick={onUnlink}>
            {t("databases.appTab.unlink")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {!link.exists ? <Notice tone="warning" title={t("databases.appTab.gone")}>{t("databases.appTab.goneBody")}</Notice> : null}
        <KeyValueList
          items={[
            { label: t("databases.appTab.variable"), value: link.env_var || null, hint: link.extra_vars ? t("databases.appTab.extraVars") : undefined },
            {
              label: t("databases.connect.connectionString"),
              value: link.url ? (
                <span className="flex min-w-0 flex-wrap items-center gap-x-2">
                  <Mono truncate>{link.url}</Mono>
                  <Button size="sm" variant="ghost" onClick={onShow}>
                    {t("databases.connect.showUrl")}
                  </Button>
                </span>
              ) : null,
              mono: true,
              copy: false,
              ...(link.url ? {} : { hint: t("databases.appTab.noUrl") }),
            },
            { label: t("databases.appTab.account"), value: link.username ?? null },
          ]}
          empty={t("databases.appTab.unknown")}
        />
      </div>
    </Card>
  );
}

/**
 * An application's databases: each one it uses, the variable its connection string is in (the
 * string itself shown in sudo mode), and what can be done to the link: create a database for
 * it, give it one that exists, rotate the password it signs in with, or take one away. Every
 * change restarts the application behind its startup check, which puts the previous
 * environment back if it does not come up; the job says so where it was started.
 */
export function AppDatabaseTab({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const links = useQuery(appDatabasesQuery(domain));
  const engines = useQuery(enginesQuery());
  const active = useQuery({ ...activeJobsQuery(), refetchInterval: 10_000 });
  const followed = useFollowedJob();
  const [tracked, setTracked] = useState<{ kind: Kind; label: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [linking, setLinking] = useState(false);
  const [unlinking, setUnlinking] = useState<DatabaseLink | null>(null);
  const [rotating, setRotating] = useState<DatabaseLink | null>(null);
  const [secret, setSecret] = useState<SecretShownProps["secret"]>(null);
  const pending = pendingFor(domain, node);

  const elsewhere: Job | null = active.data?.jobs.find((job) => job.type === "database" && job.metadata?.["domain"] === domain && RUNNING.has(job.status)) ?? null;
  const shown = followed.job ?? elsewhere;
  const kind: Kind = followed.job !== null ? (tracked?.kind ?? "working") : "working";
  const busy = shown !== null && RUNNING.has(shown.status);
  const list = links.data?.databases ?? [];

  const finishedId = shown !== null && isJobFinished(shown) ? shown.id : null;
  useEffect(() => {
    if (finishedId === null) return;
    void queryClient.invalidateQueries({ queryKey: databaseKeys.app(domain) });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
  }, [finishedId, queryClient, domain]);
  useEffect(() => {
    if (pending !== null && list.length > 0) forgetPending(domain, node);
  }, [pending, list.length, domain, node]);

  const track = (accepted: Accepted, next: Kind, label: string): void => {
    setTracked({ kind: next, label });
    const snapshot = accepted.job as Job | undefined;
    followed.follow(snapshot !== undefined && typeof snapshot.id === "string" ? snapshot : accepted.job_id);
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
  };

  const reveal = useMutation({
    mutationFn: (link: DatabaseLink) => request("post", "/api/apps/{domain}/databases/{engine}/{name}/url", { params: { domain, engine: link.engine, name: link.database } }),
    onSuccess: (result) =>
      setSecret({
        title: t("databases.connect.urlTitle", { domain }),
        description: t("databases.connect.urlDescription"),
        fields: [{ label: t("databases.connect.connectionString"), value: result.url }],
      }),
    onError: (error) => reportActionError(t("databases.connect.revealFailed"), error),
  });

  const Add = ICONS.add;
  const actions = (
    <div className="flex flex-wrap gap-2">
      <Button icon={<Add aria-hidden="true" />} disabled={busy} onClick={() => setCreating(true)}>
        {t("databases.appTab.create")}
      </Button>
      <Button icon={<Link2 aria-hidden="true" />} disabled={busy} onClick={() => setLinking(true)}>
        {t("databases.appTab.linkExisting")}
      </Button>
    </div>
  );

  return (
    <div className="flex min-w-0 flex-col gap-6">
      {shown !== null ? (
        <DatabaseJobProgress
          job={shown}
          words={words(t, kind, tracked?.label ?? "", domain)}
          {...(followed.job !== null
            ? {
                onDismiss: () => {
                  followed.dismiss();
                  setTracked(null);
                },
              }
            : {})}
        />
      ) : null}
      {pending !== null && list.length === 0 && shown === null ? (
        <Notice
          title={t("databases.appTab.pendingTitle", { engine: engineName(pending.engine, engines.data?.engines) })}
          action={
            <Button size="sm" onClick={() => setCreating(true)}>
              {t("databases.appTab.create")}
            </Button>
          }
        >
          {t("databases.appTab.pendingBody")}
        </Notice>
      ) : null}

      {links.isError && links.data === undefined ? (
        <ErrorBlock error={links.error} title={t("databases.appTab.loadFailed")} onRetry={() => void links.refetch()} retrying={links.isRefetching} />
      ) : links.data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-4">
          <span className="sr-only">{t("databases.appTab.loading")}</span>
          <Skeleton className="h-36 w-full rounded-card" />
        </div>
      ) : list.length === 0 ? (
        <EmptyState
          variant="firstUse"
          icon={<DatabaseIcon />}
          title={t("databases.appTab.emptyTitle")}
          description={t("databases.appTab.emptyDescription")}
          action={actions}
          command={`noust db provision ${domain} -e postgresql`}
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="max-w-measure text-13 text-fg-muted">{t("databases.appTab.intro")}</p>
            {actions}
          </div>
          <ul className="grid gap-4 lg:grid-cols-2">
            {list.map((link) => (
              <LinkCard
                key={`${link.engine}/${link.database}`}
                link={link}
                t={t}
                busy={busy}
                engines={engines.data?.engines}
                onShow={() => reveal.mutate(link)}
                onRotate={() => setRotating(link)}
                onUnlink={() => setUnlinking(link)}
              />
            ))}
          </ul>
        </>
      )}

      {list.length > 0 ? <CommandHint command={`noust db links ${domain}`} label={t("databases.common.fromTerminal")} /> : null}

      <CreateLinkDialog domain={domain} open={creating} onOpenChange={setCreating} onQueued={(accepted) => track(accepted, "provision", domain)} />
      <LinkDialog domain={domain} open={linking} onOpenChange={setLinking} onQueued={(accepted, _domain, database) => track(accepted, "link", database)} />
      {rotating?.username ? (
        <ConfirmDialog
          friction="simple"
          destructive={false}
          open
          onOpenChange={(open) => (open ? undefined : setRotating(null))}
          title={t("databases.users.rotateTitle", { user: rotating.username })}
          description={t("databases.appTab.rotateDescription", { user: rotating.username, domain })}
          actionLabel={t("databases.users.rotateAction")}
          server={node}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/users/{engine}/{username}/password", {
              params: { engine: rotating.engine, username: rotating.username ?? "" },
              body: { propagate: true, host: "localhost", first_password: false },
            });
            track(accepted, "rotate", rotating.username ?? "");
          }}
        />
      ) : null}
      {unlinking !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(open) => (open ? undefined : setUnlinking(null))}
          title={t("databases.appTab.unlinkTitle", { name: unlinking.database })}
          description={t("databases.appTab.unlinkDescription", { name: unlinking.database, domain, variable: unlinking.env_var || "DATABASE_URL" })}
          actionLabel={t("databases.appTab.unlinkAction")}
          server={node}
          onConfirm={async () => {
            const accepted = await request("delete", "/api/apps/{domain}/databases/{engine}/{name}", {
              params: { domain, engine: unlinking.engine, name: unlinking.database },
              query: { drop: false, restart: true },
            });
            track(accepted, "unlink", unlinking.database);
          }}
        />
      ) : null}
      <SecretShown secret={secret} onClose={() => setSecret(null)} />
    </div>
  );
}
