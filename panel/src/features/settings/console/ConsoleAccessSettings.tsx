import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { consoleExposureQuery, exposureKeys } from "../../../api/queries/consoleExposure";
import type { ConsoleExposure } from "../../../api/queries/consoleExposure";
import { jobKeys } from "../../../api/queries/jobs";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section, Sections } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { useJobInHand } from "../../jobs/useJobInHand";
import { accountsQuery } from "../accounts/api";
import { SettingsPrimaryAction } from "../SettingsShell";
import { accountsWithoutSecondFactor } from "./data";
import { PublishConsoleDialog } from "./PublishConsoleDialog";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

/** What publishing made, in the words of what unpublishing will do with it. */
function madeWords(t: T, made: boolean): string {
  return made ? t("consoleAccess.facts.made") : t("consoleAccess.facts.existing");
}

function Prerequisites({ exposure, lacking }: { exposure: ConsoleExposure; lacking: readonly string[] | null }) {
  const t = useT();
  return (
    <Section title={t("consoleAccess.prerequisites.title")} description={t("consoleAccess.prerequisites.description")}>
      <Card padding="sm">
        <ul className="flex flex-col divide-y divide-border">
          <li className="flex min-w-0 flex-col gap-2 py-3 first:pt-0">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <p className="text-13 font-medium text-fg">{t("consoleAccess.prerequisites.twoFactor")}</p>
              {lacking === null ? null : lacking.length === 0 ? (
                <StatusPill appearance="inline" size="sm" state="running" label={t("consoleAccess.prerequisites.twoFactorOk")} />
              ) : (
                <StatusPill appearance="inline" size="sm" state="failed" label={t("consoleAccess.prerequisites.twoFactorLacking", { count: lacking.length })} />
              )}
            </div>
            {lacking === null ? (
              <p className="max-w-measure text-13 text-fg-muted">{t("consoleAccess.prerequisites.twoFactorUnknown")}</p>
            ) : lacking.length > 0 ? (
              <>
                <ul className="flex flex-wrap gap-x-3 gap-y-1">
                  {lacking.map((username) => (
                    <li key={username}>
                      <Mono tone="default">{username}</Mono>
                    </li>
                  ))}
                </ul>
                <p className="max-w-measure text-13 text-fg-muted">{t("consoleAccess.prerequisites.twoFactorFix")}</p>
                <TextLink to="/settings/accounts" size="ui">
                  {t("consoleAccess.prerequisites.accountsLink")}
                </TextLink>
              </>
            ) : null}
          </li>
          <li className="flex min-w-0 flex-col gap-1 py-3">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <p className="text-13 font-medium text-fg">{t("consoleAccess.prerequisites.ens")}</p>
              <StatusPill appearance="inline" size="sm" state={exposure.ensAllows ? "running" : "warning"} label={exposure.ensAllows ? t("consoleAccess.prerequisites.ensOk") : t("consoleAccess.prerequisites.ensBlocked")} />
            </div>
          </li>
          <li className="flex min-w-0 flex-col gap-1 py-3 last:pb-0">
            <p className="text-13 font-medium text-fg">{t("consoleAccess.prerequisites.name")}</p>
            <p className="max-w-measure text-13 text-fg-muted">{t("consoleAccess.prerequisites.nameBody")}</p>
          </li>
        </ul>
      </Card>
    </Section>
  );
}

/**
 * Settings > Console access: whether the console is published on a name of its own, and the way
 * to publish it (an nginx site, a certificate, an allow-list) or take it down. The state is the
 * first thing on the page; what it checks before it publishes is next, so a refusal is no
 * surprise: the accounts that lack a second factor are named here, from the accounts list.
 */
