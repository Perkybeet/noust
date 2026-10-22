import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { removeTeamChannel, saveTeamChannel, teamChannelKeys, teamChannelsQuery, testTeamChannel, typesOf } from "../../api/queries/teamNotifications";
import type { TeamChannel, TeamChannelBody, TeamChannelType } from "../../api/queries/teamNotifications";
import { announce } from "../../app/Announcer";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { ChannelDrawer, ChannelRow, ChannelState, SecretInput, testReasonFor } from "../settings/channelParts";
import type { ChannelTest } from "../settings/channelParts";
import { channels } from "../settings/notifications";
import { FormFailure } from "../settings/SettingsForm";
import { useTeam } from "./TeamLayout";
import { useOrganization } from "./useOrganization";

/** How a team writes a channel's name: the server's own rule, which also bounds its length. */
const NAME_SHAPE = /^[a-z][a-z0-9_-]{0,23}$/;

/** What the server sends in place of a stored credential. */
const STORED = "***";

const SEVERITIES = ["any", "warning", "failed"] as const;
type Severity = (typeof SEVERITIES)[number];

/** A channel's form as text: the settings of its type, whether it is on, and which applications it hears. */
interface Draft {
  enabled: boolean;
  settings: Record<string, string>;
  apps: string;
  projects: string;
  environments: string;
  tags: string;
  severity: Severity;
}

const LISTS = ["apps", "projects", "environments", "tags"] as const;

function written(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return Array.isArray(value) ? value.map(String).join(", ") : "";
}

