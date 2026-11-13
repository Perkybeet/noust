/**
 * Where a channel keeps messages together, in the routing of a channel (item 190).
 *
 * A Telegram group with topics turned on can hold each subject (deploys, alerts, backups,
 * security, system) in a topic of its own, so the general thread is not every message of every
 * kind: the channel posts in the general thread, in one topic, or in a topic per subject, and Noust
 * can create the topics that are missing when the bot may. What a channel cannot do is not offered:
 * it says why, in a sentence, from the code the server gives (`types[].grouping.reason`). The
 * subject of each event and what each channel type can do come from the server; nothing here keeps
 * a table of its own.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { configKeys } from "../../api/queries/config";
import { notificationKeys } from "../../api/queries/notifications";
import { createTelegramTopics, notificationEventsQuery } from "../../api/queries/notificationTopics";
import type { ChannelNotice, ChannelTypeInfo, NotificationChannel, NotificationEvents, TopicsResult } from "../../api/queries/notifications";
import { Can } from "../../components/page/permissions";
import { QueryState, ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { ICONS } from "../../components/ui/icons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { eventSpec } from "./notifications";
import { TOPIC_PLACES, isTopicNumber } from "./routing";
import type { RoutingForm, TopicPlace } from "./routing";

/** The subjects the console has words for; a subject a newer server adds is shown by its own name. */
const KNOWN_SUBJECTS = ["deploys", "alerts", "backups", "security", "system"] as const;
type KnownSubject = (typeof KNOWN_SUBJECTS)[number];

/** The reasons a channel cannot group that the console words (`noust.core.notifications.grouping.REASONS`). */
const KNOWN_REASONS = ["incoming_webhook", "workflow_webhook", "custom_bot", "no_threads", "incidents", "receiver", "not_yet"] as const;
type KnownReason = (typeof KNOWN_REASONS)[number];

/** The problems creating topics can end in (`noust.core.telegram_topics.PROBLEMS`). */
const KNOWN_PROBLEMS = ["not_forum", "not_member", "no_right", "chat_unreachable", "unreachable", "refused"] as const;
type KnownProblem = (typeof KNOWN_PROBLEMS)[number];

function isKnown<K extends string>(known: readonly K[], value: string): value is K {
  return (known as readonly string[]).includes(value);
}

/** A subject's name for the operator. */
function subjectName(t: T, subject: string): string {
  return isKnown<KnownSubject>(KNOWN_SUBJECTS, subject) ? t(`notifyRouting.grouping.subject.${subject}`) : subject;
}

/** What a subject covers, in a line: the first events by name, and how many more there are. */
function coversText(t: T, kinds: readonly string[]): string {
  const shown = kinds.slice(0, 3).map((kind) => eventSpec(t, kind).label);
  const more = kinds.length - shown.length;
  const names = shown.join(", ");
  return more > 0 ? t("notifyRouting.grouping.coversMore", { names, count: more }) : t("notifyRouting.grouping.covers", { names });
}

/** The events of each subject, in the order the server lists the subjects. */
function subjectsOf(events: NotificationEvents): { subject: string; kinds: string[] }[] {
  return (events.subjects ?? []).map((subject) => ({
    subject,
    kinds: events.items.filter((item) => item.subject === subject).map((item) => item.kind),
  }));
}

export interface GroupingPartProps {
  channel: NotificationChannel;
  /** What the channel's type can do, from the server. */
  typeInfo: ChannelTypeInfo | undefined;
  form: RoutingForm;
  edit: (change: (current: RoutingForm) => RoutingForm) => void;
  /** The form has changes that are not saved: what is asked of the group is asked of what is saved. */
  dirty: boolean;
}

/**
 * The routing's part about keeping messages together: the topics of a Telegram group, or what a
 * channel does instead (a conversation in a mailbox) or why it cannot (a webhook that cannot reply).
 */
export function GroupingPart({ channel, typeInfo, form, edit, dirty }: GroupingPartProps) {
  const t = useT();
  if (typeInfo === undefined) return null;
  const { topics, replies, reason } = typeInfo.grouping;
  if (topics) return <TopicsPart channel={channel} form={form} edit={edit} dirty={dirty} />;
  const sentence = replies ? t("notifyRouting.grouping.conversations") : reason !== null && reason !== undefined && isKnown<KnownReason>(KNOWN_REASONS, reason) ? t(`notifyRouting.grouping.reason.${reason}`) : null;
  if (sentence === null) return null;
  return (
    <Subsection title={t("notifyRouting.grouping.title")}>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{sentence}</p>
    </Subsection>
  );
}

