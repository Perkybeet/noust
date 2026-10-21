import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Webhook } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Stepper } from "../../../components/page/Stepper";
import type { WizardStep } from "../../../components/page/Stepper";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { useBranchPin } from "./BranchPin";
import { GitPushCard } from "./GitPush";
import { useSudoFirst } from "./formParts";
import { RECEIVED_SHOWN, settingsKeys, webhookReceivedQuery, webhookStatusQuery } from "./queries";
import type { ReceivedDelivery, WebhookSecret, WebhookStatus } from "./queries";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { connectionView, forgeName, outcomeView, senderName } from "./webhook";
import { SETTINGS_ICONS } from "./objectIcons";

/** The example name `noust web expose-hooks` is shown with: the operator types their own. */
const HOOKS_EXAMPLE = "hooks.example.com";

/**
 * The step on screen: what it asks, said by the sentence under its title, which starts with "Done"
 * or "Not done". The numbers and the way between steps are the kit's `Stepper`.
 */
function SetupStep({ title, state, action, children }: { title: string; state: ReactNode; action?: ReactNode; children?: ReactNode }) {
  return (
    <Subsection level={4} title={title} description={state} actions={action}>
      {children}
    </Subsection>
  );
}

/** Where the connection stands, in one line, with what can be done to it. */
function Connection({ status, followTags, onRotate, onDisable }: { status: WebhookStatus; followTags: string | null; onRotate: () => void; onDisable: () => void }) {
  const t = useT();
  const view = connectionView(status.state, t.locale);
  const branch = status.branch.tracked;
  // An app that follows tags deploys a published release or a pushed tag, never a push to a branch.
  const tags = followTags !== null && followTags !== "" ? <Mono>{followTags}</Mono> : null;
  const forge = forgeName(status.forge.forge, t.locale);
  const lastPush = status.deliveries.last_push_at;
  const lastSeen = status.deliveries.last_verified_at;
  let detail: ReactNode;
  if (status.state === "disabled") {
    detail = t("appSettings.webhook.detailOff");
  } else if (status.state === "problem") {
    detail = t("appSettings.webhook.detailProblem", { count: status.deliveries.refused_since_last_verified, forge });
  } else if (status.state === "waiting") {
    detail =
      tags !== null
        ? t.rich("appSettings.webhook.detailWaitingTags", { pattern: tags, forge })
        : branch
          ? t.rich("appSettings.webhook.detailWaitingBranch", { branch: <Mono>{branch}</Mono>, forge })
          : t("appSettings.webhook.detailWaitingAny", { forge });
  } else {
    detail = (
      <>
        {tags !== null
          ? t.rich("appSettings.webhook.detailConnectedTags", { pattern: tags })
          : branch
            ? t.rich("appSettings.webhook.detailConnectedBranch", { branch: <Mono>{branch}</Mono> })
            : t("appSettings.webhook.detailConnectedAny")}{" "}
        {lastPush
          ? t.rich("appSettings.webhook.lastPush", { time: <RelativeTime value={lastPush} /> })
          : lastSeen
            ? t.rich("appSettings.webhook.lastDelivery", { time: <RelativeTime value={lastSeen} /> })
            : null}
      </>
    );
  }
  return (
    <Card padding="sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <Webhook aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
          <div className="flex min-w-0 flex-col items-start gap-1">
            <StatusPill state={view.state} label={view.label} size="sm" />
            <p className="text-13 text-pretty text-fg-muted">{detail}</p>
          </div>
        </div>
        {status.enabled ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button icon={<SETTINGS_ICONS.rebuild aria-hidden="true" />} size="sm" onClick={onRotate}>
              {t("appSettings.webhook.rotate")}
            </Button>
            <Button icon={<SETTINGS_ICONS.turnOff aria-hidden="true" />} size="sm" variant="ghost" onClick={onDisable}>
              {t("appSettings.webhook.turnOff")}
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** Where each forge keeps its webhook form, and which event to tick. */
function forgeWords(t: T, forge: string | null | undefined, events: readonly string[]): { how: string; events: string } {
  if (forge === "github") return { how: t("appSettings.webhook.howGithub"), events: t("appSettings.webhook.eventsGithub") };
  if (forge === "bitbucket") {
    // Bitbucket names its events by key, and the pull request ones are asked for only when the
    // app builds previews: the server says which, so this never lists what it will not use.
    const previews = events.some((event) => event.startsWith("pullrequest:"));
    return {
      how: t("appSettings.webhook.howBitbucket"),
      events: previews ? t("appSettings.webhook.eventsBitbucketPreviews") : t("appSettings.webhook.eventsBitbucket"),
    };
  }
  if (forge === "gitlab") return { how: t("appSettings.webhook.howGitlab"), events: t("appSettings.webhook.eventsGitlab") };
  if (forge === "gitea") return { how: t("appSettings.webhook.howGitea"), events: t("appSettings.webhook.eventsGithub") };
  return { how: t("appSettings.webhook.howOther"), events: t("appSettings.webhook.eventsOther") };
}

type StepId = "address" | "secret" | "forge";

/**
 * The three steps, each with its state: a public address the forge can reach, this app's secret,
 * and the forge's side with the exact values and a link to where they go. The kit's stepper shows
 * where the setup is and the step it is at is open; a step that is done can be opened again to read
 * its values. When this server's GitHub App already reaches the repository the first step is not
 * asked of anyone, and does not send them to a terminal.
 */
function Steps({
  status,
  secret,
  creating,
  revealing,
  onCreate,
  onReveal,
  onHide,
}: {
  status: WebhookStatus;
  secret: WebhookSecret | null;
  creating: boolean;
  revealing: boolean;
  onCreate: () => void;
  onReveal: () => void;
  onHide: () => void;
}) {
  const t = useT();
  const { hooks, forge } = status;
  const forgeLabel = forgeName(forge.forge, t.locale);
  const words = forgeWords(t, forge.forge, hooks.events);
  const hookUrl = secret?.hook_url ?? hooks.hook_url ?? null;
  const connected = status.deliveries.last_verified_at ?? null;
  const app = status.github_app;
  const byApp = app.covers_repository && !hooks.exposed;

  // The step the setup is at is the first that is not done; the operator can open one that is.
  const done: Record<StepId, boolean> = { address: hooks.exposed || byApp, secret: status.enabled, forge: connected !== null };
  const order: StepId[] = ["address", "secret", "forge"];
  // Covered by the GitHub App and with no webhook yet, the first step is the one that says why none is needed.
  const firstOpen: StepId = byApp && !status.enabled ? "address" : (order.find((id) => !done[id]) ?? "forge");
  // Past the address step without a public address: the console's own address is what gets pasted, which
  // is what a Git host on the same network needs, and the secret may already be there.
  const afterAddress: StepId = done.secret ? "forge" : "secret";
  const [chosen, setChosen] = useState<StepId | null>(null);
  // A secret on screen is pasted at the host, so that is the step it is shown in.
  const current: StepId = secret !== null ? "forge" : (chosen ?? firstOpen);
  // A step that is done can be read again from the rail, and this is the way on from it.
  const goOn = (to: StepId): ReactNode => (
    <Button icon={<SETTINGS_ICONS.next aria-hidden="true" />} size="sm" variant="ghost" onClick={() => setChosen(to)}>
      {t("common.wizard.next")}
    </Button>
  );
  const steps: WizardStep[] = [
    { id: "address", label: t("appSettings.webhook.rail.address") },
    { id: "secret", label: t("appSettings.webhook.rail.secret") },
    { id: "forge", label: t("appSettings.webhook.rail.forge", { forge: forgeLabel }) },
  ];

  const values = [
    { label: t("appSettings.webhook.urlLabel"), value: hookUrl },
    secret !== null
      ? { label: t("appSettings.webhook.secretLabel"), value: secret.secret }
      : {
          label: t("appSettings.webhook.secretLabel"),
          value: status.enabled ? (
            <span className="flex items-center gap-2">
              <span className="text-fg-muted">{t("appSettings.webhook.secretHidden")}</span>
              <Button icon={<SETTINGS_ICONS.show aria-hidden="true" />} size="sm" variant="ghost" loading={revealing} onClick={onReveal}>
                {t("appSettings.webhook.showSecret")}
              </Button>
            </span>
          ) : (
            <span className="text-fg-muted">{t("appSettings.webhook.secretNone")}</span>
          ),
          mono: false,
          copy: false as const,
        },
    { label: t("appSettings.webhook.contentTypeLabel"), value: hooks.content_type },
    ...(status.enabled && hooks.deploy_url ? [{ label: t("sourceOptions.deployUrl.label"), value: hooks.deploy_url }] : []),
    { label: t("appSettings.webhook.eventsLabel"), value: words.events, mono: false, copy: false as const },
  ];

  return (
    <Card title={t("appSettings.webhook.setupTitle")} icon={SETTINGS_ICONS.webhook}>
      <div className="flex flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={current} onSelect={(id) => setChosen(id as StepId)} />
        {current === "address" ? (
          <SetupStep
            title={t("appSettings.webhook.step1Title")}
            state={
              byApp
                ? t("appSettings.webhook.step1App", { repository: forge.repository ?? "", account: app.account ?? "" })
                : hooks.exposed && hooks.base_url
                  ? t.rich("appSettings.webhook.step1Done", { url: <Mono>{hooks.base_url}</Mono> })
                  : t("appSettings.webhook.step1ToDo", { forge: forgeLabel })
            }
            {...(done.address ? { action: goOn("secret") } : {})}
          >
            {byApp ? (
              <div className="flex flex-col items-start gap-2">
                <InlineMessage>{t("appSettings.webhook.appCoversRisk")}</InlineMessage>
                {status.enabled ? null : (
                  <Button size="sm" variant="ghost" onClick={() => setChosen(afterAddress)}>
                    {t("appSettings.webhook.appSetupAnyway")}
                  </Button>
                )}
              </div>
            ) : hooks.exposed ? null : (
              <div className="flex flex-col gap-1.5">
                <p className="text-12 text-fg-muted">{t("appSettings.webhook.step1Run")}</p>
                <CopyValue value={`noust web expose-hooks ${HOOKS_EXAMPLE}`} label={t("appSettings.webhook.copyCommand")} />
                <div>
                  <Button size="sm" variant="ghost" onClick={() => setChosen(afterAddress)}>
                    {t("appSettings.webhook.useOwnAddress")}
                  </Button>
                </div>
              </div>
            )}
          </SetupStep>
        ) : null}
        {current === "secret" ? (
          <SetupStep
            title={t("appSettings.webhook.step2Title")}
            state={status.enabled ? t("appSettings.webhook.step2Done") : t("appSettings.webhook.step2ToDo")}
            action={
              status.enabled ? (
                goOn("forge")
              ) : (
                <Button icon={<SETTINGS_ICONS.key aria-hidden="true" />} size="sm" loading={creating} onClick={onCreate}>
                  {t("appSettings.webhook.createSecret")}
                </Button>
              )
            }
          />
        ) : null}
        {current === "forge" ? (
          <SetupStep
            title={t("appSettings.webhook.step3Title", { forge: forgeLabel })}
            state={
              connected !== null
                ? t.rich("appSettings.webhook.step3Done", { forge: forgeLabel, time: <RelativeTime value={connected} /> })
                : words.how
            }
          >
            <div className="flex flex-col gap-2">
              <KeyValueList items={values} empty={t("appSettings.webhook.urlUnknown")} />
              {!hooks.hook_url_public && hookUrl !== null ? <p className="text-12 text-pretty text-fg-muted">{t("appSettings.webhook.urlNotPublic", { forge: forgeLabel })}</p> : null}
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                {forge.settings_url ? (
                  <ExternalLink href={forge.settings_url}>
                    {t("appSettings.webhook.openForgeSettings", { forge: forgeLabel })}
                  </ExternalLink>
                ) : (
                  <span />
                )}
                {secret !== null ? (
                  <Button icon={<SETTINGS_ICONS.hide aria-hidden="true" />} size="sm" variant="ghost" onClick={onHide}>
                    {t("appSettings.webhook.hideSecret")}
                  </Button>
                ) : null}
              </div>
            </div>
          </SetupStep>
        ) : null}
      </div>
    </Card>
  );
}

function receivedColumns(t: T): Column<ReceivedDelivery>[] {
  return [
    {
      id: "received",
      header: t("appSettings.webhook.receivedHeader"),
      cell: (row) => <RelativeTime value={row.received_at} className="text-fg" />,
      width: "w-32",
      card: "meta",
    },
    {
      id: "outcome",
      header: t("appSettings.webhook.outcomeHeader"),
      cell: (row) => {
        const view = outcomeView(row.outcome, t.locale);
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
      width: "w-56",
      card: "title",
    },
    {
      id: "sentBy",
      header: t("appSettings.webhook.sentByHeader"),
      cell: (row) => {
        const sender = senderName(row.provider, t.locale);
        return sender === null ? <EmptyCell reason={t("appSettings.webhook.sentByNone")} /> : <span className="block truncate text-fg">{sender}</span>;
      },
      width: "w-32",
      hideBelow: "sm",
      card: "meta",
    },
    {
      id: "branch",
      header: t("appSettings.webhook.branchHeader"),
      cell: (row) => (row.branch ? <Mono truncate>{row.branch}</Mono> : <EmptyCell reason={t("appSettings.webhook.noBranchInDelivery")} />),
      width: "w-36",
      card: "meta",
    },
    {
      id: "detail",
      header: t("appSettings.webhook.detailHeader"),
      cell: (row) => (
        <span className="block truncate text-fg-muted" title={row.detail ?? undefined}>
          {row.count > 1 ? `${t("appSettings.webhook.times", { count: row.count })} ` : ""}
          {row.detail ?? ""}
        </span>
      ),
      hideBelow: "md",
      card: "hidden",
    },
  ];
}

/** Every delivery the forge sent, whatever became of it: what shows the setup works. */
function PushesReceived({ domain, status }: { domain: string; status: WebhookStatus }) {
  const t = useT();
  const received = useQuery(webhookReceivedQuery(domain, status.state === "waiting"));
  const forge = forgeName(status.forge.forge, t.locale);
  const total = status.deliveries.total;
  return (
    <Subsection
      title={t("appSettings.webhook.receivedTitle")}
      description={t("appSettings.webhook.receivedDescription")}
      actions={
        <TextLink to="/apps/$domain/deployments" params={{ domain }} size="ui">
          {t("appSettings.webhook.deploymentsLink")}
        </TextLink>
      }
    >
      <QueryState
        query={received}
        label={t("appSettings.webhook.receivedLabel")}
        skeleton={
          <DataTable
            caption={t("appSettings.webhook.receivedCaption", { domain })}
            columns={receivedColumns(t)}
            rows={[]}
            getRowId={(row) => String(row.id)}
            loading
            skeletonRows={Math.min(Math.max(total, 1), RECEIVED_SHOWN)}
            density="compact"
            mobile="cards"
          />
        }
        isEmpty={(data) => data.items.length === 0}
        empty={<EmptyState variant="inline" title={t("appSettings.webhook.nothingReceived", { forge })} />}
      >
        {(data) => (
          <DataTable
            caption={t("appSettings.webhook.receivedCaption", { domain })}
            columns={receivedColumns(t)}
            rows={data.items}
            getRowId={(row) => String(row.id)}
            density="compact"
            mobile="cards"
          />
        )}
      </QueryState>
      {total > RECEIVED_SHOWN ? <p className="text-12 text-fg-muted">{t("appSettings.webhook.newestOf", { shown: RECEIVED_SHOWN, total })}</p> : null}
    </Subsection>
  );
}

/** The other way a push can deploy the app: this server's GitHub App, with no webhook per repository. */
function GitHubApp({ status }: { status: WebhookStatus }) {
  const t = useT();
  const app = status.github_app;
  // Covered, it is the first step of the setup that says so.
  if (app.covers_repository || status.forge.forge !== "github") return null;
  return (
    <p className="max-w-measure text-13 text-pretty text-fg-muted">
      {app.configured ? t("appSettings.webhook.appNotHere") : t("appSettings.webhook.appAlternative")}{" "}
      {app.configured && app.settings_url ? (
        <ExternalLink href={app.settings_url} inline>
          {t("appSettings.webhook.appInstallLink")}
        </ExternalLink>
      ) : (
        <TextLink to="/settings/integrations" size="ui">
          {t("appSettings.webhook.appIntegrationsLink")}
        </TextLink>
      )}
    </p>
  );
}

/**
 * Deploy on push: a push to the repository deploys the app. Said in one sentence, then set up
 * in three steps that each say whether they are done, with the branch that deploys (and the
 * warning when any branch does), what the forge has been sending, the GitHub App as the way
 * without a webhook per repository, and a warning when every push rebuilds a single folder.
 */
export function DeployOnPush() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.push", domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.push");
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const status = useQuery(webhookStatusQuery(domain));
  const [secret, setSecret] = useState<WebhookSecret | null>(null);
  const [confirm, setConfirm] = useState<"rotate" | "disable" | null>(null);
  // The fix of "any push deploys": pinning the branch, from the warning itself.
  const branchPin = useBranchPin(app);

  const settle = (enabled: boolean): void => {
    queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, webhook_enabled: enabled } : known));
    void queryClient.invalidateQueries({ queryKey: settingsKeys.webhook(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
    void queryClient.invalidateQueries({ queryKey: appKeys.webhookDeliveries(domain) });
  };

  const create = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/webhook-secret", { params: { domain } }),
    onSuccess: (result) => {
      setSecret(result);
      settle(true);
    },
    onError: (error: unknown) => {
      reportActionError(t("appSettings.webhook.secretNotCreated"), error);
    },
  });
  const reveal = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/webhook/reveal", { params: { domain } }),
    onSuccess: (result) => {
      setSecret(result);
    },
    onError: (error: unknown) => {
      reportActionError(t("appSettings.webhook.secretNotShown"), error);
    },
  });

  // Sudo mode first, so "Confirm it's you" never opens on top of another dialog.
  const elevated = (then: () => void): void => {
    sudoFirst(then, t("appSettings.webhook.notChanged", { domain }));
  };

  const data = status.data;
  return (
    <Section title={t("appSettings.webhook.title")} {...headerIcon} description={t("appSettings.webhook.description")}>
      <QueryState
        query={status}
        label={t("appSettings.webhook.label")}
        skeleton={
          <div className="flex flex-col gap-4">
            <SkeletonCard lines={1} />
            <SkeletonCard lines={4} />
          </div>
        }
      >
        {(data) => (
          <>
            <Connection status={data} followTags={app.follow_tags ?? null} onRotate={() => elevated(() => setConfirm("rotate"))} onDisable={() => elevated(() => setConfirm("disable"))} />
            <GitHubApp status={data} />
            {data.branch.any_push_deploys && !app.follow_tags ? (
              <Notice
                tone="warning"
                title={t("appSettings.webhook.anyBranchTitle")}
                action={
                  <Button size="sm" onClick={branchPin.pin}>
                    {t("appSettings.branch.pin")}
                  </Button>
                }
              >
                {t("appSettings.webhook.anyBranchBody")}
              </Notice>
            ) : null}
            {data.inplace_warning ? (
              <Notice
                tone="warning"
                title={t("appSettings.webhook.inPlaceTitle")}
                action={
                  <TextLink to="/apps/$domain/settings/deploys" params={{ domain }} size="ui">
                    {t("appSettings.webhook.inPlaceAction")}
                  </TextLink>
                }
              >
                {t("appSettings.webhook.inPlaceBody")}
              </Notice>
            ) : null}
            <Steps
              status={data}
              secret={secret}
              creating={create.isPending}
              revealing={reveal.isPending}
              onCreate={() => elevated(() => create.mutate())}
              onReveal={() => elevated(() => reveal.mutate())}
              onHide={() => setSecret(null)}
            />
            <PushesReceived domain={domain} status={data} />
          </>
        )}
      </QueryState>
      {/* Below the status, not beside its skeleton: the skeleton is a third of the page it becomes, so
          anything drawn under it would be on screen for a moment and then pushed out of view. */}
      {status.isPending ? null : (
        <>
          <GitPushCard app={app} />
          <CommandHint command={`noust app webhook show ${domain}`} label={t("appSettings.fromTerminal")} />
        </>
      )}
      {branchPin.dialogs}
      <ConfirmDialog
        open={confirm === "rotate"}
        onOpenChange={(open) => setConfirm(open ? "rotate" : null)}
        friction="simple"
        server={node}
        title={t("appSettings.webhook.rotateTitle")}
        description={t("appSettings.webhook.rotateDescription", { forge: forgeName(data?.forge.forge, t.locale) })}
        actionLabel={t("appSettings.webhook.rotateAction")}
        onConfirm={async () => {
          const result = await request("post", "/api/apps/{domain}/webhook-secret", { params: { domain } });
          setSecret(result);
          settle(true);
        }}
      />
      <ConfirmDialog
        open={confirm === "disable"}
        onOpenChange={(open) => setConfirm(open ? "disable" : null)}
        friction="simple"
        server={node}
        title={t("appSettings.webhook.disableTitle", { domain })}
        description={t("appSettings.webhook.disableDescription")}
        actionLabel={t("appSettings.webhook.turnOff")}
        onConfirm={async () => {
          await request("delete", "/api/apps/{domain}/webhook-secret", { params: { domain } });
          setSecret(null);
          settle(false);
        }}
      />
    </Section>
  );
}
