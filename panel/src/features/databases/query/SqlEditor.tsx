import { useImperativeHandle, useMemo, useRef } from "react";
import type { KeyboardEvent, Ref } from "react";

import { CONTROL_FRAME } from "../../../components/ui/Input";
import { cx } from "../../../lib/cx";
import { tokenize } from "./highlight";
import type { TokenKind } from "./highlight";

/**
 * How each kind of token is drawn. Colour means state or action in this console, so a statement
 * is told apart by weight and grey alone: keywords stronger, strings and punctuation softer,
 * comments faintest. The mono face keeps one advance at every weight, so the drawn text and the
 * text being typed never drift apart.
 */
const TOKEN_CLASS: Readonly<Record<TokenKind, string>> = {
  keyword: "font-medium text-fg",
  string: "text-fg-muted",
  identifier: "text-fg",
  number: "text-fg",
  comment: "text-fg-faint",
  punctuation: "text-fg-muted",
  text: "text-fg",
};

export interface SqlEditorHandle {
  focus: () => void;
  /** The selected text, when some is selected: running a selection runs only it. */
  selection: () => string;
}

export interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  /** Ctrl or Cmd with Enter. */
  onRun: () => void;
  label: string;
  describedBy?: string;
  className?: string;
  ref?: Ref<SqlEditorHandle>;
}

/**
 * The SQL console's editor: a plain textarea, so the browser's own editing, undo and
 * accessibility stay as they are, over a copy of its text drawn token by token with React
 * elements (never an HTML string, never an injected style sheet: the console's CSP allows
 * neither), with line numbers that follow the text's scroll. Tab keeps its usual meaning.
 */
export function SqlEditor({ value, onChange, onRun, label, describedBy, className, ref }: SqlEditorProps) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const layer = useRef<HTMLPreElement>(null);
  const gutter = useRef<HTMLDivElement>(null);
  const tokens = useMemo(() => tokenize(value), [value]);
  const lines = value.split("\n").length;

  useImperativeHandle(ref, () => ({
    focus: () => textarea.current?.focus(),
    selection: () => {
      const element = textarea.current;
      if (element === null) return "";
      return element.value.slice(element.selectionStart, element.selectionEnd);
    },
  }));

  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      onRun();
    }
  };

  const text = "whitespace-pre px-3 py-2 text-13 leading-5";
  return (
    <div className={cx("flex min-h-40 min-w-0 overflow-hidden", CONTROL_FRAME, className)}>
      <div
        ref={gutter}
        aria-hidden="true"
        // design-exception: mono-utility the numbers share the text's grid, line for line
        className="mono shrink-0 overflow-hidden border-r border-border bg-bg-sunken py-2 text-right text-12 leading-5 text-fg-faint select-none"
      >
        {Array.from({ length: lines }, (_, index) => (
          <div key={index} className="px-2.5">
            {index + 1}
          </div>
        ))}
      </div>
      <div className="relative min-w-0 flex-1">
        <pre
          ref={layer}
          aria-hidden="true"
          // design-exception: mono-utility the drawn copy must match the textarea glyph for glyph
          className={cx("mono pointer-events-none absolute inset-0 m-0 overflow-hidden", text)}
        >
          {tokens.map((token, index) => (
            <span key={index} className={TOKEN_CLASS[token.kind]}>
              {token.text}
            </span>
          ))}
          {/* A final newline has a line of its own in a textarea; the copy needs one too. */}
          {"\n"}
        </pre>
        <textarea
          ref={textarea}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={keyDown}
          onScroll={(event) => {
            const { scrollTop, scrollLeft } = event.currentTarget;
            if (layer.current) {
              layer.current.scrollTop = scrollTop;
              layer.current.scrollLeft = scrollLeft;
            }
            if (gutter.current) gutter.current.scrollTop = scrollTop;
          }}
          aria-label={label}
          {...(describedBy !== undefined ? { "aria-describedby": describedBy } : {})}
          wrap="off"
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          autoCorrect="off"
          translate="no"
          // design-exception: mono-utility the whole surface is a statement, not a value in running text
          className={cx("mono relative block h-full w-full min-w-0 resize-none overflow-auto bg-transparent text-transparent caret-fg outline-none scroll-thin", text)}
        />
      </div>
    </div>
  );
}
