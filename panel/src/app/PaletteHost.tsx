import { useParams } from "@tanstack/react-router";

import { useT } from "../i18n";
import { useHasFleet, useServerList } from "../nodes/servers";
import { useConsoleContext, useNode } from "../nodes/useNode";
import { CommandPalette } from "./CommandPalette";
import type { PaletteScope } from "./CommandPalette";
import { useConsoleCommands } from "./useConsoleCommands";
import type { AppVerb } from "./verbCommands";
import type { AppInfo } from "../features/apps/data";

/**
 * What the palette says it is working in: the application on screen, the fleet, this central, or the
 * server when there is more than one to tell apart. Nothing on a lone server (NV-10).
 */
export function usePaletteScope(): PaletteScope | undefined {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const { hostname } = useServerList();
  const { node } = useNode();
  const params = useParams({ strict: false });
  const domain = typeof params.domain === "string" ? params.domain : null;
  if (context.kind === "fleet" && hasFleet) return { label: t("shell.search.scope.all") };
  if (context.kind === "central" && hasFleet) return { label: t("shell.search.scope.central") };
  if (domain !== null) return { label: t("shell.search.scope.application"), name: domain };
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
  const commands = useConsoleCommands(open, onOpenShortcuts, onVerb);
  const scope = usePaletteScope();
  return <CommandPalette open={open} onOpenChange={onOpenChange} commands={commands} scope={scope} returnFocus={returnFocus} />;
}