function TopicsPart({ channel, form, edit, dirty }: Pick<GroupingPartProps, "channel" | "form" | "edit" | "dirty">) {
  const t = useT();
  // What Noust created is what the channel has now: the form takes it, so it is not a change to save.
  const adopt = (topics: Readonly<Record<string, number>>): void => {
    if (Object.keys(topics).length === 0) return;
    edit((current) => ({ ...current, place: "subjects", topics: Object.fromEntries(Object.entries(topics).map(([subject, number]) => [subject, String(number)])) }));
  };
  const events = useQuery(notificationEventsQuery());
  const options = TOPIC_PLACES.map((value) => ({ value, label: t(`notifyRouting.grouping.place.${value}`) }));
  const threads = channel.settings?.["threads"];
  const ownEvents = threads !== null && typeof threads === "object" && !Array.isArray(threads) ? Object.keys(threads).length : 0;
  const notices = channel.notices ?? [];

  return (
    <Subsection title={t("notifyRouting.grouping.topicsTitle")} description={t("notifyRouting.grouping.topicsDescription")}>
      <SegmentedControl<TopicPlace>
        label={t("notifyRouting.grouping.place.label")}
        options={options}
        value={form.place}
        onValueChange={(place) => {
          edit((current) => ({ ...current, place }));
        }}
        className="self-start"
      />
      <p className="text-12 text-pretty text-fg-muted">{t(`notifyRouting.grouping.placeHint.${form.place}`)}</p>
      {form.place === "one" ? (
        <Field
          label={t("notifyRouting.grouping.oneTopicLabel")}
          description={t("notifyRouting.grouping.oneTopicDescription")}
          optional
          error={form.topic.trim() !== "" && !isTopicNumber(form.topic) ? t("notifyRouting.grouping.topicBad") : null}
        >
          <Input
            mono
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            placeholder={t("notifyRouting.grouping.topicPlaceholder")}
            value={form.topic}
            onValueChange={(topic: string) => {
              edit((current) => ({ ...current, topic }));
            }}
          />
        </Field>
      ) : null}
      {form.place === "subjects" ? (
        <QueryState
          query={events}
          sentences={{
            loading: t("notifyRouting.grouping.loadingSubjects"),
            failed: t("notifyRouting.grouping.couldNotLoadSubjects"),
            refreshFailed: t("notifyRouting.grouping.couldNotRefreshSubjects"),
          }}
          skeleton={<SubjectsSkeleton />}
        >
          {(data) => <SubjectTopics channel={channel} events={data} form={form} edit={edit} dirty={dirty} onCreated={adopt} />}
        </QueryState>
      ) : null}
      {ownEvents > 0 ? <p className="text-12 text-pretty text-fg-muted">{t("notifyRouting.grouping.threadsNote", { count: ownEvents })}</p> : null}
      {notices.map((notice) => (
        <LostTopic key={notice.topic} channel={channel} notice={notice} dirty={dirty} onCreated={adopt} again={form.place === "subjects" && notice.subject !== null && notice.subject !== undefined} />
      ))}
      <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("notifyRouting.grouping.replies")}</p>
    </Subsection>
  );
}

function SubjectsSkeleton() {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {KNOWN_SUBJECTS.map((subject) => (
        <Skeleton key={subject} className="h-14 w-full" />
      ))}
    </div>
  );
}

function SubjectTopics({
  channel,
  events,
  form,
  edit,
  dirty,
  onCreated,
}: {
  channel: NotificationChannel;
  events: NotificationEvents;
  form: RoutingForm;
  edit: GroupingPartProps["edit"];
  dirty: boolean;
  onCreated: (topics: Readonly<Record<string, number>>) => void;
}) {
  const t = useT();
  const subjects = useMemo(() => subjectsOf(events), [events]);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <fieldset className="flex min-w-0 flex-col gap-4">
        <legend className="sr-only">{t("notifyRouting.grouping.subjectsLegend")}</legend>
        {subjects.map(({ subject, kinds }) => {
          const value = form.topics[subject] ?? "";
          return (
            <Field
              key={subject}
              label={subjectName(t, subject)}
              description={coversText(t, kinds)}
              optional
              error={value.trim() !== "" && !isTopicNumber(value) ? t("notifyRouting.grouping.topicBad") : null}
            >
              <Input
                mono
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
                placeholder={t("notifyRouting.grouping.topicPlaceholder")}
                value={value}
                onValueChange={(next: string) => {
                  edit((current) => ({ ...current, topics: { ...current.topics, [subject]: next } }));
                }}
              />
            </Field>
          );
        })}
      </fieldset>
      <CreateTopics channel={channel} dirty={dirty} onCreated={onCreated} />
    </div>
  );
}

