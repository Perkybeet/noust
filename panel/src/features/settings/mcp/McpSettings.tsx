import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { mcpKeys, mcpStatusQuery, saveMcpSettings } from "../../../api/queries/mcp";
import type { McpStatus, McpTool } from "../../../api/queries/mcp";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Section, Sections } from "../../../components/page/Section";
import { SettingsGroup } from "../../../components/page/SettingsGroup";
import { Badge } from "../../../components/ui/Badge";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Mono } from "../../../components/ui/Mono";
import { Setting } from "../../../components/ui/Setting";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";

const KNOWN_GROUPS = ["inspect", "deploy", "control"] as const;
type KnownGroup = (typeof KNOWN_GROUPS)[number];

function isKnownGroup(name: string): name is KnownGroup {
  return KNOWN_GROUPS.some((known) => known === name);
}

function groupName(t: T, name: string): string {
  return isKnownGroup(name) ? t(`mcpSettings.groups.${name}.name`) : name;
}

function effectOf(t: T, tool: McpTool): { label: string; asksTwice: boolean } {
  if (tool.destructive) return { label: t("mcpSettings.tools.asksTwice"), asksTwice: true };
  return { label: tool.read_only ? t("mcpSettings.tools.reads") : t("mcpSettings.tools.changes"), asksTwice: false };
}

function columnsFor(t: T): Column<McpTool>[] {
  return [
    {
      id: "tool",
      header: t("mcpSettings.tools.column.tool"),
      card: "title",
      cell: (tool) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <Mono tone="default">{tool.name}</Mono>
          <span translate="no" className="text-12 text-fg-muted">
            {tool.title}
          </span>
        </span>
      ),
    },
    {
      id: "effect",
      header: t("mcpSettings.tools.column.effect"),
      card: "status",
      width: "w-36",
      cell: (tool) => {
        const effect = effectOf(t, tool);
        // A tool that asks twice is one to look at: a state's shape and word, never an amber chip.
        return effect.asksTwice ? <StatusPill state="warning" label={effect.label} appearance="inline" size="sm" /> : <Badge>{effect.label}</Badge>;
      },
    },
    {
      id: "group",
      header: t("mcpSettings.tools.column.group"),
      hideBelow: "sm",
      cell: (tool) => <span className="text-13 text-fg-muted">{groupName(t, tool.group)}</span>,
    },
    {
      id: "permission",
      header: t("mcpSettings.tools.column.permission"),
      hideBelow: "md",
      cell: (tool) => <Mono tone="default">{tool.permission}</Mono>,
    },
  ];
}

/** The tools of one group among those the server lists, to say how many it holds. */
function toolsIn(status: McpStatus, group: string): number {
  return status.tools.filter((tool) => tool.group === group).length;
}

/**
 * Settings > MCP: whether the endpoint for MCP clients answers, the groups of tools it offers, and
 * the tools themselves. It is off until an operator turns it on, and what a client can do is its
 * API token's: this page chooses what is offered, never what a token holds.
 */
export function McpSettings() {
  const t = useT();
  useDocumentTitle(t("mcpSettings.documentTitle"), 1);
  const queryClient = useQueryClient();
  const query = useQuery(mcpStatusQuery());

  const change = useMutation({
    mutationFn: (body: { enabled?: boolean; groups?: string[] }) => saveMcpSettings(body),
    onSuccess: (_status, body) => {
      if (body.enabled !== undefined) toast.success(body.enabled ? t("mcpSettings.state.turnedOn") : t("mcpSettings.state.turnedOff"));
      else toast.success(t("mcpSettings.groups.changed"));
      void queryClient.invalidateQueries({ queryKey: mcpKeys.status });
    },
  });
  const columns = columnsFor(t);

  return (
    <Sections>
      <QueryState
        query={query}
        label={t("mcpSettings.loadingLabel")}
        skeleton={
          <div className="flex flex-col gap-8">
            <SkeletonCard lines={2} />
            <SkeletonCard lines={4} />
          </div>
        }
      >
        {(status) => (
          <>
            <SettingsGroup
              level={2}
              title={t("mcpSettings.state.title")}
              description={t("mcpSettings.state.description")}
              status={<StatusPill state={status.enabled ? "running" : "stopped"} label={status.enabled ? t("mcpSettings.state.on") : t("mcpSettings.state.off")} size="sm" />}
              command={t("mcpSettings.commandEnable")}
            >
              <Setting
                label={t("mcpSettings.state.label")}
                help={t("mcpSettings.state.help")}
                checked={status.enabled}
                busy={change.isPending && change.variables.enabled !== undefined}
                onCheckedChange={(enabled) => {
                  change.mutate({ enabled });
                }}
              />
            </SettingsGroup>
            {change.isError ? <ErrorBlock live compact error={change.error} title={t("mcpSettings.state.failed")} /> : null}

            <Section title={t("mcpSettings.connection.title")} description={t("mcpSettings.connection.description")}>
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-1.5">
                  <p className="text-13 font-medium text-fg">{t("mcpSettings.connection.address")}</p>
                  <CopyValue value={status.endpoint} label={t("mcpSettings.connection.copyAddress")} name={t("mcpSettings.connection.address")} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <p className="text-13 font-medium text-fg">{t("mcpSettings.connection.token")}</p>
                  <p className="max-w-measure text-13 text-fg-muted">{t("mcpSettings.connection.tokenHelp")}</p>
                  <TextLink to="/settings/tokens" size="ui">
                    {t("mcpSettings.connection.tokensLink")}
                  </TextLink>
                </div>
                <KeyValueList items={[{ label: t("mcpSettings.connection.protocols"), value: status.protocol_versions.join(", "), mono: true, copy: false }]} />
              </div>
            </Section>

            <SettingsGroup level={2} title={t("mcpSettings.groups.title")} description={t("mcpSettings.groups.description")}>
              {status.all_groups.map((group) => (
                <Setting
                  key={group}
                  label={groupName(t, group)}
                  help={isKnownGroup(group) ? t(`mcpSettings.groups.${group}.description`) : undefined}
                  status={<Badge>{t("mcpSettings.groups.toolCount", { count: toolsIn(status, group) })}</Badge>}
                  checked={status.groups.includes(group)}
                  busy={change.isPending && change.variables.groups !== undefined}
                  onCheckedChange={(on) => {
                    const next = on ? [...status.groups, group] : status.groups.filter((item) => item !== group);
                    change.mutate({ groups: status.all_groups.filter((item) => next.includes(item)) });
                  }}
                />
              ))}
            </SettingsGroup>

            <Section title={t("mcpSettings.tools.title")} description={t("mcpSettings.tools.description")}>
              <div className="flex flex-col gap-4">
                <DataTable caption={t("mcpSettings.tools.tableCaption")} columns={columns} rows={status.tools} getRowId={(tool) => tool.name} mobile="cards" pagination="mcp-tools" />
                <KeyValueList
                  items={[
                    { label: t("mcpSettings.tools.prompts"), value: status.prompts.join(", "), mono: true, copy: false },
                    { label: t("mcpSettings.tools.resources"), value: status.resources.join(", "), mono: true, copy: false },
                  ]}
                />
              </div>
            </Section>

            <CommandHint command={t("mcpSettings.commandToken")} />
          </>
        )}
      </QueryState>
    </Sections>
  );
}
