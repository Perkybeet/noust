import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { ICONS } from "../ui/icons";
import { StatusPill } from "../ui/StatusPill";

/**
 * Where an image stands against what its registry has: `current`, a `newer` version of the same major, a `major` one (which is
 * a decision, never an update by itself), one an application `keeps` to go back to, one `builtHere` (no registry to ask), an
 * `untagged` one, one nobody has `notLooked` at yet (looking is a button, never a page), and one that `unknown`ly could not be asked.
 */
export type VersionKind = "current" | "newer" | "major" | "keeps" | "builtHere" | "untagged" | "notLooked" | "unknown";

export interface VersionMarkProps {
  kind: VersionKind;
  className?: string;
}

/**
 * The mark of an image's version, the same on the summary, the images list and an image's dialog. Only what asks for a look
 * wears a state (a new version waits, a new major is a warning: a glyph and a word, never colour alone); an image that is well
 * is its word in stone, because a list of twenty that are well says less than the one that is not.
 */
export function VersionMark({ kind, className }: VersionMarkProps) {
  const t = useT();
  const own = className !== undefined ? { className } : {};
  if (kind === "newer") return <StatusPill state="queued" label={t("docker.version.newer")} appearance="inline" size="sm" {...own} />;
  if (kind === "major") return <StatusPill state="warning" label={t("docker.version.major")} appearance="inline" size="sm" {...own} />;
  if (kind === "keeps") {
    return (
      <span className={cx("inline-flex items-center gap-1.5 text-13 text-fg", className)}>
        <ICONS.locked aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
        {t("docker.version.keeps")}
      </span>
    );
  }
  const word = {
    current: t("docker.version.current"),
    builtHere: t("docker.version.builtHere"),
    untagged: t("docker.version.untagged"),
    notLooked: t("docker.version.notLooked"),
    unknown: t("docker.version.unknown"),
  }[kind];
  return <span className={cx("text-13", kind === "current" ? "text-fg" : "text-fg-muted", className)}>{word}</span>;
}
