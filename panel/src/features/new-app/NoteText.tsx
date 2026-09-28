import { ExternalLink } from "../../components/ui/ExternalLink";
import { noteParts } from "./recipe";

/**
 * A sentence the server wrote (a recipe's note), with every address in it a link: plain text
 * and anchors built as elements, never an HTML string.
 */
export function NoteText({ note }: { note: string }) {
  return (
    <>
      {noteParts(note).map((part, index) =>
        part.kind === "link" ? (
          <ExternalLink key={index} href={part.href} inline unlinked="text" />
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}
