/**
 * What the five lists of the zone share: the page that holds the search and the owner filter in its address, the filter that
 * narrows by whose a thing is, and the empty state of a list that nothing matched. Each list is the same table of always, so a
 * filtered list can be shared and every one of them says whose each thing is.
 */

import type { ReactNode } from "react";

import type { DockerOwner } from "../../api/queries/docker";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Select } from "../../components/ui/Select";
import type { FilterBarSearch } from "../../components/page/FilterBar";
import { useT } from "../../i18n";
import { OWNER_FILTERS } from "./search";
import type { DockerSearch, OwnerFilter } from "./search";

/** What a list page is given by its route: what the address holds, and how to change it. */
export interface DockerListProps {
  search: DockerSearch;
  /** `replace` for typing, so the history does not gain an entry per keystroke. */
  onSearchChange: (search: DockerSearch, options?: { replace?: boolean }) => void;
}

const ANY = "all";

/** Whether an owner is of the kind a filter asks for: a kind this console does not know is nobody's. */
export function ownerMatches(owner: DockerOwner, wanted: OwnerFilter | undefined): boolean {
  if (wanted === undefined) return true;
  const kind = owner.kind === "app" || owner.kind === "database" || owner.kind === "noust" ? owner.kind : "nobody";
  return kind === wanted;
}

/** Whether a name or an image contains what was typed, without caring about case. */
export function textMatches(q: string | undefined, ...fields: readonly (string | null | undefined)[]): boolean {
  if (q === undefined || q === "") return true;
  const wanted = q.toLowerCase();
  return fields.some((field) => (field ?? "").toLowerCase().includes(wanted));
}

/** The box and the owner filter every list has, as the props of the `FilterBar` and the select that goes in it. */
export function useListFilters(props: DockerListProps, label: string): { searchBox: FilterBarSearch; owner: ReactNode } {
  const t = useT();
  const { search, onSearchChange } = props;
  const options = [{ value: ANY, label: t("docker.filters.allOwners") }, ...OWNER_FILTERS.map((kind) => ({ value: kind, label: t(`docker.filters.owner.${kind}`) }))];
  return {
    searchBox: {
      value: search.q ?? "",
      onChange: (value) => onSearchChange({ ...search, ...(value !== "" ? { q: value } : { q: undefined }) }, { replace: true }),
      label,
      placeholder: t("docker.filters.search"),
    },
    owner: (
      <Select
        aria-label={t("docker.filters.ownerLabel")}
        options={options}
        value={search.owner ?? ANY}
        onValueChange={(value) => onSearchChange({ ...search, owner: value === ANY ? undefined : (value as OwnerFilter) })}
      />
    ),
  };
}

/** What a filter that matched nothing shows: one line and the way to clear it. */
export function NothingMatched({ onClear }: { onClear: () => void }) {
  const t = useT();
  return (
    <EmptyState
      variant="inline"
      title={t("docker.filters.noMatches")}
      action={
        <Button size="sm" variant="ghost" onClick={onClear}>
          {t("common.words.clearFilters")}
        </Button>
      }
    />
  );
}
