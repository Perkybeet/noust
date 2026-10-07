import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { PackageCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { appKeys } from "../../api/queries/apps";
import { isJobFinished, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { installedRecipesQuery, recipeKeys, recipesQuery } from "../../api/queries/recipes";
import type { InstalledRecipe } from "../../api/queries/recipes";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { DatabaseJobProgress } from "../databases/jobs";
import { CatalogFrame } from "./CatalogFrame";
import { RecipeLogo } from "./RecipeLogo";
import { RecipeUpdateDialog } from "./RecipeUpdateDialog";
import type { QueuedUpdate } from "./RecipeUpdateDialog";
import { installedState, versionLabel } from "./model";

/** The version column's words: `v1.37.4`, or what a recipe that pins none was recorded as. */
function versionWords(version: string | null | undefined, revision: number | null | undefined): string {
  return versionLabel(version, revision) ?? version ?? "";
}

/** Where an installed recipe stands, as the console's state language says it. */
function StandingPill({ item, t }: { item: InstalledRecipe; t: T }) {
  const state = installedState(item);
  if (state === "behind") return <StatusPill state="warning" label={t("catalog.installed.behind")} appearance="inline" size="sm" />;
  if (state === "current") return <StatusPill state="running" label={t("catalog.installed.current")} appearance="inline" size="sm" />;
  return <StatusPill state="unknown" label={t("catalog.installed.stuck")} appearance="inline" size="sm" />;
}

/**
 * What was installed from a recipe, against the catalogue (the second tab of the catalogue): the
 * version each runs and the catalogue's, whether it is behind and what changed, and the update,
 * which is the one update the rest of Noust has - a copy first, a startup check, the previous
 * version back when it fails - run as a job whose output is a click away. Coolify copies a
 * template when a service is created and never looks at it again; this page is the answer.
 */
export function InstalledRecipesPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const installed = useQuery(installedRecipesQuery());
  const catalogue = useQuery(recipesQuery());
  const followed = useFollowedJob();
  const [updating, setUpdating] = useState<InstalledRecipe | null>(null);
  const [queued, setQueued] = useState<string | null>(null);

  const recipes = useMemo(() => new Map((catalogue.data?.items ?? []).map((recipe) => [recipe.name, recipe])), [catalogue.data]);
  const job = followed.job;
  const finished = job !== null && isJobFinished(job);

  // What a finished update changed is read again once: the versions, the apps' states.
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !finished || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: recipeKeys.installed });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
  }, [job, finished, queryClient]);

  const onQueued = (accepted: QueuedUpdate, item: InstalledRecipe): void => {
    followed.follow(accepted.job === undefined ? accepted.job_id : (accepted.job as Job));
    setQueued(item.domain);
    announce(t("catalog.update.queued", { domain: item.domain }));
  };

  const busy = job !== null && !finished;
  const columns: Column<InstalledRecipe>[] = [
    {
      id: "domain",
      header: t("catalog.installed.columns.application"),
      cell: (row) => (
        <Link to="/apps/$domain" params={{ domain: row.domain }} className="font-medium text-fg hover:underline hover:underline-offset-2">
          <Mono>{row.domain}</Mono>
        </Link>
      ),
      sortValue: (row) => row.domain,
    },
    {
      id: "state",
      header: t("catalog.installed.columns.state"),
      width: "w-44",
      card: "status",
      cell: (row) => <StandingPill item={row} t={t} />,
      sortValue: (row) => ({ behind: 0, stuck: 1, current: 2 })[installedState(row)],
    },
    {
      id: "recipe",
      header: t("catalog.installed.columns.recipe"),
      cell: (row) => {
        const recipe = recipes.get(row.recipe);
        return (
          <span className="flex min-w-0 items-center gap-2">
            <RecipeLogo logo={recipe?.logo} title={recipe?.title ?? row.recipe} size="sm" />
            <span translate="no" className="min-w-0 truncate">
              {recipe?.title ?? row.recipe}
            </span>
          </span>
        );
      },
      sortValue: (row) => recipes.get(row.recipe)?.title ?? row.recipe,
    },
    {
      id: "installed",
      header: t("catalog.installed.columns.installed"),
      mono: true,
      width: "w-32",
      cell: (row) => versionWords(row.installed_version, row.installed_revision),
    },
    {
      id: "catalogue",
      header: t("catalog.installed.columns.catalogue"),
      mono: true,
      width: "w-32",
      hideBelow: "sm",
      cell: (row) => (row.latest_version === null || row.latest_version === undefined ? t("catalog.installed.gone") : versionWords(row.latest_version, row.latest_revision)),
    },
    {
      id: "why",
      header: t("catalog.installed.columns.changes"),
      hideBelow: "lg",
      card: "hidden",
      cell: (row) => {
        if (installedState(row) === "stuck") return <span className="text-pretty text-fg-muted">{row.reason ?? t("catalog.installed.goneReason")}</span>;
        const [latest] = [...row.changes].reverse();
        if (latest === undefined) return <span className="text-fg-muted">{installedState(row) === "behind" ? t("catalog.installed.noChanges") : ""}</span>;
        return (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-fg">{latest.summary}</span>
            {row.changes.length > 1 ? <span className="text-12 text-fg-muted">{t("catalog.installed.moreChanges", { count: row.changes.length - 1 })}</span> : null}
          </span>
        );
      },
    },
  ];

  const body = installed.data === undefined && installed.isError ? (
    <ErrorBlock error={installed.error} title={t("catalog.installed.loadFailed")} onRetry={() => void installed.refetch()} retrying={installed.isFetching} />
  ) : installed.data?.items.length === 0 ? (
    <EmptyState
      variant="firstUse"
      icon={<PackageCheck />}
      title={t("catalog.installed.emptyTitle")}
      description={t("catalog.installed.emptyDescription")}
      action={
        <Link to="/catalog" className={buttonClassName("secondary")}>
          {t("catalog.installed.browse")}
        </Link>
      }
      command="noust recipe install vaultwarden -d vault.example.com"
    />
  ) : (
    <DataTable
      columns={columns}
      rows={installed.data?.items ?? []}
      getRowId={(row) => row.domain}
      caption={t("catalog.installed.caption")}
      mobile="cards"
      loading={installed.data === undefined}
      skeletonRows={4}
      defaultSort={{ column: "state", direction: "ascending" }}
      pagination="installed-recipes"
      rowActions={(row) =>
        installedState(row) === "behind" ? (
          <Button size="sm" disabled={busy} onClick={() => setUpdating(row)} aria-label={t("catalog.installed.updateNamed", { domain: row.domain })}>
            {t("catalog.installed.update")}
          </Button>
        ) : null
      }
    />
  );

  return (
    <CatalogFrame
      command="noust recipe outdated"
      notice={
        job !== null ? (
          <DatabaseJobProgress
            job={job}
            words={{
              running: t("catalog.update.running", { domain: queued ?? "" }),
              succeeded: t("catalog.update.succeeded", { domain: queued ?? "" }),
              failed: t("catalog.update.failedJob", { domain: queued ?? "" }),
            }}
            {...(finished ? { onDismiss: followed.dismiss } : {})}
          />
        ) : undefined
      }
    >
      {body}
      <RecipeUpdateDialog item={updating} onOpenChange={(open) => !open && setUpdating(null)} onQueued={onQueued} />
    </CatalogFrame>
  );
}