function splitList(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** The form of a channel as it stands: a stored credential is blank (typing replaces it), the rest is as saved. */
function baselineOf(channel: TeamChannel | null, type: TeamChannelType | undefined): Draft {
  const settings: Record<string, string> = {};
  for (const { key, secret } of type?.keys ?? []) {
    settings[key] = secret ? "" : written(channel?.settings?.[key]);
  }
  const route = channel?.route ?? {};
  const severity = route["min_severity"];
  return {
    enabled: channel?.enabled ?? true,
    settings,
    apps: written(route["apps"]),
    projects: written(route["projects"]),
    environments: written(route["environments"]),
    tags: written(route["tags"]),
    severity: severity === "warning" || severity === "failed" ? severity : "any",
  };
}

/** The request that turns the baseline into the draft; only what differs, so a stored credential is never blanked. */
function bodyOf(draft: Draft, baseline: Draft, type: TeamChannelType | undefined, creating: boolean, typeName: string): TeamChannelBody {
  const settings: Record<string, string> = {};
  for (const { key } of type?.keys ?? []) {
    const value = draft.settings[key] ?? "";
    if (creating ? value !== "" : value !== (baseline.settings[key] ?? "")) settings[key] = value;
  }
  const routeChanged = LISTS.some((list) => draft[list] !== baseline[list]) || draft.severity !== baseline.severity;
  const body: TeamChannelBody = { settings };
  if (creating) body.type = typeName;
  if (creating || draft.enabled !== baseline.enabled) body.enabled = draft.enabled;
  if (routeChanged) {
    body.route = {
      ...Object.fromEntries(LISTS.flatMap((list) => (splitList(draft[list]).length > 0 ? [[list, splitList(draft[list])]] : []))),
      ...(draft.severity === "any" ? {} : { min_severity: draft.severity }),
    };
  }
  return body;
}

function sameDraft(a: Draft, b: Draft): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Which of the channels in the console a type is, for its words and its field labels. */
function specOf(t: T, name: string) {
  return channels(t).find((spec) => spec.id === name);
}

interface DrawerProps {
  team: string;
  /** The channel being changed, or null to add one. */
  channel: TeamChannel | null;
  types: readonly TeamChannelType[];
  open: boolean;
  /** Called with false when it is closed. A drawer of an existing channel stays mounted, shut, so the question to remove it can be asked after. */
  onOpenChange: (open: boolean) => void;
}

/** Adding a channel to the team or changing one: its type's fields, which applications it hears, and a test. */
function TeamChannelDrawer({ team, channel, types, open, onOpenChange }: DrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const creating = channel === null;
  const [name, setName] = useState(channel?.name ?? "");
  const [typeName, setTypeName] = useState(channel?.type ?? types[0]?.name ?? "slack");
  const type = types.find((candidate) => candidate.name === typeName);
  const baseline = baselineOf(channel, type);
  const [draft, setDraft] = useState<Draft | null>(null);
  const current = draft ?? baseline;
  const spec = specOf(t, typeName);
  const channelName = channel?.name ?? name;

  const test: ChannelTest = useMutation({ mutationFn: () => testTeamChannel(team, channelName) });
  const save = useMutation({
    mutationFn: () => saveTeamChannel(team, channelName, bodyOf(current, baseline, type, creating, typeName)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: teamChannelKeys.list(team) });
      setDraft(null);
      // The row appears, or the save bar goes: what is said is for those who do not see it.
      announce(creating ? t("notifyRouting.team.addedToast", { name: channelName }) : t("notifyRouting.team.savedToast", { name: channelName }));
      // A new channel is set up in the same form, so there is nothing left to do here.
      if (creating) onOpenChange(false);
    },
  });

  const set = (patch: Partial<Draft>): void => {
    setDraft({ ...current, ...patch });
  };
  const validName = !creating || NAME_SHAPE.test(name);
  const dirty = validName && (creating ? name !== "" : draft !== null && !sameDraft(draft, baseline));
  const remove = channel === null
    ? undefined
    : {
        label: t("notifyRouting.team.remove"),
        title: t("common.words.removeNamedQuestion", { name: channel.name }),
        description: t("notifyRouting.team.removeDescription"),
        run: async () => {
          await removeTeamChannel(team, channel.name);
          void queryClient.invalidateQueries({ queryKey: teamChannelKeys.list(team) });
          announce(t("notifyRouting.team.removedToast", { name: channel.name }));
        },
      };

  return (
    <ChannelDrawer
      open={open}
      onOpenChange={onOpenChange}
      title={creating ? t("notifyRouting.team.addTitle", { team }) : t("notifyRouting.team.editTitle", { name: channel.name })}
      description={t("notifyRouting.team.drawerDescription")}
      dirty={dirty}
      saving={save.isPending}
      onSubmit={() => {
        save.mutate();
      }}
      onDiscard={() => {
        setDraft(null);
        save.reset();
      }}
      test={test}
      testReason={creating ? t("notifyRouting.team.testAfterAdding") : testReasonFor(t, channel.configured, dirty)}
      remove={remove}
    >
      <FormFailure error={save.error} title={t("notifyRouting.team.saveFailed", { name: channelName === "" ? team : channelName })} />
      {creating ? (
        <>
          <Field label={t("common.words.name")} description={t("notifyRouting.team.nameDescription")} error={name !== "" && !NAME_SHAPE.test(name) ? t("notifyRouting.team.nameInvalid") : undefined}>
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder="slack-ops"
              value={name}
              onValueChange={(next: string) => {
                setName(next);
              }}
            />
          </Field>
          <Field label={t("notifyRouting.more.typeLabel")} nativeLabel={false}>
            <Select
              value={typeName}
              onValueChange={(next) => {
                setTypeName(next);
                setDraft(null);
              }}
              options={types.map((candidate) => ({ value: candidate.name, label: specOf(t, candidate.name)?.label ?? candidate.name }))}
            />
          </Field>
        </>
      ) : null}
      {(type?.keys ?? []).map(({ key, secret }) => {
        const field = spec?.fields.find((candidate) => candidate.key === key);
        const label = field?.label ?? key;
        const stored = channel?.settings?.[key] === STORED;
        return (
          <Field key={key} label={label} {...(field?.description !== undefined ? { description: field.description } : {})} optional={field?.optional === true}>
            {secret ? (
              <SecretInput
                label={label}
                placeholder={field?.placeholder ?? ""}
                value={current.settings[key] ?? ""}
                configured={stored}
                disabled={save.isPending}
                onChange={(value) => {
                  set({ settings: { ...current.settings, [key]: value } });
                }}
              />
            ) : (
              <Input
                mono
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                placeholder={field?.placeholder ?? ""}
                value={current.settings[key] ?? ""}
                disabled={save.isPending}
                onValueChange={(value: string) => {
                  set({ settings: { ...current.settings, [key]: value } });
                }}
              />
            )}
          </Field>
        );
      })}
      <Checkbox
        label={t("notifyRouting.team.enabled")}
        checked={current.enabled}
        onCheckedChange={(next) => {
          set({ enabled: next });
        }}
      />
      <Field label={t("notifyRouting.route.appsLabel")} optional description={t("notifyRouting.team.appsDescription")}>
        <Input mono autoComplete="off" spellCheck={false} placeholder="shop.example.com, *.staging.example.com" value={current.apps} onValueChange={(value: string) => set({ apps: value })} />
      </Field>
      <Field label={t("notifyRouting.route.projectsLabel")} optional description={t("notifyRouting.route.listDescription")}>
        <Input mono autoComplete="off" spellCheck={false} placeholder={t("notifyRouting.route.projectsPlaceholder")} value={current.projects} onValueChange={(value: string) => set({ projects: value })} />
      </Field>
      <Field label={t("notifyRouting.route.environmentsLabel")} optional description={t("notifyRouting.route.listDescription")}>
        <Input mono autoComplete="off" spellCheck={false} placeholder={t("notifyRouting.route.environmentsPlaceholder")} value={current.environments} onValueChange={(value: string) => set({ environments: value })} />
      </Field>
      <Field label={t("notifyRouting.route.tagsLabel")} optional description={t("notifyRouting.route.tagsDescription")}>
        <Input mono autoComplete="off" spellCheck={false} placeholder={t("notifyRouting.route.tagsPlaceholder")} value={current.tags} onValueChange={(value: string) => set({ tags: value })} />
      </Field>
      <Field label={t("notifyRouting.route.minSeverityLabel")} nativeLabel={false}>
        <Select
          value={current.severity}
          onValueChange={(value) => {
            set({ severity: value });
          }}
          options={SEVERITIES.map((value) => ({ value, label: t(`notifyRouting.route.severity.${value}`) }))}
        />
      </Field>
    </ChannelDrawer>
  );
}

