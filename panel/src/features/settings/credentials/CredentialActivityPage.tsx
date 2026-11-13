import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { activityQuery } from "../../../api/queries/credentials";
import type { ActivityList, Intervention } from "../../../api/queries/credentials";
import { useDocumentTitle } from "../../../app/documentTitle";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";

const DAYS = 30;

function kindOf(kind: string): "token" | "assistant" | "fleet" {
  return kind === "assistant" || kind === "fleet" ? kind : "token";
}

function columnsFor(t: T, id: number, open: (n: number) => void): Column<Intervention>[] {
  return [
    {
      id: "when",
      header: t("callerPolicy.activity.columns.when"),
      card: "title",
      sortValue: (item) => item.started_at,
      cell: (item) => (
        <span className="flex min-w-0 flex-col">
          <TextLink to="/settings/tokens/$id/$n" params={{ id: String(id), n: String(item.n) }} variant="row" size="ui">
            <RelativeTime value={item.started_at} />
          </TextLink>
          {item.ongoing ? <span className="text-12 text-fg-muted">{t("callerPolicy.activity.now")}</span> : null}
        </span>
      ),
    },
    { mono: true, id: "calls", header: t("callerPolicy.activity.columns.calls"), align: "end", cell: (item) => item.calls, sortValue: (item) => item.calls },
    { mono: true, id: "changes", header: t("callerPolicy.activity.columns.changes"), align: "end", cell: (item) => item.changes, sortValue: (item) => item.changes },
    { mono: true, id: "proposed", header: t("callerPolicy.activity.columns.proposed"), align: "end", hideBelow: "md", cell: (item) => item.proposed },
    { mono: true, id: "refused", header: t("callerPolicy.activity.columns.refused"), align: "end", hideBelow: "md", cell: (item) => item.refused },
    { mono: true, id: "failed", header: t("callerPolicy.activity.columns.failed"), align: "end", hideBelow: "md", cell: (item) => item.failed },
    {
      id: "apps",
      header: t("callerPolicy.activity.columns.apps"),
      hideBelow: "lg",
      card: "meta",
      cell: (item) => (
        <span className="flex min-w-0 flex-wrap gap-1">
          {item.apps.slice(0, 3).map((app) => (
            <Badge key={app} mono>
              {app}
            </Badge>
          ))}
          {item.apps.length > 3 ? <Badge>{`+${String(item.apps.length - 3)}`}</Badge> : null}
        </span>
      ),
    },
    { mono: true, id: "deployments", header: t("callerPolicy.activity.columns.deployments"), align: "end", hideBelow: "lg", cell: (item) => item.deployments },
    {
      id: "open",
      header: "",
      width: "w-24",
      card: "control",
      cell: (item) => (
        <Button size="sm" variant="ghost" aria-label={t("callerPolicy.activity.openLabel", { n: item.n })} onClick={() => open(item.n)}>
          {t("callerPolicy.activity.open")}
        </Button>
      ),
    },
  ];
}

function Credential({ data }: { data: ActivityList }) {
  const t = useT();
  const navigate = useNavigate();
  const credential = data.credential;
  const kind = kindOf(credential.kind);
  const title = kind === "assistant" ? t("callerPolicy.activity.assistantTitle", { owner: credential.owner ?? "" }) : t("callerPolicy.activity.title", { name: credential.name });
  useDocumentTitle(title, 2);
  const columns = columnsFor(t, credential.id, (n) => void navigate({ to: "/settings/tokens/$id/$n", params: { id: String(credential.id), n: String(n) } }));
  return (
    <Sections>
      <SurfaceSection title={title} description={t("callerPolicy.activity.description")} icon={kind === "assistant" ? OBJECT_ICONS.assistant : OBJECT_ICONS.token}>
        <KeyValueList
          items={[
            { label: t("callerPolicy.activity.facts.kind"), value: t(`callerPolicy.activity.kind.${kind}`), mono: false },
            ...(credential.owner !== null && credential.owner !== undefined ? [{ label: t("callerPolicy.activity.facts.owner"), value: credential.owner, mono: false }] : []),
            { label: t("callerPolicy.activity.facts.tokens"), value: String(credential.tokens), mono: false },
            ...((credential.clients ?? []).length > 0
              ? [{ label: t("callerPolicy.activity.facts.clients"), value: (credential.clients ?? []).map((client) => t("callerPolicy.activity.clientSays", { client: client.label })).join(", "), mono: false }]
              : []),
            { label: t("callerPolicy.activity.facts.revoked"), value: credential.revoked ? t("common.words.yes") : t("common.words.no"), mono: false },
          ]}
        />
        {kind === "assistant" ? <InlineMessage tone="info">{t("callerPolicy.activity.assistantNote")}</InlineMessage> : null}
      </SurfaceSection>
      <SurfaceSection title={t("callerPolicy.activity.interventionsTitle")} description={t("callerPolicy.activity.interventionsDescription")} icon={OBJECT_ICONS.activity} flush>
        <DataTable
          embedded
          caption={t("callerPolicy.activity.interventionsCaption")}
          columns={columns}
          rows={data.interventions}
          getRowId={(item) => String(item.n)}
          defaultSort={{ column: "when", direction: "descending" }}
          empty={<EmptyState variant="inline" title={t("callerPolicy.activity.empty", { days: data.days })} description={t("callerPolicy.activity.noAudit")} />}
        />
        {data.complete ? null : (
          <div className="px-4 pb-3">
            <InlineMessage tone="warning">{t("callerPolicy.activity.incomplete")}</InlineMessage>
          </div>
        )}
      </SurfaceSection>
    </Sections>
  );
}

/**
 * What a credential did (item 185): a token, the built-in assistant for one person or the central's fleet token, as interventions (runs
 * of calls with no pause of half an hour), each one a page. It reads the audit trail and nothing else, so with the audit trail off
 * there is nothing to show and the page says so. What a client says it is, is said as its own word.
 */
export function CredentialActivityPage({ id }: { id: number }) {
  const t = useT();
  const query = useQuery(activityQuery(id, DAYS));
  return (
    <QueryState query={query} label={t("callerPolicy.activity.loadingLabel")} skeleton={<SkeletonCard lines={4} />}>
      {(data) => <Credential data={data} />}
    </QueryState>
  );
}

