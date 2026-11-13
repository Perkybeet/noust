import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { assistantKeys, assistantSettingsQuery, assistantUsageQuery, saveAssistantSettings } from "../../../api/queries/assistant";
import type { AssistantSettings } from "../../../api/queries/assistant";
import { useDocumentTitle } from "../../../app/documentTitle";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Sections } from "../../../components/page/Section";
import { SaveBar } from "../../../components/page/SaveBar";
import { Subsection } from "../../../components/page/Subsection";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Checkbox } from "../../../components/ui/Checkbox";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { useAssistantSession } from "../../assistant/useAssistantSession";
import { LimitsFields } from "./LimitsFields";
import { draftFrom, isOwn, limitsBody } from "./model";
import type { Draft } from "./model";

function Leaves() {
  const t = useT();
  return (
    <div className="grid gap-5 sm:grid-cols-2">
      <Subsection title={t("assistantSettings.data.leaves")}>
        <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5 text-13 text-pretty text-fg">
          <li>{t("assistantSettings.data.leavesList.question")}</li>
          <li>{t("assistantSettings.data.leavesList.page")}</li>
          <li>{t("assistantSettings.data.leavesList.tools")}</li>
        </ul>
      </Subsection>
      <Subsection title={t("assistantSettings.data.never")}>
        <ul className="m-0 flex list-disc flex-col gap-1.5 pl-5 text-13 text-pretty text-fg">
          <li>{t("assistantSettings.data.neverList.secrets")}</li>
          <li>{t("assistantSettings.data.neverList.files")}</li>
          <li>{t("assistantSettings.data.neverList.accounts")}</li>
        </ul>
      </Subsection>
    </div>
  );
}

function DataForm({ settings }: { settings: AssistantSettings }) {
  const t = useT();
  const queryClient = useQueryClient();
  const canManage = useCan("settings.manage");
  // What was spent is read with the permission to use the assistant: whoever only manages it is not told what it cannot read, so the
  // section is not drawn for them and nothing is asked that the server would refuse.
  const mayRead = useAssistantSession() !== null;
  const usage = useQuery({ ...assistantUsageQuery(null), enabled: mayRead });
  const saved = draftFrom(settings);
  const [draft, setDraft] = useState<Draft>(saved);
  const own = isOwn(saved.origin);
  const changed = JSON.stringify([draft.limits, draft.inputPrice, draft.outputPrice, draft.currency, draft.mask]) !== JSON.stringify([saved.limits, saved.inputPrice, saved.outputPrice, saved.currency, saved.mask]);
  const save = useMutation({
    mutationFn: () => saveAssistantSettings({ ...limitsBody(draft, settings), mask_personal: own ? draft.mask : true }),
    onSuccess: (next) => {
      queryClient.setQueryData(assistantKeys.settings, next);
      void queryClient.invalidateQueries({ queryKey: assistantKeys.usage(null) });
      setDraft(draftFrom(next));
    },
  });
  const month = usage.data?.month;
  return (
    <Sections>
      {canManage ? null : <ReadOnlyLine permission="settings.manage" />}
      <SurfaceSection title={t("assistantSettings.data.title")} description={t("assistantSettings.data.description")} icon={OBJECT_ICONS.logShipping}>
        <Leaves />
        <Checkbox
          label={t("assistantSettings.data.mask")}
          description={`${own ? t("assistantSettings.data.maskOwn") : t("assistantSettings.data.maskOutside")} ${t("assistantSettings.data.maskHelp")}`}
          checked={own ? draft.mask : true}
          disabled={!own || !canManage}
          onCheckedChange={(checked) => setDraft({ ...draft, mask: checked })}
        />
      </SurfaceSection>
      {mayRead ? (
        <SurfaceSection title={t("assistantSettings.dataPage.month")} icon={OBJECT_ICONS.activity}>
          {month !== undefined ? (
            <KeyValueList
              items={[
                { label: t("assistantSettings.dataPage.monthSpend"), value: `${formatCount(Math.round(month.spend * 100) / 100, t.locale)} ${usage.data?.currency ?? ""}`.trim(), mono: false },
                { label: t("assistantSettings.dataPage.monthTokens"), value: t("assistantSettings.dataPage.monthTokensValue", { count: formatCount(month.prompt_tokens, t.locale), cap: formatCount(settings.limits["input_tokens_per_conversation"] ?? 0, t.locale) }), mono: false },
              ]}
            />
          ) : null}
          {(usage.data?.notes ?? []).map((note) => (
            <InlineMessage key={note.code} tone="info">
              <ServerText coded={note} text={note.text} />
            </InlineMessage>
          ))}
        </SurfaceSection>
      ) : null}
      <SurfaceSection title={t("assistantSettings.limits.title")} description={t("assistantSettings.limits.description")} icon={OBJECT_ICONS.limits}>
        <LimitsFields draft={draft} onChange={canManage ? setDraft : () => undefined} model={settings.model} />
      </SurfaceSection>
      {save.isError ? <ErrorBlock compact live error={save.error} title={t("assistantSettings.turnOn.saveFailed")} /> : null}
      {canManage ? <SaveBar changes={changed ? 1 : 0} onDiscard={() => setDraft(saved)} onSave={() => save.mutate()} saving={save.isPending} /> : null}
    </Sections>
  );
}

/** What leaves the server and the limits, for an assistant that is on: the same fields as the steps that turned it on, saved from one bar. */
export function AssistantDataPage() {
  const t = useT();
  useDocumentTitle(t("assistantSettings.dataPage.title"), 1);
  const query = useQuery(assistantSettingsQuery());
  return (
    <QueryState query={query} label={t("assistantSettings.loadingLabel")} skeleton={<SkeletonCard lines={4} />}>
      {(settings) => <DataForm settings={settings} />}
    </QueryState>
  );
}
