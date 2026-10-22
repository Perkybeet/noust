import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { certsQuery } from "../../api/queries/certs";
import { deploymentQuery } from "../../api/queries/deployments";
import { appDomainsQuery } from "../../api/queries/domains";
import { Card } from "../../components/ui/Card";
import { CopyButton } from "../../components/ui/CopyButton";
import { Disclosure } from "../../components/ui/Disclosure";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { displayAddress, isTemporaryName } from "./displayAddress";

export interface ArrivalRowProps {
  /** The application's own name. */
  domain: string;
  /** The deployment whose outcome this says: where the certificate's result is written. */
  deploymentId?: number | null;
  /**
   * What happened, as a sentence ("shop.example.com is deployed"): announced, and the row's first
   * line. Without it the pill and the address share the first line.
   */
  title?: string;
  /** What leads to the build: its log, the job's output. After the title, on the same line where it fits. */
  actions?: ReactNode;
  /** The three things worth doing next: said on the first deploy of an application. */
  next?: boolean;
}

/**
 * The row a deploy that serves ends on: it is live, at which address, and whether HTTPS covers it;
 * on the first deploy of an application, the three things worth doing next. The wizard ends on it
 * and so does a deployment's page, which is why it is one. It states a fact, so it is a row and not
 * a banner, and the green is the state of the application, not a decoration. A certificate the
 * authority refused does not fail the deploy (a temporary name on a shared domain is refused more
 * often than not): the application serves over HTTP, and the authority's own words are one press away.
 */
export function ArrivalRow({ domain, deploymentId = null, title, actions, next = false }: ArrivalRowProps) {
  const t = useT();
  const names = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  const deployment = useQuery({ ...deploymentQuery(deploymentId ?? 0), enabled: deploymentId !== null });
  const refused = deployment.data?.certificate_state === "failed";
  const issued = deployment.data?.certificate_state === "issued";
  const address = displayAddress(domain, names.data?.domains, certs.data);
  const secure = !refused && (issued || address.secure);
  const url = `${secure ? "https" : "http"}://${address.name}`;
  const output = deployment.data?.certificate_output?.trim() ?? "";
  const live = <StatusPill state="running" label={t("apps.arrival.live")} />;
  const site = (
    <>
      <ExternalLink href={url} label={t("apps.arrival.liveSite", { domain: address.name })}>
        <Mono>{url}</Mono>
      </ExternalLink>
      <CopyButton value={url} label={t("apps.arrival.copy")} />
    </>
  );
  return (
    <Card padding="sm">
      <div className="flex min-w-0 flex-col gap-2">
        {title !== undefined ? (
          <>
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <p role="status" className="flex min-w-0 items-center gap-2 text-14 font-medium text-fg">
                {live}
                <span className="min-w-0 break-words">{title}</span>
              </p>
              {actions}
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">{site}</div>
          </>
        ) : (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
            {live}
            {site}
            {actions}
          </div>
        )}
        <p className="text-13 text-pretty text-fg-muted">
          {secure ? (
            t("apps.arrival.httpsReady")
          ) : (
            <>
              {t(refused ? "apps.arrival.httpOnly" : "apps.arrival.httpOnlyNone")}{" "}
              {isTemporaryName(address.name) ? t("apps.arrival.temporaryHelp") : null}{" "}
              <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
                {t("apps.arrival.openDomains")}
              </TextLink>
            </>
          )}
        </p>
        {refused && output !== "" ? (
          <Disclosure label={t("apps.arrival.certbotSaid")}>
            <SystemOutput label={t("apps.arrival.certbotOutput", { domain: address.name })}>{output}</SystemOutput>
          </Disclosure>
        ) : null}
        {next ? (
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-13 text-fg-muted">
            <span>{t("common.words.next")}</span>
            <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
              {t("apps.arrival.nextDomain")}
            </TextLink>
            <TextLink to="/apps/$domain/database" params={{ domain }} size="ui">
              {t("apps.arrival.nextDatabase")}
            </TextLink>
            <TextLink to="/backups" size="ui">
              {t("apps.arrival.nextBackup")}
            </TextLink>
          </p>
        ) : null}
      </div>
    </Card>
  );
}
