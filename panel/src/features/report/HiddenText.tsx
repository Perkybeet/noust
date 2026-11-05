import { useRef } from "react";

import { useNeedsScrollFocus } from "../../components/ui/scrollable";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { pieces } from "./redact";

/** The mark that stands where a secret was: a word, so it is told by more than its tone. */
function HiddenMark() {
  const t = useT();
  return (
    <mark className="rounded-chip bg-bg-sunken-deep px-1 font-sans text-12 font-semibold text-fg-muted">{t("report.send.hidden")}</mark>
  );
}

/**
 * A text as it will be written in the file, with each place where a secret was hidden marked as hidden. It is the
 * system's own words in mono (what the support report and the errors say is never rewritten), scrolling inside
 * its box when it is long, and in the tab order only then, as `SystemOutput` is.
 */
export function HiddenText({ text, label, className }: { text: string; label: string; className?: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const scrolls = useNeedsScrollFocus(ref);
  return (
    <pre
      ref={ref}
      translate="no"
      {...(scrolls ? { tabIndex: 0, role: "region", "aria-label": label } : {})}
      className={cx(
        "max-h-56 overflow-auto rounded-control bg-bg-sunken p-2.5 font-mono text-12 leading-5 whitespace-pre-wrap break-words text-fg scroll-thin",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
        className,
      )}
    >
      {pieces(text).map((piece, index) => (piece.hidden ? <HiddenMark key={index} /> : piece.text))}
    </pre>
  );
}

/** The word that names what a mark means, for the sentence that explains it. */
export function HiddenWord() {
  return <HiddenMark />;
}
