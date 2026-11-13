import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { assistantKeys, assistantSettingsQuery, probeAssistant, turnAssistantOff } from "../../../api/queries/assistant";
import type { AssistantSettings } from "../../../api/queries/assistant";
import { sessionQuery } from "../../../api/queries/auth";
import { useDocumentTitle } from "../../../app/documentTitle";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { GuardedButton, ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { QueryState } from "../../../components/page/QueryState";
import { Section, Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { locationWord } from "../../../components/assistant/AiMark";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { useT } from "../../../i18n";
import { SettingsMasterSwitch, SettingsPrimaryAction } from "../SettingsPrimaryAction";
import { ProbeResult } from "./ProbeResult";
import { TurnOnWizard } from "./TurnOnWizard";
import type { Step } from "./search";

const FACT_ICONS = [OBJECT_ICONS.diagnose, OBJECT_ICONS.logShipping, OBJECT_ICONS.approval] as const;

/** What it is while it is off, in three things, and the one way to turn it on. Nothing of it exists anywhere else in the console. */
function OffView({ canTurnOn }: { canTurnOn: boolean }) {
  const t = useT();
  const session = useQuery(sessionQuery());
  const ens = session.data?.security_profile === "ens-medium";
  const facts = [
    { title: t("assistantSettings.off.facts.forTitle"), body: t("assistantSettings.off.facts.forBody") },
    { title: t("assistantSettings.off.facts.leavesTitle"), body: t("assistantSettings.off.facts.leavesBody") },
    { title: t("assistantSettings.off.facts.decidesTitle"), body: t("assistantSettings.off.facts.decidesBody") },
  ];
  return (
    <Sections>
      {canTurnOn ? null : <ReadOnlyLine permission="settings.manage" />}
      <Card padding="md" title={t("assistantSettings.off.factsTitle")} icon={OBJECT_ICONS.assistant}>
        <ul className="m-0 flex list-none flex-col gap-4 p-0">
          {facts.map((fact, index) => {
            const Icon = FACT_ICONS[index] ?? OBJECT_ICONS.diagnose;
            return (
              <li key={fact.title} className="flex items-start gap-3">
                <Icon aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
                <div className="min-w-0">
                  <p className="m-0 text-13 font-medium text-fg">{fact.title}</p>
                  <p className="m-0 max-w-measure text-13 text-pretty text-fg-muted">{fact.body}</p>
                </div>
              </li>
            );
          })}
        </ul>
      </Card>
      {ens ? (
        <Notice title={t("assistantSettings.off.ens.title")}>{t("assistantSettings.off.ens.body")}</Notice>
      ) : null}
    </Sections>
  );
}

function OnView({ settings }: { settings: AssistantSettings }) {
  const t = useT();
  const queryClient = useQueryClient();
  const canManage = useCan("settings.manage");
  const again = useMutation({
    mutationFn: probeAssistant,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: assistantKeys.settings });
      toast.info(t("assistantSettings.on.againDone"));
    },
  });
  const off = useMutation({
    mutationFn: turnAssistantOff,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["auth", "session"] });
    },
  });
  return (
    <Sections>
      {canManage ? null : <ReadOnlyLine permission="settings.manage" />}
      <SurfaceSection title={t("assistantSettings.on.model.title")} description={t("assistantSettings.on.model.description")} icon={OBJECT_ICONS.assistant}>
        <KeyValueList
          items={[
            { label: t("assistantSettings.on.facts.model"), value: settings.model, mono: true },
            { label: t("assistantSettings.on.facts.where"), value: locationWord(t, settings.location), mono: false },
            { label: t("assistantSettings.on.facts.address"), value: settings.base_url, mono: true },
            { label: t("assistantSettings.on.facts.key"), value: settings.has_key ? t("assistantSettings.on.facts.keyYes") : t("assistantSettings.on.facts.keyNo"), mono: false },
            ...(settings.probed_at !== null && settings.probed_at !== undefined ? [{ label: t("assistantSettings.on.facts.probed"), value: <RelativeTime value={settings.probed_at} />, mono: false }] : []),
          ]}
        />
        {settings.probe !== null && settings.probe !== undefined ? <ProbeResult probe={settings.probe} /> : null}
        {canManage ? (
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/settings/assistant" search={{ step: "model" }} className={buttonClassName("secondary", "md")}>
              {t("assistantSettings.on.change")}
            </Link>
            <Button variant="secondary" icon={<VERB_ICONS.test aria-hidden="true" />} loading={again.isPending} onClick={() => again.mutate()}>
              {t("assistantSettings.on.again")}
            </Button>
          </div>
        ) : null}
      </SurfaceSection>
      <SurfaceSection title={t("assistantSettings.on.can.title")} icon={OBJECT_ICONS.diagnose}>
        <InlineMessage tone="info">{settings.calls_tools ? t("assistantSettings.on.can.yes") : t("assistantSettings.on.can.no")}</InlineMessage>
        <p>
          <TextLink to="/settings/assistant/data" size="ui">
            {t("assistantSettings.on.data")}
          </TextLink>
        </p>
      </SurfaceSection>
      {canManage ? (
        <Section title={t("assistantSettings.on.off.title")} description={t("assistantSettings.on.off.description")}>
          <div>
            <ConfirmDialog
              friction="simple"
              title={t("assistantSettings.on.off.confirmTitle")}
              description={t("assistantSettings.on.off.confirmBody")}
              actionLabel={t("assistantSettings.on.off.confirmAction")}
              onConfirm={async () => {
                await off.mutateAsync();
              }}
              trigger={<Button variant="secondary">{t("assistantSettings.on.off.action")}</Button>}
            />
          </div>
        </Section>
      ) : null}
    </Sections>
  );
}

