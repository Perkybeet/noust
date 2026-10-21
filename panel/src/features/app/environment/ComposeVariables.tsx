import { Variable } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { composeVariablesQuery } from "../../../api/queries/runtime";
import type { ComposeVariable } from "../../../api/queries/runtime";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { RowList } from "../../../components/ui/RowList";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";

function stateWords(t: T, variable: ComposeVariable): string {
  if (variable.status === "set") return t("environment.compose.stateSet");
  if (variable.status === "default") return t("environment.compose.stateDefault", { value: variable.default ?? "" });
  if (variable.status === "missing") return t("environment.compose.stateMissing");
  return t("environment.compose.stateUnset");
}

/**
 * The variables the stack's compose file asks for, and which of them it has: what Compose would
 * otherwise tell the operator minutes into a build. One the file requires with no value stops the
 * next deployment, so it comes first; adding it opens the same dialog as any variable. Values are
 * never shown, only whether there is one.
 */
export function ComposeVariables({ domain, onAdd }: { domain: string; onAdd: (name: string) => void }) {
  const t = useT();
  const report = useQuery(composeVariablesQuery(domain));
  const variables = [...(report.data?.variables ?? [])].sort((a, b) => Number(b.status === "missing") - Number(a.status === "missing"));
  return (
    <Section title={t("environment.compose.title")} icon={<Variable />} description={t("environment.compose.description")}>
      {report.isError ? (
        <ErrorBlock compact error={report.error} title={t("environment.compose.readFailed")} onRetry={() => void report.refetch()} retrying={report.isRefetching} />
      ) : report.data === undefined ? (
        <LoadingRegion label={t("environment.compose.loading")}>
          <Skeleton className="h-16 w-full" />
        </LoadingRegion>
      ) : variables.length === 0 ? (
        <EmptyState variant="inline" title={t("environment.compose.none", { file: report.data.file })} />
      ) : (
        <>
          <RowList aria-label={t("environment.compose.listLabel", { file: report.data.file })}>
            {variables.map((variable) => {
              const places = variable.used_in ?? [];
              return (
                <RowList.Item
                  key={variable.name}
                  actions={
                    <>
                      {places.length > 0 ? <span className="text-12 text-fg-muted">{t("environment.compose.usedBy", { places: places.join(", ") })}</span> : null}
                      {variable.status === "set" ? null : (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<ICONS.add aria-hidden="true" />}
                          aria-label={t("environment.compose.addAria", { name: variable.name })}
                          onClick={() => {
                            onAdd(variable.name);
                          }}
                        >
                          {t("environment.compose.add")}
                        </Button>
                      )}
                    </>
                  }
                >
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                    <Mono>{variable.name}</Mono>
                    {/* A variable the file requires and has no value for stops the next deployment: that is a state. That one is set is a fact about it. */}
                    {variable.status === "missing" ? (
                      <StatusPill state="failed" label={stateWords(t, variable)} appearance="inline" size="sm" />
                    ) : (
                      <Badge>{stateWords(t, variable)}</Badge>
                    )}
                    {variable.required && variable.message !== "" ? <span className="min-w-0 text-12 text-fg-muted">{variable.message}</span> : null}
                  </div>
                </RowList.Item>
              );
            })}
          </RowList>
          <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("environment.compose.hint")}</p>
        </>
      )}
    </Section>
  );
}
