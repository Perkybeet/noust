import type { ReactNode } from "react";

import { Mono } from "../../components/ui/Mono";
import { ServerText } from "../../components/ui/ServerText";
import type { CodedText, SentencePart } from "../../components/ui/ServerText";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { sentenceKey } from "../../lib/serverSentence";

/** `noust setup init` in a sentence becomes code; the rest stays text. */
function inline(text: string): ReactNode[] {
  return text.split("`").map((part, index) => (index % 2 === 1 ? <Mono key={index}>{part}</Mono> : part));
}

/**
 * What the backend suggests doing, as it wrote it. Its paragraphs are separated by a blank
 * line, and a paragraph of several lines is a file to write (the smallest compose.yaml that
 * builds a Dockerfile), so it is shown as one, verbatim, in mono: indentation is meaning there.
 * A suggestion that is a sentence of the server's catalog (`coded`, and `part="hint"` when it is
 * the fix of one) is said in this console's language, its command set as it is, in mono.
 */
export function Suggestion({ text, coded, part = "text", className }: { text: string; coded?: CodedText | null | undefined; part?: SentencePart; className?: string }) {
  const t = useT();
  if (coded && sentenceKey(coded, part) !== null) {
    return (
      <div className={cx("flex min-w-0 flex-col gap-2 text-13 text-pretty text-fg-muted", className)}>
        <p>
          <ServerText coded={coded} text={text} part={part} />
        </p>
      </div>
    );
  }
  const paragraphs = text.split(/\n\s*\n/).filter((paragraph) => paragraph.trim() !== "");
  return (
    <div className={cx("flex min-w-0 flex-col gap-2 text-13 text-pretty text-fg-muted", className)}>
      {paragraphs.map((paragraph, index) =>
        paragraph.includes("\n") ? (
          <SystemOutput key={index} label={t("newApp.source.suggestedFile")} className="rounded-control border border-border bg-bg-sunken px-3 py-2">
            {paragraph}
          </SystemOutput>
        ) : (
          <p key={index}>{inline(paragraph)}</p>
        ),
      )}
    </div>
  );
}
