/**
 * One stack: its containers, the document it runs from and what it touches (its volumes and networks). Whose it is decides what
 * the header offers: an application's is changed from the application (its Compose, its updates, its way back), and what nobody of
 * Noust made is started and stopped here and brought to Noust to be edited or updated with a net.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { dockerStackQuery } from "../../api/queries/docker";
import { dockerComposeQuery, dockerComposeVersionsQuery } from "../../api/queries/dockerChanges";
import { Can, useCan } from "../../components/page/permissions";
import type { DockerContainer } from "../../api/queries/docker";
import { DetailPage } from "../../components/page/DetailPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { OwnerMark } from "../../components/page/OwnerMark";
import { RelativeTime } from "../../components/page/RelativeTime";
import { ErrorBlock } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { useNode } from "../../nodes/useNode";
import { useStackVerb } from "./actions";
import { BringDialog } from "./BringDialog";
import { COMPOSE_WHY_CODES, ownerOf, ownerTarget, stackVerbs } from "./model";
import { ContainerLink, ContainerPill, StackPill } from "./pieces";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

/** What a stack touches: the names of the volumes and the networks its containers use, each once. */
export function touchesOf(containers: readonly DockerContainer[]): { volumes: string[]; networks: string[] } {
  const volumes = new Set<string>();
  const networks = new Set<string>();
  for (const container of containers) {
    for (const mount of container.mounts ?? []) volumes.add(mount);
    for (const network of container.networks ?? []) networks.add(network);
  }
  return { volumes: [...volumes].sort(), networks: [...networks].sort() };
}