export function ConsoleAccessSettings() {
  const t = useT();
  useDocumentTitle(t("consoleAccess.documentTitle"), 1);
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(consoleExposureQuery());
  // Reading the accounts needs a permission the one who publishes may lack: then the server's own
  // refusal, on preview, says which accounts stop it.
  const accounts = useQuery({ ...accountsQuery(), retry: false });
  const [publishing, setPublishing] = useState(false);
  const [unpublishing, setUnpublishing] = useState(false);
  const job = useJobInHand(() => void queryClient.invalidateQueries({ queryKey: exposureKeys.all }));
  const exposure = query.data;
  const lacking = accounts.data === undefined ? null : accountsWithoutSecondFactor(accounts.data.accounts);
  const stuck = exposure !== undefined && !exposure.exposed && exposure.domain !== null;
  const problem = exposure !== undefined && exposure.exposed && exposure.problems.length > 0;

  return (
    <Sections>
      {exposure !== undefined && !exposure.exposed && !stuck ? (
        <SettingsPrimaryAction>
          <Button variant="primary" onClick={() => setPublishing(true)}>
            {t("consoleAccess.actions.publish")}
          </Button>
        </SettingsPrimaryAction>
      ) : null}
      {job.notice}
      {query.isError && exposure === undefined ? (
        <ErrorBlock error={query.error} title={t("consoleAccess.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : exposure === undefined ? (
        <LoadingRegion label={t("consoleAccess.loading")} className="flex flex-col gap-8">
          <Skeleton className="h-24 w-full rounded-card" />
          <Skeleton className="h-48 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <>
          <Card
            level={2}
            title={t("consoleAccess.state.title")} icon={OBJECT_ICONS.domain}
            status={
              <StatusPill
                state={exposure.exposed ? (problem ? "warning" : "running") : stuck ? "warning" : "stopped"}
                label={exposure.exposed ? (problem ? t("consoleAccess.state.problem") : t("consoleAccess.state.on")) : stuck ? t("consoleAccess.state.unfinished") : t("consoleAccess.state.off")}
                size="sm"
              />
            }
            description={
              exposure.exposed
                ? problem
                  ? t("consoleAccess.state.problemBody", { url: exposure.url ?? exposure.domain ?? "" })
                  : t("consoleAccess.state.onBody", { url: exposure.url ?? exposure.domain ?? "" })
                : stuck
                  ? t("consoleAccess.state.unfinishedBody", { domain: exposure.domain ?? "" })
                  : t("consoleAccess.state.offBody")
            }
            actions={
              exposure.exposed || stuck ? (
                <Button size="sm" disabled={job.busy} onClick={() => setUnpublishing(true)}>
                  {t("consoleAccess.actions.unpublish")}
                </Button>
              ) : undefined
            }
          >
            {exposure.exposed || stuck ? (
              <KeyValueList
                items={[
                  { label: t("consoleAccess.facts.address"), value: exposure.url !== null ? <ExternalLink href={exposure.url} /> : (exposure.domain ?? ""), mono: false, copy: exposure.url ?? false },
                  {
                    label: t("consoleAccess.facts.allowList"),
                    value: exposure.allowIps.length === 0 ? t("consoleAccess.facts.anyone") : exposure.allowIps.join(", "),
                    mono: exposure.allowIps.length > 0,
                    copy: false,
                  },
                  { label: t("consoleAccess.facts.site"), value: madeWords(t, exposure.siteCreated), mono: false, copy: false },
                  { label: t("consoleAccess.facts.certificate"), value: madeWords(t, exposure.certificateCreated), mono: false, copy: false },
                  {
                    label: t("consoleAccess.facts.proxy"),
                    value: exposure.proxyAdded !== null ? t("consoleAccess.facts.proxyMade", { address: exposure.proxyAdded }) : t("consoleAccess.facts.proxyExisting"),
                    mono: false,
                    copy: false,
                  },
                  ...(exposure.createdAt !== null ? [{ label: t("consoleAccess.facts.since"), value: <RelativeTime value={exposure.createdAt} />, mono: false, copy: false as const }] : []),
                  ...(exposure.createdBy !== null ? [{ label: t("consoleAccess.facts.by"), value: exposure.createdBy }] : []),
                ]}
              />
            ) : undefined}
          </Card>

          {exposure.problems.length > 0 ? (
            <Notice tone="warning">
              <ul className="flex flex-col gap-1" translate="no">
                {exposure.problems.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Notice>
          ) : null}

          {exposure.exposed || stuck ? null : <Prerequisites exposure={exposure} lacking={lacking} />}

          <CommandHint command="noust console expose --domain console.example.com --allow-ip 203.0.113.0/24" label={t("common.words.fromTerminal")} />
        </>
      )}

      {exposure !== undefined && publishing ? <PublishConsoleDialog open onOpenChange={(next) => setPublishing(next)} ensAllows={exposure.ensAllows} /> : null}
      <ConfirmDialog
        friction="simple"
        open={unpublishing}
        onOpenChange={setUnpublishing}
        title={t("consoleAccess.unpublish.title")}
        description={t("consoleAccess.unpublish.description")}
        actionLabel={t("consoleAccess.unpublish.action")}
        server={node}
        onConfirm={async () => {
          const accepted = await request("delete", "/api/console/exposure");
          job.track(accepted, {
            running: t("consoleAccess.unpublish.running"),
            succeeded: t("consoleAccess.unpublish.succeeded"),
            failed: t("consoleAccess.unpublish.failed"),
          });
          void queryClient.invalidateQueries({ queryKey: jobKeys.active });
        }}
      />
    </Sections>
  );
}
