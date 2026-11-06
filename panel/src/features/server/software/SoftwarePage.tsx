/**
 * Server > Software (4.2, item 118): what the machine is made of as far as Noust is concerned. Each piece with what
 * Noust uses it for, whether it is there and in which version, who put it there, what depends on it and whether it
 * can go; removing asks the server first what it would do, and installing is a task the task centre follows.
 *
 * The page decides nothing about what may be removed: that is the server's manager, which refuses with its reason
 * and the list of what stands in the way. The page only asks, and says the answer.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Disclosure } from "../../../components/ui/Disclosure";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { useCan } from "../../../components/page/permissions";
import { useT } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { ServerQueryState } from "../errors";
import { TabToolbar } from "../TabToolbar";
import { useServerJob } from "../serverJob";
import { useInstall } from "./InstallSoftware";
import { RemoveSoftware } from "./RemoveSoftware";
import { GROUPS, GROUP_LABELS, groupOf, installPermissions, removePermissions } from "./registry";
import type { GroupId } from "./registry";
import { SOFTWARE_CAPABILITY, recheckSoftware, softwareKeys, softwareQuery } from "./queries";
import type { SoftwareItem, SoftwareList } from "./queries";
import { SoftwareTable } from "./SoftwareTable";
import { componentName } from "./words";

type Show = "all" | "installed" | "missing";

function visible(items: readonly SoftwareItem[], show: Show): SoftwareItem[] {
  return items.filter((item) => (show === "installed" ? item.installed : show === "missing" ? !item.installed : true));
}

/** The pieces by group, in the order the page lists them; a group with none to show is left out. */
export function groupsOf(items: readonly SoftwareItem[]): { group: GroupId; items: SoftwareItem[] }[] {
  return GROUPS.map((group) => ({ group, items: items.filter((item) => groupOf(item.id) === group) })).filter((entry) => entry.items.length > 0);
}

/** The machine's own pieces, which Noust never removes: seven rows nobody acts on, so they are one line until asked for. */
function isBase(group: GroupId, rows: readonly SoftwareItem[]): boolean {
  return group === "machine" && rows.every((row) => row.installed && row.severity === "never");
}

function FoldedBase({ items, children }: { items: readonly SoftwareItem[]; children: ReactNode }) {
  const t = useT();
  return (
    <Disclosure variant="card" label={items.map((item) => componentName(t, item.id)).join(", ")} summary={t("software.machine.note")}>
      {children}
    </Disclosure>
  );
}

function Content({ data, checkedAt }: { data: SoftwareList; checkedAt: number }) {
  const t = useT();
  const can = useCan();
  const jobs = useServerJob();
  const queryClient = useQueryClient();
  const [show, setShow] = useState<Show>("all");
  const [removing, setRemoving] = useState<SoftwareItem | null>(null);
  const installing = useInstall(data.family);
  const recheck = useMutation({
    mutationFn: recheckSoftware,
    onSuccess: (fresh) => queryClient.setQueryData(softwareKeys.list, fresh),
  });
  const items = data.items ?? [];
  const installed = items.filter((item) => item.installed).length;
  const shown = visible(items, show);
  const managed = data.managed;
  const canInstall = (id: string): boolean => managed && installPermissions(id).every(can);
  const canRemove = (id: string): boolean => managed && removePermissions(id).every(can);
  // A page nobody may change anything from says so once, above, instead of drawing nothing in every row.
  const readOnly = managed && !items.some((item) => (item.installed ? item.severity !== "never" && canRemove(item.id) : item.installable && canInstall(item.id)));
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar
        summary={t("software.summary")}
        actions={
          <Button icon={<VERB_ICONS.refresh aria-hidden="true" />} loading={recheck.isPending} onClick={() => recheck.mutate()}>
            {t("software.recheck")}
          </Button>
        }
      />
      {!managed ? (
        <Notice tone="warning" title={t("software.unmanaged")}>
          <ServerText coded={data.reason_coded} text={data.reason} />
        </Notice>
      ) : readOnly ? (
        <Notice title={t("software.readOnly")} />
      ) : null}
      {managed && jobs.busy ? <p className="text-13 text-fg-muted">{t("software.busy")}</p> : null}
      <SegmentedControl
        className="self-start"
        label={t("software.filter.label")}
        value={show}
        onValueChange={setShow}
        options={[
          { value: "all", label: t("software.filter.all"), count: items.length },
          { value: "installed", label: t("software.filter.installed"), count: installed },
          { value: "missing", label: t("software.filter.missing"), count: items.length - installed },
        ]}
      />
      {shown.length === 0 ? (
        <EmptyState
          variant="inline"
          icon={<OBJECT_ICONS.packages />}
          title={show === "installed" ? t("software.empty.installedTitle") : t("software.empty.missingTitle")}
          description={show === "installed" ? t("software.empty.installedDescription") : t("software.empty.missingDescription")}
        />
      ) : (
        groupsOf(shown).map(({ group, items: rows }) => {
          const table = (
            <SoftwareTable
              caption={t("software.tableCaption", { group: t(GROUP_LABELS[group]) })}
              items={rows}
              rights={{
                canInstall,
                canRemove,
                busy: jobs.busy,
                onInstall: installing.install,
                onRemove: setRemoving,
              }}
            />
          );
          return (
            <Section key={group} title={t(GROUP_LABELS[group])} level={2}>
              {isBase(group, rows) ? <FoldedBase items={rows}>{table}</FoldedBase> : table}
            </Section>
          );
        })
      )}
      <div className="flex min-w-0 flex-col gap-3">
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-12 text-fg-muted">
          <span>
            {data.family === "none" ? t("software.family.none") : t.rich("software.family.packages", { manager: <Mono key="manager">{data.family}</Mono> })}
          </span>
          <span>{t.rich("software.checked", { when: <RelativeTime key="when" value={checkedAt} /> })}</span>
        </p>
      </div>
      {removing !== null ? <RemoveSoftware item={removing} onClose={() => setRemoving(null)} /> : null}
      {installing.dialog}
    </div>
  );
}

function Software() {
  const t = useT();
  const query = useQuery(softwareQuery());
  return (
    <ServerQueryState query={query} label={t("software.label")} skeleton={<SkeletonSection rows={6} />}>
      {(data) => <Content data={data} checkedAt={query.dataUpdatedAt} />}
    </ServerQueryState>
  );
}

/** The Software section of the Server area. */
export function SoftwarePage() {
  return (
    <NodeCapabilityGate capability={SOFTWARE_CAPABILITY}>
      <Software />
    </NodeCapabilityGate>
  );
}