export function StackPage({ name }: { name: string }) {
  const t = useT();
  const query = useQuery(dockerStackQuery(name));
  const detail = query.data;
  const stack = detail?.stack;
  const containers = detail?.containers ?? [];
  const owner = stack === null || stack === undefined ? null : ownerOf(stack.owner);
  const target = stack === null || stack === undefined ? undefined : ownerTarget(stack.owner);
  const touches = touchesOf(containers);
  const { node } = useNode();
  const start = useStackVerb("start");
  const stop = useStackVerb("stop");
  const [stopping, setStopping] = useState(false);
  const [bringing, setBringing] = useState<string | null>(null);
  const theirs = owner !== null && owner.kind === "nobody";
  // Whether the document is edited from here is the server's to say, and reading it is a reveal: whoever may not edit is not asked for it.
  // The versions are of a document Noust keeps; of any other the server answers a 409, which the browser would report as an error.
  const can = useCan();
  const mayEdit = can("docker.manage") && can("secrets.reveal");
  const compose = useQuery({ ...dockerComposeQuery(name), enabled: detail?.available === true && owner !== null && !theirs && mayEdit });
  const editable = compose.data?.editable === true;
  const versions = useQuery({ ...dockerComposeVersionsQuery(name), enabled: editable });
  const saved = versions.data?.items ?? [];
  const whyCode = compose.data === undefined || editable ? null : (COMPOSE_WHY_CODES.find((code) => code === compose.data.why_code) ?? null);
  const last = saved[0];
  const verbs = stack === null || stack === undefined ? null : stackVerbs(stack);
  const columns: Column<DockerContainer>[] = [
    { id: "name", header: t("docker.columns.container"), card: "title", cell: (row) => <ContainerLink name={row.name} /> },
    { id: "state", header: t("docker.columns.state"), card: "status", width: "w-44", cell: (row) => <ContainerPill kind={row.state_kind} /> },
    { id: "service", header: t("docker.columns.service"), card: "meta", hideBelow: "sm", cell: (row) => (row.service === "" ? "" : <Mono>{row.service}</Mono>) },
    { id: "image", header: t("docker.columns.image"), card: "meta", hideBelow: "md", cell: (row) => <Mono truncate>{row.image}</Mono> },
  ];
  const primary: ReactNode =
    owner?.kind === "app" ? (
      <Link to="/apps/$domain" params={{ domain: owner.subject }} className={buttonClassName("primary")}>
        {t("docker.container.openApp")}
      </Link>
    ) : theirs ? (
      <Can permission="docker.manage">
        <Button variant="primary" onClick={() => setBringing(name)}>
          {t("docker.menu.bring")}
        </Button>
      </Can>
    ) : undefined;
  // What nobody of Noust made is started and stopped here; an application's stack is changed from the application.
  const secondary: ReactNode =
    verbs === null ? undefined : verbs.direct.includes("stop") ? (
      <Can permission="docker.manage">
        <Button variant="secondary" icon={<VERB_ICONS.turnOff aria-hidden="true" />} onClick={() => setStopping(true)}>
          {t("common.words.stop")}
        </Button>
      </Can>
    ) : verbs.direct.includes("start") ? (
      <Can permission="docker.manage">
        <Button variant="secondary" icon={<VERB_ICONS.turnOn aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate(name)}>
          {t("common.words.start")}
        </Button>
      </Can>
    ) : undefined;
  const files = stack?.config_files ?? [];
  const facts: KeyValueItem[] = [
    ...(stack?.working_dir !== undefined && stack.working_dir !== "" ? [{ label: t("docker.stack.folder"), value: stack.working_dir, mono: true }] : []),
    { label: t("docker.stack.volumes"), value: touches.volumes.length === 0 ? t("docker.container.facts.none") : touches.volumes.join(", "), mono: touches.volumes.length > 0 },
    { label: t("docker.stack.networks"), value: touches.networks.length === 0 ? t("docker.container.facts.none") : touches.networks.join(", "), mono: touches.networks.length > 0 },
  ];
  // The document the stack runs from: where it is, when it was last edited and how many are saved to go back to.
  const composeRows: KeyValueItem[] = [
    ...(files.length > 0 ? [{ label: t("docker.stack.composeFile"), value: files.join(", "), mono: true }] : []),
    // A stack nobody made has no document of Noust's; one Noust keeps has its edits; the rest has no document to speak of.
    ...(theirs
      ? [
          { label: t("docker.stack.lastEdit"), value: t("docker.stack.neverSeen"), mono: false, copy: false as const },
          { label: t("docker.stack.savedDocuments"), value: t("docker.container.facts.none"), mono: false, copy: false as const },
        ]
      : editable
        ? [
            {
              label: t("docker.stack.lastEdit"),
              mono: false,
              copy: false as const,
              value:
                last === undefined
                  ? t("docker.stack.neverEdited")
                  : t.rich("docker.stack.lastEditValue", { when: <RelativeTime key="when" value={last.created_at} />, who: last.actor ?? t("docker.stack.byNoust") }),
            },
            {
              label: t("docker.stack.savedDocuments"),
              mono: false,
              copy: false as const,
              value: saved.length === 0 ? t("docker.container.facts.none") : t("docker.stack.versions", { count: saved.length }),
              ...(saved.length === 0 ? { hint: t("docker.stack.noVersions") } : {}),
            },
          ]
        : []),
  ];
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <DetailPage
        header={{
          title: name,
          mono: true,
          ...(stack !== null && stack !== undefined ? { status: <StackPill state={stack.state} appearance="pill" size="md" /> } : {}),
          ...(owner !== null ? { description: <OwnerMark variant="sentence" kind={owner.kind} subject={owner.subject} {...(target !== undefined ? { target } : {})} /> } : {}),
          ...(secondary !== undefined ? { secondaryActions: secondary } : {}),
          ...(primary !== undefined ? { primaryAction: primary } : {}),
        }}
      >
        {query.isError && detail === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.stack.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : detail === undefined ? (
          <LoadingRegion label={t("docker.stack.loading")}>
            <Skeleton className="h-64 w-full" />
          </LoadingRegion>
        ) : !detail.available ? (
          <Unavailable engine={detail.engine} />
        ) : (
          <Sections surfaces>
            <SurfaceSection title={t("docker.stack.containers")} icon={OBJECT_ICONS.container} description={t("docker.stack.containersLine", { running: stack?.running ?? 0, total: stack?.total ?? 0 })} flush>
              <DataTable
                caption={t("docker.stack.containersCaption", { name })}
                columns={columns}
                rows={containers}
                getRowId={(row) => row.id}
                embedded
              />
            </SurfaceSection>
            <SurfaceSection
              title={t("docker.stack.composeTitle")}
              icon={OBJECT_ICONS.compose}
              description={theirs ? t("docker.stack.composeTheirs") : whyCode !== null ? t(`docker.compose.why.${whyCode}`) : t("docker.stack.composeLine")}
              {...(!editable
                ? {}
                : {
                    actions: (
                      // Reading and judging a document is a reveal, and applying it changes Docker: whoever lacks either does not see the way in.
                      <Can permission="docker.manage">
                        <Can permission="secrets.reveal">
                          <Link to="/server/docker/compose/$name" params={{ name }} className={buttonClassName("secondary", "sm")}>
                            {t("docker.stack.edit")}
                          </Link>
                        </Can>
                      </Can>
                    ),
                  })}
            >
              {composeRows.length > 0 ? <KeyValueList items={composeRows} /> : null}
            </SurfaceSection>
            <SurfaceSection title={t("docker.stack.touchesTitle")} icon={OBJECT_ICONS.volume}>
              <KeyValueList items={facts} />
            </SurfaceSection>
          </Sections>
        )}
        <Can permission="docker.manage">
          <ConfirmDialog
            friction="simple"
            open={stopping}
            onOpenChange={setStopping}
            title={t("docker.stack.stopTitle", { name })}
            description={t("docker.stack.stopDescription", { count: stack?.running ?? 0 })}
            actionLabel={t("common.words.stop")}
            {...(node !== null ? { server: node } : {})}
            onConfirm={async () => {
              await stop.mutateAsync(name);
            }}
          />
        </Can>
        <BringDialog stack={bringing} onClose={() => setBringing(null)} />
      </DetailPage>
    </NodeCapabilityGate>
  );
}
