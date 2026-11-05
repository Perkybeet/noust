import type { ReactNode } from "react";

import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { HelpPopover } from "../../../components/ui/HelpPopover";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatDate, parseTimestamp } from "../../../lib/format";
import { DependentChips } from "./Dependents";
import type { Dependent, SoftwareItem } from "./queries";
import { componentName, dependentsSentence, neverReason, purposeOf, shortVersion } from "./words";

/** What the page may do to a row: what the account is allowed, and whether another task is changing the software. */
export interface RowRights {
  canInstall: (id: string) => boolean;
  canRemove: (id: string) => boolean;
  /** Another job holds the package manager: the buttons wait, as the server would refuse them (409 host_busy). */
  busy: boolean;
  onInstall: (item: SoftwareItem) => void;
  onRemove: (item: SoftwareItem) => void;
}

function dependentsOf(item: SoftwareItem): readonly Dependent[] {
  return item.dependents ?? [];
}

function Component({ item }: { item: SoftwareItem }) {
  const t = useT();
  const { coded, text } = purposeOf(item);
  return (
    // The cell is a line of the table and this is prose: it wraps, and the table's `nowrap` stays for the others.
    <span className="flex items-start gap-2.5 whitespace-normal sm:min-w-56">
      <OBJECT_ICONS.packages aria-hidden="true" className={cx("mt-0.5 size-icon-md shrink-0", item.installed ? "text-fg-muted" : "text-fg-faint")} />
      <span className="min-w-0 max-w-xl">
        <span className="block font-medium text-fg">{componentName(t, item.id)}</span>
        <span className="block text-12 text-pretty text-fg-muted">
          <ServerText coded={coded} text={text} />
        </span>
      </span>
    </span>
  );
}

/** The version and, under it, who put the piece there: one column, so the table keeps its last one on a 1440px screen. */
function VersionCell({ item }: { item: SoftwareItem }) {
  const t = useT();
  if (!item.installed) return <StatusPill state="stopped" label={t("software.state.notInstalled")} appearance="inline" size="sm" />;
  const version = item.version != null && item.version !== "" ? item.version : null;
  const when = parseTimestamp(item.installed_at);
  const date = when === null ? null : formatDate(when, { year: false });
  const who = item.installed_by ?? null;
  const line = date !== null && who !== null ? t("software.origin.when", { date, who }) : date !== null ? t("software.origin.whenOnly", { date }) : null;
  const byNoust = item.origin === "noust";
  return (
    <span className="flex flex-col py-0.5">
      {version !== null ? <Mono title={version}>{shortVersion(version)}</Mono> : <span className="text-fg-muted">{t("software.state.installed")}</span>}
      <span className={cx("text-12", byNoust ? "text-fg" : "text-fg-muted")}>{byNoust ? t("software.origin.noust") : t("software.origin.unknown")}</span>
      {byNoust && line !== null ? <span className="text-12 text-fg-muted">{line}</span> : null}
    </span>
  );
}

/** The "?" that says why a piece can or cannot be removed, with what depends on it by name. */
function RemovableHelp({ t, item, said }: { t: T; item: SoftwareItem; said: ReactNode }) {
  return <HelpPopover label={t("software.removable.helpLabel", { name: componentName(t, item.id) })}>{said}</HelpPopover>;
}

function RemovableCell({ item }: { item: SoftwareItem }) {
  const t = useT();
  if (item.severity === "never") {
    return (
      <span className="inline-flex items-center gap-1 text-fg-muted">
        <ICONS.locked aria-hidden="true" className="size-icon-sm text-fg-faint" />
        {t("software.removable.never")}
        <RemovableHelp t={t} item={item} said={neverReason(t.locale, item.id) ?? t("software.removable.helpNever")} />
      </span>
    );
  }
  if (!item.installed) return <span className="text-fg-faint">{t("software.removable.notApplicable")}</span>;
  const dependents = dependentsOf(item);
  if (item.severity === "removable") {
    return (
      <span className="inline-flex items-center gap-1 text-fg">
        {t("common.words.yes")}
        <RemovableHelp t={t} item={item} said={t("software.removable.helpYes")} />
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-fg">
      {t("software.removable.whenUnused")}
      <RemovableHelp
        t={t}
        item={item}
        said={dependents.length > 0 ? t("software.removable.helpUsed", { things: dependentsSentence(t, dependents) }) : t("software.removable.helpFree")}
      />
    </span>
  );
}

function columns(t: T): Column<SoftwareItem>[] {
  return [
    { id: "component", header: t("software.columns.component"), stacked: () => true, cell: (item) => <Component item={item} /> },
    { id: "version", header: t("software.columns.version"), width: "w-44", stacked: () => true, card: "status", cell: (item) => <VersionCell item={item} /> },
    {
      id: "dependents",
      header: t("software.columns.dependents"),
      width: "w-36",
      hideBelow: "md",
      card: "meta",
      cell: (item) => <DependentChips dependents={dependentsOf(item)} installed={item.installed} />,
    },
    { id: "removable", header: t("software.columns.removable"), width: "w-52", card: "meta", cell: (item) => <RemovableCell item={item} /> },
  ];
}

/** Both buttons are as wide, so the columns of one group's table line up with the next group's. */
const ACTION = "min-w-24";

/** The one button a row has, if the account may press it: Remove for what is there, Install for what is not. */
function RowAction({ item, rights }: { item: SoftwareItem; rights: RowRights }) {
  const t = useT();
  const name = componentName(t, item.id);
  if (item.installed && item.severity !== "never" && rights.canRemove(item.id)) {
    return (
      <Button size="sm" className={ACTION} icon={<OBJECT_ICONS.delete aria-hidden="true" />} disabled={rights.busy} aria-label={t("common.words.removeNamed", { name })} onClick={() => rights.onRemove(item)}>
        {t("software.actions.remove")}
      </Button>
    );
  }
  if (!item.installed && rights.canInstall(item.id)) {
    if (!item.installable) return <span className="text-12 text-fg-muted">{t("software.actions.notInstallable")}</span>;
    return (
      <Button size="sm" className={ACTION} icon={<VERB_ICONS.install aria-hidden="true" />} disabled={rights.busy} aria-label={t("software.actions.installNamed", { name })} onClick={() => rights.onInstall(item)}>
        {t("software.actions.install")}
      </Button>
    );
  }
  // Nothing to press, but the room of a button stays, so the columns of this group's table line up with the others'.
  return <span aria-hidden="true" className={cx("inline-block", ACTION)} />;
}

/** One group of the software, as a table: the pieces, what each is for and who put it there, and what can be done to it. */
export function SoftwareTable({ caption, items, rights }: { caption: string; items: readonly SoftwareItem[]; rights: RowRights }) {
  const t = useT();
  return (
    <DataTable
      caption={caption}
      columns={columns(t)}
      rows={items}
      getRowId={(item) => item.id}
      rowActions={(item) => <RowAction item={item} rights={rights} />}
    />
  );
}
