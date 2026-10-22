import { Dialog as BaseDialog } from "@base-ui/react/dialog";
import { Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode, RefObject } from "react";

import { BACKDROP, MODAL_POPUP } from "../components/ui/Dialog";
import { Kbd } from "../components/ui/Kbd";
import { Mono } from "../components/ui/Mono";
import { StatusPill } from "../components/ui/StatusPill";
import type { Status } from "../components/ui/StatusPill";
import { useT } from "../i18n";
import type { PlainKey } from "../i18n";
import { cx } from "../lib/cx";
import { readRecent, rememberRecent } from "./paletteRecent";

/**
 * What the palette finds: every page, every kind of resource the console lists, and the actions
 * that are not a page. The identifiers stay English: they are compared, not shown.
 */
export type CommandGroup =
  | "Recent"
  | "Pages"
  | "Settings"
  | "Applications"
  | "Projects"
  | "Teams"
  | "Databases"
  | "Domains"
  | "Certificates"
  | "Services"
  | "Servers"
  | "Deployments"
  | "Operations"
  | "Actions";

export interface Command {
  /** Unique across the palette. */
  id: string;
  group: CommandGroup;
  label: string;
  icon?: ReactNode;
  /** Other words it should be found by. */
  keywords?: string;
  /**
   * For a command that does something to a named thing ("Restart shop.example.com"): the words
   * that say what it does. It is found only once one of them is typed, so naming the thing
   * alone finds the thing and not every verb that could be said of it.
   */
  verb?: string;
  /** What distinguishes it from the next one, in muted text after the name: a type, a time left, where it lives. */
  detail?: string;
  /**
   * Shown with nothing typed even where its group is held back: the verbs of the application
   * the operator is looking at, which are what they came to the palette for.
   */
  browse?: boolean;
  /** Keys of the shortcut that does the same, shown at the end of the row. */
  shortcut?: readonly string[];
  /** For applications: their state, shown as a pill. */
  status?: Status;
  /**
   * `navigate` commands open a page, which then takes focus; `action` commands leave the
   * operator where they were, so focus returns to what opened the palette.
   */
  kind: "navigate" | "action";
  run: () => void;
}

export interface CommandResults {
  group: CommandGroup;
  items: Command[];
}

/** What the palette says it is working in, at the left of the field: the server on a fleet, or the application on screen. */
export interface PaletteScope {
  /** Already translated: "Server", "Application", "All servers". */
  label: string;
  /** A system name, set in mono beside the label. */
  name?: string;
}

const GROUP_ORDER: readonly CommandGroup[] = [
  "Recent",
  "Pages",
  "Settings",
  "Applications",
  "Projects",
  "Teams",
  "Databases",
  "Domains",
  "Certificates",
  "Services",
  "Servers",
  "Deployments",
  "Operations",
  "Actions",
];

/** The group identifiers stay English (they are compared, not shown); this is what renders them. */
const GROUP_LABEL: Record<CommandGroup, PlainKey> = {
  Recent: "shell.commandGroups.recent",
  Pages: "shell.commandGroups.pages",
  Settings: "shell.commandGroups.settings",
  Applications: "shell.commandGroups.applications",
  Projects: "shell.commandGroups.projects",
  Teams: "shell.commandGroups.teams",
  Databases: "shell.commandGroups.databases",
  Domains: "shell.commandGroups.domains",
  Certificates: "shell.commandGroups.certificates",
  Services: "shell.commandGroups.services",
  Servers: "shell.commandGroups.servers",
  Deployments: "shell.commandGroups.deployments",
  Operations: "shell.commandGroups.operations",
  Actions: "shell.commandGroups.actions",
};

/**
 * With nothing typed, a long list of applications would push actions off screen, and a list of
 * every certificate and deployment would bury them. The applications show six; the rest of the
 * resources (databases, domains, certificates, services, deployments) appear once something is
 * typed, which is what the search box is for.
 */
const BROWSE_LIMIT: Partial<Record<CommandGroup, number>> = {
  Applications: 6,
  Projects: 0,
  Teams: 0,
  Databases: 0,
  Domains: 0,
  Certificates: 0,
  Services: 0,
  Deployments: 0,
  // What can be done to every application would bury the page's own verbs: they come once something is typed.
  Operations: 0,
};

