import { useQuery } from "@tanstack/react-query";

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
import { displayAddress, isTemporaryName } from "../app/displayAddress";

export interface SuccessRowProps {
  /** The application's own name. */
  domain: string;
  /** The deployment the job recorded: where the certificate's outcome is written. */
  deploymentId: number | null;
}

/**
 * The row a first deploy ends on: it is live, at which address, and whether HTTPS covers it; then
 * the three things worth doing next. It states a fact, so it is a row and not a banner, and the
 * green is the state of the application, not a decoration. A certificate the authority refused
 * does not fail the deploy (a temporary name on a shared domain is refused more often than not):
 * the application serves over HTTP, and the authority's own words are one press away.
 */
export function SuccessRow({ domain, deploymentId }: SuccessRowProps) {
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
  return (
    <Card padding="sm">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <StatusPill state="running" label={t("newApp.success.live")} />
          <ExternalLink href={url} label={t("newApp.success.liveSite", { domain: address.name })}>
            <Mono>{url}</Mono>
          </ExternalLink>
          <CopyButton value={url} label={t("newApp.success.copy")} />
        </div>
        <p className="text-13 text-pretty text-fg-muted">
          {secure ? (
            t("newApp.success.httpsReady")
          ) : (
            <>
              {t(refused ? "newApp.success.httpOnly" : "newApp.success.httpOnlyNone")}{" "}
              {isTemporaryName(address.name) ? t("newApp.address.temporaryHelp") : null}{" "}
              <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
                {t("newApp.success.openDomains")}
              </TextLink>
            </>
          )}
        </p>
        {refused && output !== "" ? (
          <Disclosure label={t("newApp.success.certbotSaid")}>
            <SystemOutput label={t("newApp.success.certbotOutput", { domain: address.name })}>{output}</SystemOutput>
          </Disclosure>
        ) : null}
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-13 text-fg-muted">
          <span>{t("newApp.success.next")}</span>
          <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
            {t("newApp.success.nextDomain")}
          </TextLink>
          <TextLink to="/apps/$domain/database" params={{ domain }} size="ui">
            {t("newApp.success.nextDatabase")}
          </TextLink>
          <TextLink to="/backups" size="ui">
            {t("newApp.success.nextBackup")}
          </TextLink>
        </p>
      </div>
    </Card>
  );
}