/** The button that asks the group, through the bot, for the topics that are missing, and what came of it. */
function CreateTopics({ channel, dirty, onCreated }: { channel: NotificationChannel; dirty: boolean; onCreated: (topics: Readonly<Record<string, number>>) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const create = useMutation({
    mutationFn: () => createTelegramTopics(channel.name, false),
    onSuccess: (result) => {
      onCreated(result.topics ?? {});
    },
    onSettled: async () => {
      // The numbers it saved are part of the channel and of the configuration.
      await Promise.all([queryClient.invalidateQueries({ queryKey: notificationKeys.channels }), queryClient.invalidateQueries({ queryKey: configKeys.all })]);
    },
  });
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {/* Asking the group for topics changes the channel: it is whoever manages the settings who may. */}
        <Can permission="settings.manage">
          <Button
            size="sm"
            icon={<ICONS.add aria-hidden="true" />}
            loading={create.isPending}
            disabled={dirty}
            onClick={() => {
              create.mutate();
            }}
          >
            {t("notifyRouting.grouping.create.button")}
          </Button>
        </Can>
        <span className="text-12 text-fg-muted">{dirty ? t("notifyRouting.grouping.create.needsSave") : t("notifyRouting.grouping.create.hint")}</span>
      </div>
      {create.isError ? <ErrorBlock live compact error={create.error} title={t("notifyRouting.grouping.create.failedTitle")} /> : null}
      {create.data !== undefined ? <CreatedTopics result={create.data} /> : null}
    </div>
  );
}

function CreatedTopics({ result }: { result: TopicsResult }) {
  const t = useT();
  const made = Object.keys(result.created ?? {}).length;
  if (result.problem === null || result.problem === undefined) {
    return (
      <InlineMessage tone="success" live>
        {made > 0 ? t("notifyRouting.grouping.create.done", { count: made }) : t("notifyRouting.grouping.create.nothing")}
      </InlineMessage>
    );
  }
  const problem = result.problem;
  const words = isKnown<KnownProblem>(KNOWN_PROBLEMS, problem) ? { title: t(`notifyRouting.grouping.create.problem.${problem}.title`), fix: t(`notifyRouting.grouping.create.problem.${problem}.fix`) } : { title: t("notifyRouting.grouping.create.failedTitle"), fix: "" };
  const notMade = (result.failed ?? []).map((subject) => subjectName(t, subject)).join(", ");
  return (
    <Notice tone="error" live title={words.title}>
      <div className="flex min-w-0 flex-col gap-2">
        {words.fix !== "" ? <p>{words.fix}</p> : null}
        {made > 0 ? <p>{t("notifyRouting.grouping.create.partial", { count: made })}</p> : null}
        {notMade !== "" && made > 0 ? <p>{t("notifyRouting.grouping.create.notMade", { names: notMade })}</p> : null}
        {result.detail !== null && result.detail !== undefined && result.detail !== "" ? (
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-12 text-fg-muted">{t("notifyRouting.grouping.create.saidLabel")}</span>
            <SystemOutput label={t("notifyRouting.grouping.create.saidLabel")} className="rounded-control border border-border bg-surface px-3 py-2">
              {result.detail}
            </SystemOutput>
          </div>
        ) : null}
      </div>
    </Notice>
  );
}

/** A topic of the channel that stopped taking messages: said with Telegram's own words, and the way to make it again. */
function LostTopic({
  channel,
  notice,
  dirty,
  again,
  onCreated,
}: {
  channel: NotificationChannel;
  notice: ChannelNotice;
  dirty: boolean;
  again: boolean;
  onCreated: (topics: Readonly<Record<string, number>>) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const recreate = useMutation({
    mutationFn: () => createTelegramTopics(channel.name, true),
    onSuccess: (result) => {
      onCreated(result.topics ?? {});
    },
    onSettled: async () => {
      await Promise.all([queryClient.invalidateQueries({ queryKey: notificationKeys.channels }), queryClient.invalidateQueries({ queryKey: configKeys.all })]);
    },
  });
  const subject = notice.subject;
  const title = subject !== null && subject !== undefined ? t("notifyRouting.grouping.lost.titleSubject", { subject: subjectName(t, subject), topic: notice.topic }) : t("notifyRouting.grouping.lost.title", { topic: notice.topic });
  return (
    <Notice
      tone="warning"
      title={title}
      {...(again
        ? {
            action: (
              <Can permission="settings.manage">
                <Button size="sm" loading={recreate.isPending} disabled={dirty} onClick={() => { recreate.mutate(); }}>
                  {t("notifyRouting.grouping.lost.again")}
                </Button>
              </Can>
            ),
          }
        : {})}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p>{t.rich("notifyRouting.grouping.lost.body", { when: <RelativeTime key="when" value={notice.at} /> })}</p>
        {notice.reason !== "" ? (
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-12 text-fg-muted">{t("notifyRouting.grouping.lost.saidLabel")}</span>
            <SystemOutput label={t("notifyRouting.grouping.lost.saidLabel")} className="rounded-control border border-border bg-surface px-3 py-2">
              {notice.reason}
            </SystemOutput>
          </div>
        ) : null}
        {again ? <p className="text-12 text-fg-muted">{t("notifyRouting.grouping.lost.againHint")}</p> : null}
        {recreate.isError ? <ErrorBlock live compact error={recreate.error} title={t("notifyRouting.grouping.create.failedTitle")} /> : null}
        {recreate.data?.problem !== undefined && recreate.data.problem !== null ? <CreatedTopics result={recreate.data} /> : null}
      </div>
    </Notice>
  );
}
