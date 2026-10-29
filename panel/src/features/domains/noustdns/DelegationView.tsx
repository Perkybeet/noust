import { useQuery } from "@tanstack/react-query";

import { noustCheckQuery, noustDelegationQuery } from "../../../api/queries/noustDns";
import type { NoustDelegationCheck, NoustSentence } from "../../../api/queries/noustDns";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Subsection } from "../../../components/page/Subsection";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import type { Column } from "../../../components/ui/DataTable";
import { DataTable } from "../../../components/ui/DataTable";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { ServerText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";

/** A sentence of the server's, its fix under it: said in the console's language when it has the code. */
function Sentence({ sentence }: { sentence: NoustSentence }) {
  const coded = { code: sentence.code, params: sentence.params ?? {} };
  return (
    <span className="flex min-w-0 flex-col gap-1">
      <span className="text-14 text-pretty">
        <ServerText coded={coded} text={sentence.text} />
      </span>
      {sentence.hint ? (
        <span className="text-13 text-pretty text-fg-muted">
          <ServerText coded={coded} text={sentence.hint} part="hint" />
        </span>
      ) : null}
    </span>
  );
}

function YesNo({ value }: { value: boolean | null | undefined }) {
  const t = useT();
  if (value === null || value === undefined) return <span className="text-fg-muted">{t("noustDns.delegation.unknown")}</span>;
  return <StatusPill state={value ? "running" : "failed"} label={value ? t("common.words.yes") : t("common.words.no")} appearance="inline" size="sm" />;
}

/** What the world sees: each name server asked directly and the public resolvers, and what is still missing. */
function CheckResult({ check }: { check: NoustDelegationCheck }) {
  const t = useT();
  const columns: Column<NoustDelegationCheck["servers"][number]>[] = [
    { id: "name", header: t("noustDns.delegation.serverColumn"), cell: (server) => <Mono>{server.name}</Mono> },
    { id: "answers", header: t("noustDns.delegation.answersColumn"), card: "status", width: "w-28", cell: (server) => <YesNo value={server.answers} /> },
    { id: "authority", header: t("noustDns.delegation.authorityColumn"), hideBelow: "sm", cell: (server) => <YesNo value={server.authoritative} /> },
    { id: "serial", header: t("noustDns.delegation.serialColumn"), align: "end", hideBelow: "md", cell: (server) => <Mono className="tabular-nums">{server.serial ?? ""}</Mono> },
    { id: "recursion", header: t("noustDns.delegation.recursionColumn"), hideBelow: "md", cell: (server) => <YesNo value={server.refuses_recursion} /> },
  ];
  const facts: KeyValueItem[] = [
    { label: t("noustDns.delegation.publicNameservers"), value: check.public_nameservers.join(", ") },
    { label: t("noustDns.delegation.dsPublished"), value: <YesNo value={check.ds_published} />, mono: false, copy: false },
  ];
  if (check.ds_published === true) facts.push({ label: t("noustDns.delegation.dsMatches"), value: <YesNo value={check.ds_matches} />, mono: false, copy: false });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusPill state={check.ready ? "running" : "warning"} label={check.ready ? t("noustDns.delegation.ready") : t("noustDns.delegation.notReady")} />
        <span className="text-14 text-fg-muted">{check.delegated === true ? t("noustDns.delegation.delegated") : check.delegated === false ? t("noustDns.delegation.notDelegated") : t("noustDns.delegation.unknown")}</span>
      </div>
      <DataTable caption={t("noustDns.delegation.checkTitle")} columns={columns} rows={check.servers} getRowId={(server) => `${server.name}/${server.address}`} />
      <KeyValueList items={facts} />
      {check.problems.length > 0 ? (
        <Subsection title={t("noustDns.delegation.problemsTitle")}>
          <ul className="flex flex-col gap-3">
            {check.problems.map((problem) => (
              <li key={`${problem.code}-${JSON.stringify(problem.params ?? {})}`} className="flex items-start gap-2">
                <ICONS.error aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fail" />
                <Sentence sentence={problem} />
              </li>
            ))}
          </ul>
        </Subsection>
      ) : null}
    </div>
  );
}

/**
 * Delegating the domain to these servers: what only the registrar can do (the name servers, their
 * glue, the DS), in the order that works, and the check that asks the world whether it was done. The
 * steps and the problems are the server's own sentences, said in the console's language, each with
 * its fix. Nothing here changes anything: it says what is true now.
 */
export function DelegationView({ zone }: { zone: string }) {
  const t = useT();
  const plan = useQuery(noustDelegationQuery(zone));
  const check = useQuery({ ...noustCheckQuery(zone), enabled: false });

  return (
    <QueryState
      query={plan}
      label={t("noustDns.delegation.stepsTitle")}
      skeleton={
        <LoadingRegion label={t("common.queryState.loading", { label: t("noustDns.delegation.stepsTitle") })} className="flex flex-col gap-3">
          <Skeleton className="h-32" />
          <Skeleton className="h-24" />
        </LoadingRegion>
      }
    >
      {(delegation) => (
        <div className="flex flex-col gap-4">
          <Card level={2} title={t("noustDns.delegation.stepsTitle")}>
            <ol className="flex flex-col gap-4">
              {delegation.steps.map((step, index) => (
                <li key={`${step.code}-${index}`} className="flex items-start gap-3">
                  <span aria-hidden="true" className="flex size-6 shrink-0 items-center justify-center rounded-pill bg-bg-sunken text-12 font-medium text-fg-muted tabular-nums">
                    {index + 1}
                  </span>
                  <Sentence sentence={step} />
                </li>
              ))}
            </ol>
            {delegation.warnings.length > 0 ? (
              <ul className="mt-5 flex flex-col gap-3 border-t border-border pt-4">
                {delegation.warnings.map((warning) => (
                  <li key={warning.code} className="flex items-start gap-2">
                    <ICONS.warning aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-warn" />
                    <Sentence sentence={warning} />
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
          <Card level={2} title={t("noustDns.delegation.nameserversTitle")} padding="sm">
            <KeyValueList
              items={delegation.nameservers.map((server) => ({
                label: server.name,
                value: server.addresses.length > 0 ? server.addresses.join(", ") : t("noustDns.delegation.noAddress"),
                mono: server.addresses.length > 0,
                copy: server.addresses.length > 0 ? server.addresses.join(" ") : false,
                ...(server.glue ? { hint: t("noustDns.delegation.needsGlue") } : {}),
              }))}
            />
          </Card>
          <Card
            level={2}
            title={t("noustDns.delegation.checkTitle")}
            actions={
              <Button size="sm" variant="primary" icon={<ICONS.search aria-hidden="true" />} loading={check.isFetching} onClick={() => void check.refetch()}>
                {t("noustDns.delegation.check")}
              </Button>
            }
          >
            {check.isFetching ? (
              <LoadingRegion label={t("noustDns.delegation.checking")} className="flex flex-col gap-3">
                <Skeleton className="h-20" />
              </LoadingRegion>
            ) : check.isError ? (
              <ErrorBlock compact error={check.error} title={t("noustDns.delegation.checkFailed")} />
            ) : check.data !== undefined ? (
              <CheckResult check={check.data} />
            ) : null}
          </Card>
          <CommandHint command={`noust dns zone check ${zone}`} label={t("common.words.fromTerminal")} />
        </div>
      )}
    </QueryState>
  );
}
