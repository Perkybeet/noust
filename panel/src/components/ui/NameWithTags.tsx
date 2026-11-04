import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Badge } from "./Badge";
import { Mono } from "./Mono";
import { Popover } from "./Popover";

export interface NameWithTagsProps {
  /** What the row is called. */
  name: ReactNode;
  /** The name is a system value (a database, a unit, a repository): it is set in mono and is not translated. */
  mono?: boolean;
  /** Every tag, in order. The first `shown` follow the name; the rest wait behind "+N". */
  tags: readonly string[];
  /** How many tags follow the name before "+N": two by default, one where the name is long. */
  shown?: number;
  /** Names the popover of the tags: "Tags of tienda_prod". Already translated; defaults to the plain count. */
  title?: string;
  className?: string;
}

/**
 * A name and its tags on one line of a table: the first tags as pebbles after the name, the rest behind "+N" in a
 * popover. The row keeps the height every row has, whatever its tags (owner item 122: tags stacked under the name grew
 * the row and touched its edge, and a cell that grew pushed the one beside it). Tags are never stacked under a name.
 * The name gives way first: it is cut at the end of its line, with the whole of it as its title.
 */
export function NameWithTags({ name, mono = false, tags, shown = 2, title, className }: NameWithTagsProps) {
  const t = useT();
  const first = tags.slice(0, shown);
  const rest = tags.slice(shown);
  return (
    <span data-slot="name-with-tags" className={cx("flex min-w-0 items-center gap-2", className)}>
      <span translate="no" {...(typeof name === "string" ? { title: name } : {})} className="min-w-0 shrink truncate font-medium text-fg">
        {mono ? <Mono>{name}</Mono> : name}
      </span>
      {first.length > 0 ? (
        <span className="flex shrink-0 items-center gap-1">
          {first.map((tag) => (
            <Badge key={tag} className="max-w-36 truncate font-normal">
              <span translate="no" className="truncate">
                {tag}
              </span>
            </Badge>
          ))}
        </span>
      ) : null}
      {rest.length > 0 ? (
        <Popover
          title={title ?? t("common.tags.title", { count: tags.length })}
          align="start"
          className="w-64"
          trigger={
            <button
              type="button"
              aria-label={t("common.tags.more", { count: rest.length, names: rest.join(", ") })}
              className="h-5 shrink-0 cursor-pointer rounded-chip px-1.5 text-12 leading-none font-semibold text-accent-fg hover:bg-accent-soft"
            >
              +{rest.length}
            </button>
          }
        >
          <ul className="flex flex-wrap gap-1">
            {tags.map((tag) => (
              <li key={tag}>
                <Badge className="font-normal">
                  <span translate="no">{tag}</span>
                </Badge>
              </li>
            ))}
          </ul>
        </Popover>
      ) : null}
    </span>
  );
}