/** One channel of the team: what can be done to it depends on the role the principal holds in the team. */
function TeamChannelRow({ team, channel, types, canManage }: { team: string; channel: TeamChannel; types: readonly TeamChannelType[]; canManage: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const test: ChannelTest = useMutation({ mutationFn: () => testTeamChannel(team, channel.name) });
  const label = specOf(t, channel.type)?.label ?? channel.type;
  const on = channel.enabled && channel.configured;

  if (!canManage) {
    return (
      <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 sm:px-5">
        <span className="text-13 font-medium text-fg">{channel.name}</span>
        <ChannelState on={on} />
        <span className="text-12 text-fg-muted">{label}</span>
      </li>
    );
  }
  return (
    <>
      <ChannelRow
        label={channel.name}
        description={t("notifyRouting.more.typeOf", { label })}
        on={on}
        configured={channel.configured}
        test={test}
        onOpen={() => {
          setOpen(true);
        }}
      />
      <TeamChannelDrawer team={team} channel={channel} types={types} open={open} onOpenChange={setOpen} />
    </>
  );
}

/**
 * The notification channels of a team, a tab of the team's page: Slack, ntfy, Telegram or a webhook
 * that hears the applications of this team and nothing else. They do not hear the server itself,
 * and email stays the server's. Reading them takes membership; adding, changing, testing and
 * removing take the admin role in the team (and sudo mode, which the console asks for).
 */
export function TeamNotifications() {
  const t = useT();
  const team = useTeam();
  const org = useOrganization();
  const canManage = org.can("apps.manage", team.slug);
  const list = useQuery(teamChannelsQuery(team.slug));
  const [adding, setAdding] = useState(false);
  useDocumentTitle(t("notifyRouting.team.documentTitle", { team: team.name }), 1);

  const types = list.data === undefined ? [] : typesOf(list.data.types);
  const items = list.data?.items ?? [];

  return (
    <div className="flex flex-col gap-6">
      <Section
        title={t("notifyRouting.team.title")}
        description={t("notifyRouting.team.description")}
        {...(canManage
          ? {
              actions: (
                <Button
                  icon={<Plus aria-hidden="true" />}
                  disabled={list.data === undefined || types.length === 0}
                  onClick={() => {
                    setAdding(true);
                  }}
                >
                  {t("notifyRouting.team.add")}
                </Button>
              ),
            }
          : {})}
      >
        <QueryState
          query={list}
          label={t("notifyRouting.team.queryLabel")}
          skeleton={
            <Card padding="none">
              <div className="flex items-center justify-between gap-4 px-4 py-3 sm:px-5">
                <Skeleton className="h-4 w-64 max-w-full" />
                <Skeleton className="h-control-sm w-20" />
              </div>
            </Card>
          }
        >
          {() => (
            <Card padding="none">
              {items.length === 0 ? (
                <p className="px-4 py-3 text-13 text-fg-muted sm:px-5">{canManage ? t("notifyRouting.team.empty") : t("notifyRouting.team.emptyReadOnly")}</p>
              ) : (
                <ul aria-label={t("notifyRouting.team.listLabel")} className="flex min-w-0 flex-col divide-y divide-border">
                  {items.map((channel) => (
                    <TeamChannelRow key={channel.name} team={team.slug} channel={channel} types={types} canManage={canManage} />
                  ))}
                </ul>
              )}
            </Card>
          )}
        </QueryState>
      </Section>
      {!canManage && list.data !== undefined ? <p className="text-13 text-fg-muted">{t("notifyRouting.team.readOnly")}</p> : null}
      {adding ? (
        <TeamChannelDrawer team={team.slug} channel={null} types={types} open onOpenChange={setAdding} />
      ) : null}
      <CommandHint label={t("notifyRouting.team.fromTerminal")} command={`noust notify channel set alerts --team ${team.slug} --type slack --secret-stdin webhook_url`} />
    </div>
  );
}
