import { useRef } from "react";

import { Card } from "../../components/ui/Card";
import { useNeedsScrollFocus } from "../../components/ui/scrollable";

/**
 * The usage notice as its operator wrote it: line breaks kept, nothing interpreted. A long one
 * scrolls in its box, which then takes focus so a keyboard can read it to the end.
 */
export function NoticeText({ text, label }: { text: string; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const focusable = useNeedsScrollFocus(ref);
  return (
    <Card padding="md">
      <div
        ref={ref}
        {...(focusable ? { tabIndex: 0, role: "region", "aria-label": label } : {})}
        className="max-h-80 overflow-y-auto text-14 whitespace-pre-wrap text-fg -outline-offset-2"
      >
        {text}
      </div>
    </Card>
  );
}
