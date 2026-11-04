import { useState } from "react";

import { SETTINGS_FILTER_FROM, answersFilter, groupNavItems } from "../../components/page/SettingsLayout";
import { Input } from "../../components/ui/Input";
import { Kbd } from "../../components/ui/Kbd";
import { ICONS } from "../../components/ui/icons";
import { useSettingsItems } from "../../features/settings/useSettingsItems";
import { useT } from "../../i18n";
import { ColumnHead } from "./ColumnHead";
import { ColumnList } from "./ColumnList";
import type { ListGroup } from "./ColumnList";

/**
 * Settings: whose each section is, in the groups the register gives, and from twelve sections a box that
 * narrows them by name and by the name they had before (NV-11): twenty-four sections are searched, not read.
 * On a fleet, the sections that are of the whole instance (accounts, tokens, compliance...) say so beside
 * their name, "All the fleet", instead of changing which server is chosen: Settings never moves the selector.
 */
export function SettingsColumn() {
  const t = useT();
  const { items, hasFleet } = useSettingsItems();
  const [query, setQuery] = useState("");
  const filterable = items.length >= SETTINGS_FILTER_FROM;
  const narrowing = filterable && query.trim() !== "";
  const kept = narrowing ? items.filter((item) => answersFilter(item, query)) : items;
  const wide = new Set(items.filter((item) => item.central).map((item) => item.to));
  const Search = ICONS.search;
  const groups: ListGroup[] = groupNavItems(kept).map((group) => ({
    key: group.key,
    ...(group.heading !== undefined ? { heading: group.heading } : {}),
    ...(group.scope !== undefined ? { scope: group.scope } : {}),
    items: group.items.map((item) => ({
      key: item.to,
      to: item.to,
      ...(item.search !== undefined ? { search: item.search } : {}),
      label: item.label,
      ...(item.icon !== undefined ? { icon: item.icon } : {}),
      ...(item.exact === true ? { exact: true } : {}),
      ...(hasFleet && wide.has(item.to) ? { tag: t("nav.column.fleetWide") } : {}),
    })),
  }));
  return (
    <>
      <ColumnHead title={t("nav.settings.label")} />
      {filterable ? (
        <div className="shrink-0 px-3 pb-3">
          <Input
            type="search"
            size="sm"
            aria-label={t("common.settingsLayout.filter")}
            placeholder={t("common.settingsLayout.filterPlaceholder")}
            // The page's `/` goes here when nothing else on it searches (the shell takes the first).
            data-page-search=""
            value={query}
            onValueChange={(value: string) => setQuery(value)}
            icon={<Search />}
            {...(query === "" ? { suffix: <Kbd className="pointer-coarse:hidden">/</Kbd> } : {})}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      ) : null}
      {narrowing && kept.length === 0 ? (
        <p role="status" className="shrink-0 px-5 pb-3 text-13 text-fg-muted">
          {t("common.settingsLayout.noMatches")}
        </p>
      ) : null}
      <ColumnList label={t("nav.landmarks.settingsSections")} groups={groups} />
    </>
  );
}
