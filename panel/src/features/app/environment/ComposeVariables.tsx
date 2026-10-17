import { useQuery } from "@tanstack/react-query";

import { composeVariablesQuery } from "../../../api/queries/runtime";
import type { ComposeVariable } from "../../../api/queries/runtime";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
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
    <Section title={t("environment.compose.title")} description={t("environment.compose.description")}>
      {report.isError ? (
        <ErrorBlock compact error={report.error} title={t("environment.compose.readFailed")} onRetry={() => void report.refetch()} retrying={report.isRefetching} />
      ) : report.data === undefined ? (
        <div aria-busy="true">
          <span className="sr-only">{t("environment.compose.loading")}</span>
          <Skeleton className="h-16 w-full" />
        </div>
      ) : variables.length === 0 ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("environment.compose.none", { file: report.data.file })}</p>
      ) : (
        <>
          <ul aria-label={t("environment.compose.listLabel", { file: report.data.file })} className="flex flex-col divide-y divide-border rounded-card border border-border">
            {variables.map((variable) => {
              const places = variable.used_in ?? [];
              return (
              <li key={variable.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
                <Mono>{variable.name}</Mono>
                <Badge tone={variable.status === "missing" ? "fail" : variable.status === "set" ? "ok" : "neutral"}>{stateWords(t, variable)}</Badge>
                {variable.required && variable.message !== "" ? <span className="min-w-0 text-12 text-fg-muted">{variable.message}</span> : null}
                <span className="ml-auto flex items-center gap-3">
                  {places.length > 0 ? <span className="text-12 text-fg-muted">{t("environment.compose.usedBy", { places: places.join(", ") })}</span> : null}
                  {variable.status === "set" ? null : (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t("environment.compose.addAria", { name: variable.name })}
                      onClick={() => {
                        onAdd(variable.name);
                      }}
                    >
                      {t("environment.compose.add")}
                    </Button>
                  )}
                </span>
              </li>
              );
            })}
          </ul>
          <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("environment.compose.hint")}</p>
        </>
      )}
    </Section>
  );
}