/** A group whose labels are names of things on the machine, not words of the console. */
const NAMED_GROUPS: ReadonlySet<CommandGroup> = new Set(["Applications", "Projects", "Teams", "Databases", "Domains", "Certificates", "Services", "Servers"]);

/** How many commands the palette remembers having opened (NV-14: the three most recent). */
const RECENT_SHOWN = 3;

/** What a leading `>` or `@` asks for: only what can be done, or only another scope. */
export type PaletteMode = "all" | "actions" | "scope";

const ACTION_GROUPS: ReadonlySet<CommandGroup> = new Set(["Actions", "Operations"]);

/** The mode a query asks for and the text that is searched once the prefix is taken off. */
export function parseQuery(raw: string): { mode: PaletteMode; text: string } {
  const typed = raw.trimStart();
  if (typed.startsWith(">")) return { mode: "actions", text: typed.slice(1) };
  if (typed.startsWith("@")) return { mode: "scope", text: typed.slice(1) };
  return { mode: "all", text: raw };
}

function isSubsequence(needle: string, haystack: string): boolean {
  let at = 0;
  for (const char of haystack) {
    if (char === needle[at]) at += 1;
    if (at === needle.length) return true;
  }
  return needle.length === 0;
}

/** How many scattered letters make a match: fewer than this find nearly anything. */
const SUBSEQUENCE_FROM = 4;

/** How well a command matches what was typed; 0 is no match. */
export function scoreCommand(command: Command, query: string): number {
  const q = query.trim().toLowerCase();
  if (q === "") return 1;
  const tokens = q.split(/\s+/);
  if (command.verb !== undefined) {
    const verbWords = command.verb.toLowerCase().split(/\s+/);
    if (!tokens.some((token) => verbWords.some((word) => word.startsWith(token)))) return 0;
  }
  const label = command.label.toLowerCase();
  if (label === q) return 100;
  if (label.startsWith(q)) return 80;
  if (label.split(/[\s./-]+/).some((word) => word.startsWith(q))) return 60;
  if (label.includes(q)) return 40;
  // What is shown beside the name is found too: nobody should have to guess what is searched.
  const haystack = `${label} ${command.keywords ?? ""} ${command.detail ?? ""} ${command.group}`.toLowerCase();
  if (tokens.every((token) => haystack.includes(token))) return 20;
  // Scattered letters only mean something once there are a few of them.
  if (q.length >= SUBSEQUENCE_FROM && isSubsequence(q, label)) return 10;
  return 0;
}

/**
 * Matching commands grouped, best group first once something is typed. A leading `>` keeps only
 * what can be done (actions and the verbs on an application) and `@` only the ways to change scope;
 * with nothing typed, the commands in `recent` come first.
 */
export function filterCommands(commands: readonly Command[], query: string, recent: readonly Command[] = []): CommandResults[] {
  const { mode, text } = parseQuery(query);
  const pool = mode === "actions" ? commands.filter((command) => ACTION_GROUPS.has(command.group)) : mode === "scope" ? commands.filter((command) => command.group === "Servers") : commands;
  const browsing = text.trim() === "";
  const groups = GROUP_ORDER.map((group) => {
    const scored = (group === "Recent" ? (browsing && mode === "all" ? recent : []) : pool.filter((command) => command.group === group))
      .map((command, order) => ({ command, order, score: scoreCommand(command, text) }))
      .filter((entry) => entry.score > 0)
      .sort((a, b) => b.score - a.score || a.order - b.order);
    const limit = browsing ? BROWSE_LIMIT[group] : undefined;
    const kept = limit === undefined ? scored : limit === 0 ? scored.filter((entry) => entry.command.browse === true) : scored.slice(0, limit);
    return { group, best: scored[0]?.score ?? 0, items: kept.map((entry) => entry.command) };
  }).filter((group) => group.items.length > 0);
  if (!browsing) groups.sort((a, b) => b.best - a.best);
  return groups.map(({ group, items }) => ({ group, items }));
}

