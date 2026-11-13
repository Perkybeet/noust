import { ExternalLink as ExternalIcon } from "lucide-react";
import { Fragment } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { CopyValue } from "../ui/CopyValue";
import { Mono } from "../ui/Mono";
import { textLinkClassName } from "../ui/TextLink";
import { readReply } from "./replyText";
import type { Inline } from "./replyText";

function Marks({ inline }: { inline: readonly Inline[] }) {
  const t = useT();
  return (
    <>
      {inline.map((piece, index) => {
        const key = `${piece.k}${String(index)}`;
        switch (piece.k) {
          case "text":
            return <Fragment key={key}>{piece.text}</Fragment>;
          case "strong":
            return (
              <strong key={key} className="font-medium">
                {piece.text}
              </strong>
            );
          case "code":
            return <Mono key={key}>{piece.text}</Mono>;
          case "image":
            // An image would be a request to somebody else's server made by reading: it is never drawn.
            return (
              <span key={key} className="text-fg-muted">
                {piece.alt === "" ? t("assistant.message.imageOmittedNoAlt") : t("assistant.message.imageOmitted", { alt: piece.alt })}
              </span>
            );
          case "dead":
            return (
              <span key={key}>
                {piece.label}{" "}
                <Mono tone="muted" className="text-12 break-all">
                  {piece.destination}
                </Mono>{" "}
                <span className="sr-only">{t("assistant.message.notALink")}</span>
              </span>
            );
          case "link":
            // Where it goes is drawn beside it whatever it says it is: a label can say any address.
            return (
              <span key={key} className="inline-flex max-w-full flex-wrap items-baseline gap-x-1">
                <a href={piece.href} target="_blank" rel="noopener noreferrer nofollow" className={cx(textLinkClassName("inline"), "inline-flex items-baseline gap-1")}>
                  {piece.label}
                  <ExternalIcon aria-hidden="true" className="size-icon-xs shrink-0 self-center" />
                  <span className="sr-only">{t("assistant.message.opensInNewTab")}</span>
                </a>
                <Mono tone="muted" className="text-12">
                  {`(${piece.host})`}
                </Mono>
                <span className="sr-only">{t("assistant.message.destination", { host: piece.host })}</span>
              </span>
            );
        }
      })}
    </>
  );
}

export interface MessageTextProps {
  /** The text of the answer as the model wrote it. */
  text: string;
  className?: string;
}

/**
 * The text of an answer, drawn without HTML. A reply is text that comes from outside, so it is read into paragraphs, lists,
 * code, bold and links by `readReply` and drawn as elements; nothing goes through `innerHTML` (the console's content
 * security policy and Trusted Types forbid it), there is no image, a tag stays as the characters it is, and a link shows where
 * it goes and opens only when pressed.
 */
export function MessageText({ text, className }: MessageTextProps) {
  const t = useT();
  const blocks = readReply(text);
  return (
    <div className={cx("flex min-w-0 flex-col gap-3 text-13 leading-5 text-fg", className)}>
      {blocks.map((block, index) => {
        const key = `${block.k}${String(index)}`;
        switch (block.k) {
          case "p":
            return (
              <p key={key} className="text-pretty">
                <Marks inline={block.inline} />
              </p>
            );
          case "list": {
            const List = block.ordered ? "ol" : "ul";
            return (
              <List key={key} className={cx("m-0 flex flex-col gap-1.5 pl-5", block.ordered ? "list-decimal" : "list-disc")}>
                {block.items.map((item, n) => (
                  <li key={`${key}.${String(n)}`} className="text-pretty">
                    <Marks inline={item} />
                  </li>
                ))}
              </List>
            );
          }
          case "code":
            return (
              <div key={key} className="flex flex-col gap-1">
                <p className="text-12 text-fg-muted">{t("assistant.message.codeLabel")}</p>
                <CopyValue value={block.text} label={t("assistant.message.copyCode")} />
              </div>
            );
        }
      })}
    </div>
  );
}

export interface LiteralLinesProps {
  /** Whose words they are, in a line: the deployment's log, a diagnosis. */
  caption: ReactNode;
  lines: readonly { n: number; text: string }[];
}

/**
 * The system's own words an answer leans on, verbatim, with their line numbers, above the explanation (design principle 5). They
 * are drawn from Noust's copy of the text, never from the model's words, so an answer cannot put words in the system's mouth.
 */
export function LiteralLines({ caption, lines }: LiteralLinesProps) {
  const t = useT();
  return (
    <figure className="m-0 flex flex-col gap-1">
      <figcaption className="text-12 text-fg-muted">{caption}</figcaption>
      <pre
        translate="no"
        aria-label={t("assistant.message.literalLines")}
        className="elev-carved-fine m-0 overflow-x-auto rounded-control bg-bg-sunken px-3 py-2 text-12 leading-5 whitespace-pre-wrap break-words text-fg scroll-thin"
      >
        {lines.map((line) => `${String(line.n).padStart(3, " ")}  ${line.text}`).join("\n")}
      </pre>
    </figure>
  );
}
