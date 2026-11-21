import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { deploymentQuery } from "../../api/queries/deployments";
import { Card } from "../../components/ui/Card";
import { CopyButton } from "../../components/ui/CopyButton";
import { Disclosure } from "../../components/ui/Disclosure";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { isTemporaryName } from "./displayAddress";
import { useAppAnswer } from "./useAppAnswer";

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
 *
 * Where it answers is the one reading the application's own page makes (`useAppAnswer`): its names and a certificate on a
 * server with Noust's web server, a port of the machine on a central that deploys on its own Docker. On the second there is no
 * certificate to cover and no domain to add, so the row asks and says neither.
 */
export function ArrivalRow({ domain, deploymentId = null, title, actions, next = false }: ArrivalRowProps) {
  const t = useT();
  const { answer, offersNames } = useAppAnswer(domain);
  const deployment = useQuery({ ...deploymentQuery(deploymentId ?? 0), enabled: deploymentId !== null });
  const refused = deployment.data?.certificate_state === "failed";
  const issued = deployment.data?.certificate_state === "issued";
  const named = answer.kind === "names" ? answer.address : null;
  const secure = named !== null && !refused && (issued || named.secure);
  const url = answer.kind === "machine" ? answer.url : named !== null ? `${secure ? "https" : "http"}://${named.name}` : null;
  const output = deployment.data?.certificate_output?.trim() ?? "";
  const live = <StatusPill state="running" label={t("apps.arrival.live")} />;
  const site =
    url !== null ? (
      <>
        {/* The link draws the whole address, scheme included, so its name starts with the whole address (WCAG 2.5.3): the text takes the address as its `{domain}`, which the community languages' copies of it expect. */}
        <ExternalLink href={url} label={t("apps.arrival.liveSite", { domain: url })}>
          <Mono>{url}</Mono>
        </ExternalLink>
        <CopyButton value={url} label={t("apps.arrival.copy")} />
      </>
    ) : answer.kind === "loading" ? (
      // An address that may be another is not drawn while the server has not said: a name that does not exist was what this showed.
      <Skeleton className="h-5 w-56" />
    ) : (
      <span className="text-13 text-fg-muted">{t("containerHost.publish.noWeb")}</span>
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
        {answer.kind === "machine" ? (
          // The sentence the application's own page says of the same address.
          <p className="text-13 text-pretty text-fg-muted">{t.rich("containerHost.publish.reach", { address: <Mono key="address">{answer.url}</Mono> })}</p>
        ) : named !== null ? (
          <p className="text-13 text-pretty text-fg-muted">
            {secure ? (
              t("apps.arrival.httpsReady")
            ) : (
              <>
                {t(refused ? "apps.arrival.httpOnly" : "apps.arrival.httpOnlyNone")}{" "}
                {isTemporaryName(named.name) ? t("apps.arrival.temporaryHelp") : null}{" "}
                {offersNames ? (
                  <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
                    {t("apps.arrival.openDomains")}
                  </TextLink>
                ) : null}
              </>
            )}
          </p>
        ) : null}
        {refused && output !== "" && named !== null ? (
          <Disclosure label={t("apps.arrival.certbotSaid")}>
            <SystemOutput label={t("apps.arrival.certbotOutput", { domain: named.name })}>{output}</SystemOutput>
          </Disclosure>
        ) : null}
        {next ? (
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-13 text-fg-muted">
            <span>{t("common.words.next")}</span>
            {offersNames ? (
              <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
                {t("apps.arrival.nextDomain")}
              </TextLink>
            ) : null}
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