function domId(base: string, id: string): string {
  return `${base}-${id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

/** What `>` and `@` do, for whoever opens the palette and wonders. */
function Hints({ scopes }: { scopes: boolean }) {
  const t = useT();
  return (
    <div aria-hidden="true" className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-bg-sunken px-4 py-2 text-12 text-fg-faint max-sm:hidden">
      <span className="flex items-center gap-1.5">
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd>
        {t("shell.search.moveHint")}
      </span>
      <span className="flex items-center gap-1.5">
        <Kbd>↵</Kbd>
        {t("shell.search.openHint")}
      </span>
      <span className="flex items-center gap-1.5">
        <Kbd>&gt;</Kbd>
        {t("shell.search.actionsHint")}
      </span>
      {scopes ? (
        <span className="flex items-center gap-1.5">
          <Kbd>@</Kbd>
          {t("shell.search.scopeHint")}
        </span>
      ) : null}
      <span className="flex items-center gap-1.5">
        <Kbd>Esc</Kbd>
        {t("shell.search.closeHint")}
      </span>
    </div>
  );
}

function PaletteBody({
  commands,
  onRun,
  inputRef,
  scope,
}: {
  commands: readonly Command[];
  onRun: (command: Command) => void;
  inputRef: RefObject<HTMLInputElement | null>;
  scope?: PaletteScope | undefined;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const base = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const listId = `${base}-results`;

  // The three the operator opened last, found again among what is offered now (a page that is gone is not).
  const recent = useMemo(
    () =>
      readRecent()
        .flatMap((id) => commands.filter((command) => command.id === id).slice(0, 1))
        .slice(0, RECENT_SHOWN)
        .map((command): Command => ({ ...command, id: `recent:${command.id}`, group: "Recent" })),
    [commands],
  );
  const results = useMemo(() => filterCommands(commands, query, recent), [commands, query, recent]);
  const flat = useMemo(() => results.flatMap((group) => group.items), [results]);
  const positions = useMemo(() => new Map(flat.map((command, position) => [command, position])), [flat]);
  const activeIndex = flat.length === 0 ? -1 : Math.min(active, flat.length - 1);
  const activeCommand = activeIndex >= 0 ? flat[activeIndex] : undefined;
  const activeId = activeCommand ? domId(base, activeCommand.id) : undefined;
  const scopes = useMemo(() => commands.some((command) => command.group === "Servers"), [commands]);

  useEffect(() => {
    if (activeId === undefined) return;
    document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (flat.length === 0) return;
    if (event.key === "ArrowDown" || (event.key === "n" && event.ctrlKey)) {
      event.preventDefault();
      setActive((activeIndex + 1) % flat.length);
    } else if (event.key === "ArrowUp" || (event.key === "p" && event.ctrlKey)) {
      event.preventDefault();
      setActive((activeIndex - 1 + flat.length) % flat.length);
    } else if (event.key === "Enter" && activeCommand) {
      event.preventDefault();
      onRun(activeCommand);
    }
  };

  return (
    <>
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-border px-4">
        <Search aria-hidden="true" className="size-4 shrink-0 text-fg-faint" />
        {scope !== undefined ? (
          // Where the palette is working, said once: its results are about this server or this application.
          <span data-slot="scope" className="inline-flex h-6 max-w-40 shrink-0 items-center gap-1 rounded-chip border border-border bg-bg-sunken px-1.5 text-12 text-fg-muted">
            <span className="shrink-0">{scope.label}</span>
            {scope.name !== undefined ? (
              <Mono tone="default" truncate className="min-w-0">
                {scope.name}
              </Mono>
            ) : null}
          </span>
        ) : null}
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label={t("shell.search.placeholder")}
          aria-expanded={flat.length > 0}
          aria-controls={flat.length > 0 ? listId : undefined}
          aria-activedescendant={activeId}
          aria-autocomplete="list"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder={t("shell.search.placeholder")}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          className="h-full min-w-0 flex-1 bg-transparent text-14 text-fg outline-none placeholder:text-fg-faint"
        />
        <Kbd className="max-sm:hidden">Esc</Kbd>
      </div>

      {flat.length > 0 ? (
        <div
          id={listId}
          role="listbox"
          aria-label={t("shell.search.results")}
          className="max-h-[min(60vh,26rem)] min-h-0 overflow-y-auto p-1.5 scroll-thin"
        >
          {results.map((result) => (
            <div key={result.group} role="group" aria-labelledby={`${base}-${result.group}`} className="pb-1">
              <div id={`${base}-${result.group}`} aria-hidden="true" className="px-2 pt-2 pb-1 text-12 font-medium text-fg-faint">
                {t(GROUP_LABEL[result.group])}
              </div>
              {result.items.map((command) => {
                const position = positions.get(command) ?? 0;
                const selected = command === activeCommand;
                return (
                  // Keyboard selection lives on the input (aria-activedescendant); the row only
                  // answers the pointer.
                  // eslint-disable-next-line jsx-a11y/click-events-have-key-events
                  <div
                    key={command.id}
                    id={domId(base, command.id)}
                    role="option"
                    aria-selected={selected}
                    tabIndex={-1}
                    onMouseMove={() => {
                      if (!selected) setActive(position);
                    }}
                    onMouseDown={(event) => {
                      event.preventDefault();
                    }}
                    onClick={() => {
                      onRun(command);
                    }}
                    className={cx(
                      "flex h-9 cursor-pointer items-center gap-2.5 rounded-control px-2 text-13 text-fg select-none",
                      selected && "bg-surface-active",
                    )}
                  >
                    <span aria-hidden="true" className="flex size-4 shrink-0 text-fg-muted [&_svg]:size-4">
                      {command.icon}
                    </span>
                    <span translate={NAMED_GROUPS.has(command.group) ? "no" : undefined} className="min-w-0 flex-1 truncate">
                      {command.label}
                    </span>
                    {command.detail !== undefined ? (
                      // Read with the name by a screen reader: it is what tells two of them apart, and says where one lives.
                      <span className="max-w-48 shrink truncate text-12 text-fg-muted max-sm:hidden">{command.detail}</span>
                    ) : null}
                    {command.status !== undefined ? (
                      <StatusPill state={command.status} appearance="inline" size="sm" />
                    ) : null}
                    {command.shortcut ? (
                      <span aria-hidden="true" className="flex shrink-0 items-center gap-0.5">
                        {command.shortcut.map((key) => (
                          <Kbd key={key}>{key}</Kbd>
                        ))}
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      ) : (
        <div role="status" className="flex flex-col gap-1 px-4 py-10 text-center">
          <p className="text-13 text-fg-muted">{t("shell.search.noMatches", { query: query.trim() })}</p>
          <p className="text-12 text-pretty text-fg-faint">{t("shell.search.noMatchesHint")}</p>
          <p className="text-12 text-fg-faint">{t.rich("shell.search.noMatchesCli", { command: <Mono key="cli">noust --help</Mono> })}</p>
        </div>
      )}

      <Hints scopes={scopes} />
    </>
  );
}

export interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: readonly Command[];
  /** What the palette is working in, shown at the left of the field. */
  scope?: PaletteScope | undefined;
  /** Where focus returns when the palette closes without opening a page. */
  returnFocus?: () => HTMLElement | null;
}

/**
 * Jump anywhere, by name: pages, applications and actions (`Ctrl+K` / `⌘K`). An ARIA 1.2
 * combobox: focus stays in the input while the arrow keys move a highlighted option, which
 * is announced through aria-activedescendant.
 */
export function CommandPalette({ open, onOpenChange, commands, scope, returnFocus }: CommandPaletteProps) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement>(null);
  const lastRun = useRef<Command["kind"] | null>(null);

  // The palette is opened from outside (the trigger, Ctrl+K), which Base UI's onOpenChange
  // never reports; without this, a page opened from it once kept focus from ever being
  // handed back on a later Escape.
  useEffect(() => {
    if (open) lastRun.current = null;
  }, [open]);

  const run = (command: Command): void => {
    lastRun.current = command.kind;
    // A page opened, or a thing made, is worth finding again; a theme switched or a session ended is not.
    if (command.kind === "navigate" && command.id !== "action:sign-out") rememberRecent(command.id.replace(/^recent:/, ""));
    onOpenChange(false);
    command.run();
  };

  return (
    <BaseDialog.Root
      open={open}
      onOpenChange={(next: boolean) => {
        if (next) lastRun.current = null;
        onOpenChange(next);
      }}
    >
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className={BACKDROP} />
        <BaseDialog.Viewport className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[10vh] sm:pt-[14vh]">
          <BaseDialog.Popup
            initialFocus={inputRef}
            finalFocus={() => {
              // A page was opened: it takes focus itself (its h1), nothing to hand back.
              if (lastRun.current === "navigate") return false;
              return returnFocus?.() ?? true;
            }}
            className={cx(MODAL_POPUP, "sm:max-w-[640px]")}
          >
            <BaseDialog.Title className="sr-only">{t("shell.search.dialogTitle")}</BaseDialog.Title>
            <PaletteBody commands={commands} onRun={run} inputRef={inputRef} scope={scope} />
          </BaseDialog.Popup>
        </BaseDialog.Viewport>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  );
}
