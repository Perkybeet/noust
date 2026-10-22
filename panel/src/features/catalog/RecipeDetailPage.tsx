import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Container } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { isApiError, request } from "../../api/client";
import { recipeKeys, recipeQuery } from "../../api/queries/recipes";
import type { Recipe, RecipeVariableInfo } from "../../api/queries/recipes";
import { DetailPage } from "../../components/page/DetailPage";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDate } from "../../lib/format";
import { joinList } from "../new-app/wizard";
import { useNode } from "../../nodes/useNode";
import { ORIGIN_LABELS, dayOf, previewNote, versionLabel } from "./model";
import { RecipeLogo } from "./RecipeLogo";
import { RecipeRequirements } from "./RecipeRequirements";
import { announce } from "../../app/Announcer";
import { categoryWords, dependencyWords } from "./RecipeCard";

/** Where a recipe's code comes from, or what it runs, as the rows of the facts card. */
function runsItems(t: T, recipe: Recipe): KeyValueItem[] {
  const items: KeyValueItem[] = [];
  items.push({
    label: t("catalog.detail.runsAs"),
    value: recipe.runs_in_docker ? t("catalog.detail.runsDocker") : t("catalog.detail.runsNative"),
    mono: false,
    copy: false,
  });
  const source = recipe.source ?? null;
  if (source?.url !== null && source?.url !== undefined) {
    const pin =
      source.ref !== null && source.ref !== undefined
        ? t("newApp.recipes.sourceTag", { ref: source.ref })
        : source.sha256 !== null && source.sha256 !== undefined
          ? t("newApp.recipes.sourceSha", { sha: source.sha256 })
          : source.checksum_url !== null && source.checksum_url !== undefined
            ? t("newApp.recipes.sourceChecksum", { checksum: source.checksum_url })
            : null;
    items.push({ label: t("newApp.recipes.source"), value: source.url, ...(pin !== null ? { hint: pin } : {}) });
  } else if (source !== null && source.files.length > 0) {
    items.push({ label: t("newApp.recipes.source"), value: t("newApp.recipes.sourceTemplate", { files: joinList(source.files, t.locale) }), mono: false, copy: false });
  }
  items.push({
    label: t("newApp.recipes.deploys"),
    value: t(recipe.layout === "inplace" ? "newApp.recipes.inplace" : "newApp.recipes.releases"),
    mono: false,
    copy: false,
  });
  const health = recipe.health ?? null;
  if (health !== null) {
    const expect = health.expect ?? null;
    items.push({
      label: t("newApp.recipes.health"),
      value: expect !== null ? t("newApp.recipes.healthExpect", { path: health.path, expect }) : t("newApp.recipes.healthValue", { path: health.path }),
      copy: false,
    });
  }
  return items;
}

/** What identifies the recipe as a published thing: its version, when it changed, its licence. */
function factsItems(t: T, recipe: Recipe): KeyValueItem[] {
  const version = versionLabel(recipe.version, recipe.revision);
  const updated = dayOf(recipe.updated);
  const items: KeyValueItem[] = [];
  if (version !== null) items.push({ label: t("catalog.detail.version"), value: version });
  if (updated !== null) items.push({ label: t("catalog.detail.updated"), value: formatDate(updated, {}, t.locale), mono: false, copy: false });
  items.push({ label: t("catalog.detail.category"), value: categoryWords(t, recipe.category), mono: false, copy: false });
  if (recipe.license !== null && recipe.license !== undefined) items.push({ label: t("catalog.detail.license"), value: recipe.license });
  if (recipe.architectures.length > 0) items.push({ label: t("catalog.detail.architectures"), value: joinList(recipe.architectures, t.locale), mono: false, copy: false });
  if (recipe.min_noust !== null && recipe.min_noust !== undefined) items.push({ label: t("catalog.detail.minNoust"), value: recipe.min_noust });
  return items;
}

function variableColumns(t: T): Column<RecipeVariableInfo>[] {
  return [
    { id: "name", header: t("catalog.detail.variables.name"), cell: (row) => <Mono>{row.name}</Mono>, sortValue: (row) => row.name },
    {
      id: "origin",
      header: t("catalog.detail.variables.origin"),
      width: "w-56",
      cell: (row) => {
        const key = ORIGIN_LABELS[row.origin];
        return (
          <span className="flex flex-wrap items-center gap-1.5">
            <span>{key === undefined ? row.origin : t(key)}</span>
            {row.required ? <Badge>{t("catalog.detail.variables.required")}</Badge> : null}
            {row.secret ? <Badge>{t("catalog.detail.variables.secret")}</Badge> : null}
          </span>
        );
      },
    },
    {
      id: "default",
      header: t("catalog.detail.variables.default"),
      width: "w-40",
      hideBelow: "md",
      card: "hidden",
      // A secret has no default to show, and a default is the recipe's, never a secret.
      cell: (row) => (row.default !== null && row.default !== undefined && !row.secret ? <Mono truncate>{row.default === "" ? "\"\"" : row.default}</Mono> : null),
    },
    {
      id: "description",
      header: t("catalog.detail.variables.purpose"),
      hideBelow: "lg",
      card: "meta",
      cell: (row) => <span className="text-pretty text-fg-muted">{row.description}</span>,
    },
  ];
}