/**
 * Settings > AI assistant: what it is when it is off and the four steps that turn it on, and, once it is on, the model it uses and
 * what it can do with it, what leaves and the limits, who may use it, and turning it off. Off, nothing of it is drawn anywhere else
 * in the console; this page is for whoever may turn it on.
 */
export function AssistantSettingsPage({ step, onStep }: { step: Step | undefined; onStep: (step: Step | undefined) => void }) {
  const t = useT();
  useDocumentTitle(t("assistantSettings.documentTitle"), 1);
  const query = useQuery(assistantSettingsQuery());
  const session = useQuery(sessionQuery());
  const canManage = useCan("settings.manage");
  // A central that deploys nothing itself cannot turn it on: the session says so with the sentence of why, and it is said as it is.
  const hubReason = session.data?.assistant?.reason?.code === "features.hub_role" ? session.data.assistant.reason : null;
  const cannotHere = hubReason !== null;
  return (
    <>
      {query.data !== undefined ? (
        <SettingsMasterSwitch>
          <StatusPill state={query.data.enabled ? "running" : "stopped"} label={query.data.enabled ? t("assistantSettings.state.on") : t("assistantSettings.state.off")} />
        </SettingsMasterSwitch>
      ) : null}
      {query.data !== undefined && !query.data.enabled && step === undefined ? (
        <SettingsPrimaryAction>
          {canManage && !cannotHere ? (
            <Link to="/settings/assistant" search={{ step: "model" }} className={buttonClassName("primary", "md")}>
              {t("assistantSettings.off.turnOn")}
            </Link>
          ) : (
            <GuardedButton permission="settings.manage" variant="primary" {...(hubReason !== null ? { unavailable: <ServerText coded={hubReason} text={hubReason.text} /> } : {})}>
              {t("assistantSettings.off.turnOn")}
            </GuardedButton>
          )}
        </SettingsPrimaryAction>
      ) : null}
      <QueryState query={query} label={t("assistantSettings.loadingLabel")} skeleton={<SkeletonCard lines={4} />}>
        {(settings) =>
          settings.enabled ? (
            <OnView settings={settings} />
          ) : step !== undefined && canManage ? (
            <TurnOnWizard settings={settings} step={step} onStep={onStep} />
          ) : (
            <OffView canTurnOn={canManage} />
          )
        }
      </QueryState>
    </>
  );
}

