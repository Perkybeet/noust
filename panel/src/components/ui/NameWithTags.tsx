import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Badge } from "./Badge";
import { Mono } from "./Mono";
import { Popover } from "./Popover";

export interface NameWithTagsProps {
  /** What the row is called. Left out where the tags are a column of their own (a server's labels). */
  name?: ReactNode;
  /** The name is a system value (a database, a unit, a repository): it is set in mono and is not translated. */
  mono?: boolean;
  /** The tags are system values (`region=eu`), set in mono. */
  monoTags?: boolean;
  /** Every tag, in order. The first `shown` follow the name; the rest wait behind "+N". */
  tags: readonly string[];
  /** How many tags follow the name before "+N": two by default, one where the name is long. */
  shown?: number;
  /** Names the popover of the tags: "Tags of tienda_prod". Already translated; defaults to the plain count. */
  title?: string;
  /**
   * Draws a tag as something other than its text: a link to the thing it names (an application's tag opens the
   * application). The pebble around it, and the "+N", are the same; the accessible name of the "+N" lists the words.
   */
  renderTag?: (tag: string) => ReactNode;
  /**
   * The line sits inside a control of its own (the first cell of a table whose rows open is the row's button): the "+N"
   * is then text, with the tags it stands for in its title and for a screen reader, since a button cannot hold a button.
   */
  inControl?: boolean;
  className?: string;
}

/** The "+N": 24px by 24px at least, since a smaller target fails WCAG 2.2 AA (2.5.8); the negative margin keeps the line the 20px of a pebble, so a row does not grow by it. */
const MORE = "-my-0.5 inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded-chip px-1.5 text-12 leading-none font-semibold text-accent-fg";

/**
 * A name and its tags on one line of a table: the first tags as pebbles after the name, the rest behind "+N" in a
 * popover. The row keeps the height every row has, whatever its tags (owner item 122: tags stacked under the name grew
 * the row and touched its edge, and a cell that grew pushed the one beside it). Tags are never stacked under a name.
 * The name gives way first: it is cut at the end of its line, with the whole of it as its title.
 */
export function NameWithTags({ name, mono = false, monoTags = false, tags, shown = 2, title, renderTag, inControl = false, className }: NameWithTagsProps) {
  const t = useT();
  const first = tags.slice(0, shown);
  const rest = tags.slice(shown);
  const moreWords = t("common.tags.more", { count: rest.length, names: rest.join(", ") });
  return (
    <span data-slot="name-with-tags" className={cx("flex min-w-0 items-center gap-2", className)}>
      {name !== undefined ? (
        <span translate="no" {...(typeof name === "string" ? { title: name } : {})} className="min-w-0 shrink-[50] truncate font-medium text-fg">
          {mono ? <Mono>{name}</Mono> : name}
        </span>
      ) : null}
      {first.length > 0 ? (
        // The name takes nearly all of what is short and the pebbles only what is left, so the "+N" is never the part cut off at the edge of a card.
        <span className="flex min-w-0 shrink items-center gap-1">
          {first.map((tag) => (
            <Badge key={tag} mono={monoTags} className="max-w-36 truncate font-normal">
              <span translate="no" className="truncate">
                {renderTag === undefined ? tag : renderTag(tag)}
              </span>
            </Badge>
          ))}
        </span>
      ) : null}
      {rest.length > 0 && inControl ? (
        <span title={rest.join(", ")} className={MORE}>
          +{rest.length}
          <span className="sr-only">{moreWords}</span>
        </span>
      ) : rest.length > 0 ? (
        <Popover
          title={title ?? t("common.tags.title", { count: tags.length })}
          align="start"
          className="w-64"
          trigger={
            <button type="button" aria-label={moreWords} className={cx(MORE, "cursor-pointer hover:bg-accent-soft")}>
              +{rest.length}
            </button>
          }
        >
          <ul className="flex flex-wrap gap-1">
            {tags.map((tag) => (
              <li key={tag}>
                <Badge mono={monoTags} className="font-normal">
                  <span translate="no">{renderTag === undefined ? tag : renderTag(tag)}</span>
                </Badge>
              </li>
            ))}
          </ul>
        </Popover>
      ) : null}
    </span>
  );
}