function DetailSkeleton() {
  const t = useT();
  return (
    <LoadingRegion label={t("catalog.detail.loading")} className="flex flex-col gap-8">
      <Skeleton className="h-40 w-full rounded-card" />
      <Skeleton className="h-32 w-full rounded-card" />
    </LoadingRegion>
  );
}

/**
 * One recipe in full (T2 without tabs): what it is and where it comes from, what it needs from
 * the server, every variable and where its value comes from (the ones it asks for, the ones it
 * generates, the secrets), what it opens to the network, what it tells the operator after the
 * install, and what each version changed. The install itself is the wizard's, with the address
 * and the variables asked; this page leads to it and shows no secret and no default of one.
 */
export function RecipeDetailPage({ name }: { name: string }) {
  const t = useT();
  const navigate = useNavigate();
  const { node } = useNode();
  const query = useQuery(recipeQuery(name));
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const recipe = query.data;

  const use = (): void => {
    void navigate({ to: "/apps/new", search: { start: "recipe", recipe: name } });
  };
  const header = {
    title: recipe?.title ?? name,
    breadcrumbs: [{ label: t("catalog.page.title"), to: "/catalog" }],
    server: node,
    ...(recipe !== undefined
      ? {
          description: recipe.slogan !== "" ? recipe.slogan : recipe.description,
          meta: (
            <>
              {versionLabel(recipe.version, recipe.revision) !== null ? <Badge mono>{versionLabel(recipe.version, recipe.revision)}</Badge> : null}
              <Badge>{categoryWords(t, recipe.category)}</Badge>
              {recipe.origin === "operator" ? <Badge>{t("catalog.card.yours")}</Badge> : null}
              {recipe.origin === "catalog" ? <Badge>{t("catalog.card.package")}</Badge> : null}
              {recipe.runs_in_docker ? (
                <Badge>
                  <Container aria-hidden="true" className="size-icon-xs" />
                  {t("catalog.card.docker")}
                </Badge>
              ) : null}
            </>
          ),
          ...(recipe.origin === "operator"
            ? {
                secondaryActions: (
                  <Button variant="ghost" onClick={() => setRemoving(true)}>
                    {t("catalog.own.remove")}
                  </Button>
                ),
              }
            : {}),
          ...(recipe.available
            ? {
                primaryAction: (
                  <Button variant="primary" onClick={use}>
                    {t("newApp.recipes.use", { title: recipe.title })}
                  </Button>
                ),
              }
            : {}),
        }
      : {}),
  };

  let body: ReactNode;
  if (recipe === undefined) {
    body =
      query.isError && isApiError(query.error) && query.error.status === 404 ? (
        <ErrorBlock error={query.error} title={t("catalog.detail.notFound", { name })} hint={t("catalog.detail.notFoundHint")} />
      ) : query.isError ? (
        <ErrorBlock error={query.error} title={t("newApp.recipes.openFailed", { title: name })} onRetry={() => void query.refetch()} retrying={query.isFetching} />
      ) : (
        <DetailSkeleton />
      );
  } else {
    const needs = dependencyWords(t, recipe.dependencies);
    const links = (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <ExternalLink href={recipe.homepage} inline unlinked="text" className="text-13">
          {t("catalog.card.website")}
        </ExternalLink>
        {recipe.docs !== null && recipe.docs !== undefined && recipe.docs !== recipe.homepage ? (
          <ExternalLink href={recipe.docs} inline className="text-13">
            {t("catalog.card.docs")}
          </ExternalLink>
        ) : null}
        {recipe.repository !== null && recipe.repository !== undefined ? (
          <ExternalLink href={recipe.repository} inline className="text-13">
            {t("catalog.detail.repository")}
          </ExternalLink>
        ) : null}
      </div>
    );
    body = (
      <Sections>
        {recipe.available ? null : (
          <Notice tone="warning" title={t("newApp.recipes.unavailable")}>
            {recipe.unavailable_reason ?? ""}
          </Notice>
        )}

        <Section title={t("catalog.detail.about")}>
          <Card padding="md">
            <div className="flex flex-col gap-4">
              <div className="flex items-start gap-4">
                <RecipeLogo logo={recipe.logo} title={recipe.title} size="lg" />
                <div className="flex min-w-0 flex-col gap-2">
                  <p className="max-w-measure text-14 text-pretty text-fg">{recipe.description}</p>
                  {links}
                  {recipe.tags.length > 0 ? (
                    <ul aria-label={t("catalog.card.tags")} className="flex flex-wrap gap-1.5">
                      {recipe.tags.map((tag) => (
                        <li key={tag} translate="no">
                          <Badge>{tag}</Badge>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              </div>
              <KeyValueList items={factsItems(t, recipe)} />
            </div>
          </Card>
        </Section>

        <Section title={t("catalog.detail.runs")} description={t("catalog.detail.runsDescription")}>
          <Card padding="none">
            <div className="px-4 py-1">
              <KeyValueList items={runsItems(t, recipe)} />
            </div>
          </Card>
          {recipe.images.length > 0 ? (
            <DataTable
              caption={t("catalog.detail.images.caption")}
              rows={recipe.images}
              getRowId={(row) => row.service}
              mobile="cards"
              columns={[
                { id: "service", header: t("catalog.detail.images.service"), cell: (row) => <Mono>{row.service}</Mono>, card: "title" },
                { id: "image", header: t("catalog.detail.images.image"), cell: (row) => <Mono truncate>{`${row.repository}:${row.tag}`}</Mono> },
                {
                  id: "digest",
                  header: t("catalog.detail.images.digest"),
                  hideBelow: "md",
                  card: "hidden",
                  cell: (row) => (row.digest !== null && row.digest !== undefined ? <Mono truncate>{row.digest}</Mono> : <span className="text-fg-muted">{t("catalog.detail.images.unpinned")}</span>),
                },
              ]}
            />
          ) : null}
        </Section>

        <RecipeRequirements name={recipe.name} />

        {needs.length > 0 || recipe.requires.length > 0 ? (
          <Section title={t("catalog.detail.needs")} description={t("catalog.detail.needsDescription")}>
            <ul className="flex list-disc flex-col gap-1 pl-5 text-13 text-pretty text-fg marker:text-fg-faint">
              {needs.map((need) => (
                <li key={need}>{t("catalog.detail.provides", { service: need })}</li>
              ))}
              {recipe.requires.map((need) => (
                <li key={need}>{need}</li>
              ))}
            </ul>
          </Section>
        ) : null}

        {recipe.public_ports.length > 0 ? (
          <Section title={t("catalog.detail.ports")}>
            <Notice tone="warning" title={t("catalog.detail.portsTitle")}>
              <div className="flex flex-col gap-2">
                <p>{t("catalog.detail.portsDescription")}</p>
                <ul className="flex flex-col gap-1">
                  {recipe.public_ports.map((port) => (
                    <li key={`${String(port.port)}/${port.protocol}`} className="flex flex-wrap gap-x-2">
                      <Mono>{`${String(port.port)}/${port.protocol}`}</Mono>
                      <span>{port.purpose}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </Notice>
          </Section>
        ) : null}

        {recipe.variables.length > 0 ? (
          <Section title={t("catalog.detail.variables.title")} description={t("catalog.detail.variables.intro")}>
            <DataTable
              caption={t("catalog.detail.variables.caption")}
              rows={recipe.variables}
              getRowId={(row) => row.name}
              columns={variableColumns(t)}
              mobile="cards"
              pagination="recipe-variables"
            />
          </Section>
        ) : null}

        {recipe.notes.length > 0 ? (
          <Section title={t("catalog.detail.notes")} description={t("catalog.detail.notesDescription")}>
            <ol className="flex list-decimal flex-col gap-2 pl-5 text-13 text-pretty text-fg marker:text-fg-muted">
              {recipe.notes.map((note) => (
                <li key={note}>{previewNote(note)}</li>
              ))}
            </ol>
          </Section>
        ) : null}

        {recipe.changelog.length > 0 ? (
          <Section title={t("catalog.detail.changelog")}>
            <ol className="flex flex-col gap-2 text-13">
              {recipe.changelog.map((change) => (
                <li key={change.version} className="flex gap-3">
                  <Mono className="shrink-0">{change.version}</Mono>
                  <span className="text-pretty text-fg">{change.summary}</span>
                </li>
              ))}
            </ol>
          </Section>
        ) : null}
      </Sections>
    );
  }

  return (
    <>
      <DetailPage header={header}>{body}</DetailPage>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        destructive
        server={node}
        title={t("catalog.own.removeTitle", { title: recipe?.title ?? name })}
        description={t("catalog.own.removeDescription")}
        actionLabel={t("catalog.own.removeAction")}
        onConfirm={async () => {
          await request("delete", "/api/recipes/private/{name}", { params: { name } });
          void queryClient.invalidateQueries({ queryKey: recipeKeys.all });
          announce(t("catalog.own.removed", { title: recipe?.title ?? name }));
          void navigate({ to: "/catalog" });
        }}
      />
    </>
  );
}
