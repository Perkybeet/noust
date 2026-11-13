import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { sessionQuery } from "../api/queries/auth";
import { searchViewQuery } from "../api/queries/views";
import type { SearchHit } from "../api/queries/views";
import { useT } from "../i18n";
import { useHasFleet, useServerList } from "../nodes/servers";
import { useSelection } from "../nodes/useNode";
import { CommandPalette } from "./CommandPalette";
import type { PaletteScope } from "./CommandPalette";
import { FLEET_SEARCH_LIMIT, fleetSearchCommands } from "./fleetSearchCommands";
import { useConsoleCommands } from "./useConsoleCommands";
import type { AppVerb } from "./verbCommands";
import type { AppInfo } from "../features/apps/data";

/**
 * What the palette says it is working in: the application on screen, the fleet, or the
 * server when there is more than one to tell apart. Nothing on a lone server (NV-10).
 */
export function usePaletteScope(): PaletteScope | undefined {
  const t = useT();
  const hasFleet = useHasFleet();
  const { hostname } = useServerList();
  // The selector's place, which a page of the instance (no server's) leaves where it was.
  const selection = useSelection();
  const params = useParams({ strict: false });
  const domain = typeof params.domain === "string" ? params.domain : null;
  if (selection.kind === "all" && hasFleet) return { label: t("shell.search.scope.all") };
  if (domain !== null) return { label: t("shell.search.scope.application"), name: domain };
  const node = selection.kind === "server" ? selection.node : null;
  if (hasFleet || node !== null) {
    const name = node ?? hostname;
    return { label: t("shell.search.scope.server"), ...(name !== null ? { name } : {}) };
  }
  return undefined;
}

export interface PaletteHostProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenShortcuts: () => void;
  onVerb: (verb: AppVerb, app: AppInfo) => void;
  returnFocus: () => HTMLElement | null;
}

/**
 * The command palette with everything it knows how to find. It is its own chunk: the frame of
 * every page carries the chord that opens it, and this code - the register's search, the resources,
 * the verbs - is fetched the first time it is wanted.
 */
export default function PaletteHost({ open, onOpenChange, onOpenShortcuts, onVerb, returnFocus }: PaletteHostProps) {
  const own = useConsoleCommands(open, onOpenShortcuts, onVerb);
  const scope = usePaletteScope();
  const navigate = useNavigate();
  const [typed, setTyped] = useState("");
  const { hits, words } = useFleetHits(typed, open);
  // What is on the other servers of the fleet, found by the central, beside what is on this one.
  const commands = useMemo(
    () => [...own, ...fleetSearchCommands(hits, words, (target) => void navigate({ to: target.to, search: target.search as never }))],
    [own, hits, words, navigate],
  );
  return <CommandPalette open={open} onOpenChange={onOpenChange} commands={commands} scope={scope} returnFocus={returnFocus} onQueryChange={setTyped} />;
}

const NO_HITS: readonly SearchHit[] = [];

interface FleetHits {
  hits: readonly SearchHit[];
  /** The words the hits answered, which is what they are found by while the next question is on its way. */
  words: string;
}

/** Two letters make a search worth asking the central for: fewer find nearly everything. */
const SEARCH_FROM = 2;

/** How long a pause in typing is a question: a keystroke each is not. */
const SEARCH_PAUSE_MS = 250;

/**
 * What the words match on the other servers of the fleet, once the palette is open, there is a fleet and the account
 * may read applications. It asks after a pause in typing, keeps the last answer while the next one is read (the list
 * never blanks), and asks nothing on a lone server.
 */
function useFleetHits(text: string, open: boolean): FleetHits {
  const hasFleet = useHasFleet();
  const { data: allowed } = useQuery({ ...sessionQuery(), select: (session) => session.permissions?.includes("apps.read") ?? false });
  const words = text.trim();
  const worth = open && hasFleet && allowed === true && words.length >= SEARCH_FROM;
  const [asked, setAsked] = useState("");
  useEffect(() => {
    if (!worth) return;
    const timer = window.setTimeout(() => {
      setAsked(words);
    }, SEARCH_PAUSE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [words, worth]);
  const found = useQuery({ ...searchViewQuery(asked, FLEET_SEARCH_LIMIT), enabled: worth && asked !== "", placeholderData: keepPreviousData });
  // What was asked of an earlier text is not shown for one too short to ask about.
  return worth && asked !== "" ? { hits: found.data?.items ?? NO_HITS, words: asked } : { hits: NO_HITS, words: "" };
}
