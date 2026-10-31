import { useCallback, useRef, useState } from "react";

import { sameSource } from "./wizard";
import type { SourceForm } from "./wizard";

/**
 * A read of the source that is under way, and whether to move on once it is read (Continue was pressed for it) or only to
 * show what it found (a paste, the field left).
 */
export interface SourceRead {
  form: SourceForm;
  advance: boolean;
}

export interface SourceReads {
  /**
   * Whether the read under way is one Continue asked for. Only then is Continue busy: a busy button takes no press, and
   * a press that began before the field's read started (every press of 80ms or more, as the field is left when it
   * starts) would end on a button that refuses it.
   */
  advancing: boolean;
  /**
   * Asks for a read of `form`. Returns the read to start, or null when the one under way already covers that source:
   * it is joined, and a press of Continue makes it one to go on with.
   */
  ask: (form: SourceForm, advance: boolean) => SourceRead | null;
  /** Says a read ended, and whether it is still the one that matters: a read another replaced, or that was given up, is not. */
  finish: (read: SourceRead) => boolean;
  /** Forgets the read under way, as a change of way does, so nothing still under way is gone on with. */
  forget: () => void;
  /**
   * Continue is pressed down: until the finger lifts, the field's being left (it is what a press on the button does)
   * asks for no read. A read that starts under the finger puts what it draws above the button, the button moves, and
   * the release lands on something else: no click, and the press is lost.
   */
  press: () => void;
  /** Whether a press on Continue is down. */
  pressing: () => boolean;
}

/**
 * Which read of the source is under way, kept apart from the render. Whoever presses Continue while the field's own read
 * runs joins that read instead of starting a second one, and decides it from this record: a render may be a tick behind
 * the call that started the read, and a press read from it started the read again or was lost.
 */
export function useSourceReads(): SourceReads {
  const under = useRef<SourceRead | null>(null);
  const [advancing, setAdvancing] = useState(false);
  const ask = useCallback((form: SourceForm, advance: boolean): SourceRead | null => {
    const current = under.current;
    if (current !== null && sameSource(current.form, form)) {
      if (advance && !current.advance) {
        current.advance = true;
        setAdvancing(true);
      }
      return null;
    }
    const read: SourceRead = { form, advance };
    under.current = read;
    setAdvancing(advance);
    return read;
  }, []);
  const finish = useCallback((read: SourceRead): boolean => {
    if (under.current !== read) return false;
    under.current = null;
    setAdvancing(false);
    return true;
  }, []);
  const forget = useCallback((): void => {
    under.current = null;
    setAdvancing(false);
  }, []);
  const down = useRef(false);
  const press = useCallback((): void => {
    down.current = true;
    const lift = (): void => {
      down.current = false;
      window.removeEventListener("pointerup", lift, true);
      window.removeEventListener("pointercancel", lift, true);
    };
    // Wherever it lifts: the button may have been left, or moved from under the finger.
    window.addEventListener("pointerup", lift, true);
    window.addEventListener("pointercancel", lift, true);
  }, []);
  const pressing = useCallback((): boolean => down.current, []);
  return { advancing, ask, finish, forget, press, pressing };
}
