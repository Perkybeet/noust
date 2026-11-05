/**
 * A page that gathers several forms (an application's Code and build, Deploys and triggers, Resources, Access)
 * keeps the rule that a form is saved from one bar (docs/DESIGN.md, T3, FM-1): the page is the form. Each part it
 * gathers still builds its own `useSaveBar`, and where it used to draw `<SaveBar>` it draws a `PartSaveBar`:
 * hosted, that registers its state with the page's `SaveHost` and draws nothing, so the page's one bar adds the
 * parts up, checks every part that changed before any is sent, asks "Confirm it's you" once and saves them in
 * order; standing alone (a unit test, a part opened by itself), it is the `SaveBar` it always was.
 */

import { useContext, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../../api/errors";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import type { SectionProps } from "../../../components/page/Section";
import { Disclosure } from "../../../components/ui/Disclosure";
import { cx } from "../../../lib/cx";
import { reportActionError } from "../../apps/useAppActions";
import type { SaveBarHandle } from "./formParts";
import { PartFoldedContext, PartHostContext, SaveRegistryContext } from "./settingsApp";
import type { SaveRegistry } from "./settingsApp";

/** Whether the part is drawn inside a page that gathers it: it then leaves out what the page says once. */
export function useHosted(): boolean {
  return useContext(PartHostContext);
}

/**
 * The page's one save bar, and the registry the parts it gathers write their state to. `notSaved` is what to
 * say when the save could not start at all ("The settings of shop.example.com were not saved").
 */
export function SaveHost({ notSaved, children }: { notSaved: string; children: ReactNode }) {
  const bars = useRef(new Map<string, SaveBarHandle>());
  // What is drawn is the sum of the parts' counts, kept as state so that drawing never reads the registry.
  const [changes, setChanges] = useState(0);
  const [saving, setSaving] = useState(false);

  const registry = useMemo<SaveRegistry>(
    () => ({
      set(id, bar) {
        bars.current.set(id, bar);
        // The callbacks are renewed on every render and read when pressed; only the count is drawn, and setting the
        // same number again draws nothing.
        setChanges(total(bars.current));
      },
      remove(id) {
        if (bars.current.delete(id)) setChanges(total(bars.current));
      },
    }),
    [],
  );

  // What is pressed reads the parts as they are then: a part draws again on every keystroke and renews its callbacks, and
  // the page is drawn again only when the count moves, so a list kept from the last draw would save what was typed a
  // moment ago.
  const changing = (): SaveBarHandle[] => [...bars.current.values()].filter((bar) => bar.changes > 0);

  const onSave = (): void => {
    const changed = changing();
    if (saving || changed.length === 0) return;
    // Every part that changed is checked first, so each shows its own mistakes and none is sent half way.
    if (!changed.map((bar) => bar.check()).every(Boolean)) return;
    setSaving(true);
    (async () => {
      for (const bar of changed) await bar.commit();
    })()
      .catch((error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(notSaved, error);
      })
      .finally(() => {
        setSaving(false);
      });
  };
  const onDiscard = (): void => {
    for (const bar of changing()) bar.onDiscard();
  };

  return (
    <SaveRegistryContext.Provider value={registry}>
      {children}
      <SaveBar changes={changes} saving={saving} onSave={onSave} onDiscard={onDiscard} />
    </SaveRegistryContext.Provider>
  );
}

/** How many fields differ from what is stored, in all the parts that registered. */
function total(bars: ReadonlyMap<string, SaveBarHandle>): number {
  let sum = 0;
  for (const bar of bars.values()) sum += bar.changes;
  return sum;
}

/** What a part draws where it used to draw its save bar. */
export function PartSaveBar({ bar }: { bar: SaveBarHandle }) {
  const registry = useContext(SaveRegistryContext);
  const id = useId();
  useLayoutEffect(() => {
    registry?.set(id, bar);
  });
  useLayoutEffect(
    () => () => {
      registry?.remove(id);
    },
    [registry, id],
  );
  if (registry !== null) return null;
  return <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />;
}

/**
 * The heading a part has as a subsection of its own: a `Section`. Inside a page it keeps its title, which names
 * what the cards under it are about (the page's own name is the column's), and leaves its line out: the cards
 * say what each is, and a page says one thing once.
 */
export function PartSection({ children, ...section }: SectionProps) {
  const hosted = useHosted();
  const folded = useContext(PartFoldedContext);
  const props = { ...section };
  if (hosted) delete props.description;
  // The fold's own label says what is inside: the heading stays for the reader of the page and is not drawn twice.
  if (folded) props.className = cx(props.className, "[&>header]:sr-only");
  return <Section {...props}>{children}</Section>;
}

/**
 * What a part keeps one press away on a page that gathers it: the cards most operators never open (how the repository is
 * reached, the sandbox and the cache of a build). Standing alone, a part shows everything, as it always did.
 */
export function PartFold({ label, children }: { label: string; children: ReactNode }) {
  if (!useHosted()) return <>{children}</>;
  return (
    <Disclosure variant="card" label={label}>
      <div className="flex min-w-0 flex-col gap-6">{children}</div>
    </Disclosure>
  );
}
