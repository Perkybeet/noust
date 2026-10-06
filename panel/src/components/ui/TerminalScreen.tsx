import { ArrowDownToLine } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "./Button";
import { TerminalController } from "./terminal/controller";
import type { ScreenSource } from "./terminal/controller";

export type { ScreenSource } from "./terminal/controller";

export interface TerminalScreenProps {
  /** The emulator and when it changes. */
  source: ScreenSource;
  /** Bytes to send to the program: keys, pastes, replies to its queries. */
  onInput: (data: string) => void;
  /** The grid changed size; the program has to be told. */
  onResize: (cols: number, rows: number) => void;
  /** The accessible name of the terminal: what it is a terminal of. */
  label: string;
  /** The session is over: the screen stays, nothing typed is sent. */
  disabled?: boolean;
  /** Show the screen as text instead of drawing it, for people who cannot read a canvas. */
  textView?: boolean;
  /** Read new output aloud as it arrives. Off by default: a full-screen program redraws constantly. */
  announce?: boolean;
  /** Takes the keyboard when it appears. */
  focusOnMount?: boolean;
  /** Text was copied from the screen, or the browser refused. */
  onCopied?: (text: string, ok: boolean) => void;
  className?: string;
}

/** How often the text view and the announcements follow the screen. */
const TEXT_INTERVAL_MS = 250;
/** How many announced lines the live region keeps. */
const ANNOUNCED_LINES = 40;

interface Announced {
  id: number;
  text: string;
}

/** The lines of `current` that were not in `previous`, in order: what is new on a screen that redraws. */
function newLines(previous: readonly string[], current: readonly string[]): string[] {
  const seen = new Map<string, number>();
  for (const line of previous) seen.set(line, (seen.get(line) ?? 0) + 1);
  const fresh: string[] = [];
  for (const line of current) {
    if (line.trim() === "") continue;
    const count = seen.get(line) ?? 0;
    if (count > 0) seen.set(line, count - 1);
    else fresh.push(line);
  }
  return fresh;
}

/**
 * A terminal, drawn on a canvas.
 *
 * Not xterm.js, which injects a `<style>` element the console's content security policy forbids:
 * the emulator (`lib/terminal`) and the drawing (`./terminal`) are the console's own. What
 * a keyboard, a screen reader and a clipboard need is here and nowhere else:
 *
 * - **Keyboard and paste** land on a hidden text area that holds the focus, so an input method, a
 *   virtual keyboard and the browser's paste all work. Ctrl+C is an interrupt; copy is
 *   Ctrl+Shift+C and paste Ctrl+Shift+V.
 * - **Reading it** without sight: `textView` replaces the canvas with the same screen as text
 *   (a region the keyboard can scroll), and `announce` reads new lines aloud as they appear.
 * - **The newest output** is one press away once the view has been scrolled back.
 * - **Size**: it fills its box, and tells the program when the box changes.
 *
 * Its colours are tokens, read at the box, and it repaints when the theme changes.
 */
export function TerminalScreen({ source, onInput, onResize, label, disabled = false, textView = false, announce = false, focusOnMount = false, onCopied, className }: TerminalScreenProps) {
  const t = useT();
  const hintId = useId();
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const controller = useRef<TerminalController | null>(null);
  // The latest callbacks, read by the controller without rebuilding it.
  const callbacks = useRef({ onInput, onResize, onCopied });
  const [scrolledBack, setScrolledBack] = useState(false);
  const [focused, setFocused] = useState(false);
  const [text, setText] = useState("");
  const [announced, setAnnounced] = useState<Announced[]>([]);

  useEffect(() => {
    callbacks.current = { onInput, onResize, onCopied };
  });

  useEffect(() => {
    const box = container.current;
    const surface = canvas.current;
    const keyboard = input.current;
    if (box === null || surface === null || keyboard === null) return;
    const made = new TerminalController(
      { container: box, canvas: surface, input: keyboard },
      source,
      {
        onInput: (data) => {
          callbacks.current.onInput(data);
        },
        onResize: (cols, rows) => {
          callbacks.current.onResize(cols, rows);
        },
        onScrolled: (offset) => {
          setScrolledBack(offset > 0);
        },
        onFocusChange: setFocused,
        onCopied: (copied, ok) => {
          callbacks.current.onCopied?.(copied, ok);
        },
      },
    );
    controller.current = made;
    made.start();
    if (focusOnMount) made.focus();
    return () => {
      made.stop();
      controller.current = null;
    };
  }, [source, focusOnMount]);

  useEffect(() => {
    controller.current?.setDisabled(disabled);
  }, [disabled]);

  // The text view and the announcements follow the screen at a calm pace, never per byte.
  useEffect(() => {
    if (!textView && !announce) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let before = source.terminal.screen.textRows();
    let serial = 0;
    const refresh = (): void => {
      timer = null;
      const screen = source.terminal.screen;
      if (textView) setText(screen.text());
      if (announce) {
        const now = screen.textRows();
        const fresh = newLines(before, now);
        before = now;
        if (fresh.length > 0) {
          setAnnounced((current) => [...current, ...fresh.map((line) => ({ id: serial++, text: line }))].slice(-ANNOUNCED_LINES));
        }
      }
    };
    refresh();
    const stop = source.subscribe(() => {
      timer ??= setTimeout(refresh, TEXT_INTERVAL_MS);
    });
    return () => {
      stop();
      if (timer !== null) clearTimeout(timer);
    };
  }, [source, textView, announce]);

  return (
    <div className={cx("relative", className)}>
      <div
        ref={container}
        data-terminal-focused={focused ? "true" : "false"}
        className="relative h-full w-full overflow-hidden rounded-card border border-border bg-bg-sunken font-mono focus-within:outline focus-within:outline-2 focus-within:outline-focus focus-within:-outline-offset-2"
      >
        <canvas ref={canvas} aria-hidden="true" className={cx("block", textView && "invisible")} />
        <textarea
          ref={input}
          aria-label={label}
          aria-describedby={hintId}
          autoCapitalize="off"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          className="absolute left-0 top-0 size-px resize-none opacity-0"
          rows={1}
        />
        {textView ? (
          <pre
            tabIndex={0}
            role="region"
            aria-label={t("terminal.screen.textView")}
            className="absolute inset-0 overflow-auto bg-bg-sunken p-2 text-12 text-fg -outline-offset-2"
          >
            {text}
          </pre>
        ) : null}
      </div>
      <p id={hintId} className="sr-only">
        {t("terminal.screen.hint")}
      </p>
      <div role="log" aria-live={announce ? "polite" : "off"} aria-label={t("terminal.screen.announcements")} className="sr-only">
        {announced.map((line) => (
          <div key={line.id}>{line.text}</div>
        ))}
      </div>
      {scrolledBack ? (
        <Button
          variant="secondary"
          size="sm"
          icon={<ArrowDownToLine aria-hidden="true" />}
          className="absolute bottom-3 right-3"
          onClick={() => {
            controller.current?.scrollToBottom();
            controller.current?.focus();
          }}
        >
          {t("terminal.screen.jump")}
        </Button>
      ) : null}
    </div>
  );
}
