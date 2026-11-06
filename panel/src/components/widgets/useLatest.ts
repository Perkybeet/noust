import { useLayoutEffect, useRef } from "react";
import type { RefObject } from "react";

/**
 * The value of the last render, for a handler a window listener holds on to: the listener is added once
 * and reads this, so it never acts on what the page was before the pointer moved. Written in a layout
 * effect, which runs before any event the new render could be asked about.
 */
export function useLatest<T>(value: T): RefObject<T> {
  const latest = useRef(value);
  useLayoutEffect(() => {
    latest.current = value;
  });
  return latest;
}
