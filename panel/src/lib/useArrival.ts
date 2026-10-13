import { useEffect, useRef } from "react";

/**
 * Runs `onArrive` each time `ready` turns true: a page reached by a link that asks for something
 * (`?new`, from the palette) does it once, and then spends the link so `ready` turns false again.
 *
 * The latest `onArrive` is the one that runs, so the caller does not have to list what it reads;
 * what it reads is what the page had when `ready` turned true.
 */
export function useOnArrival(ready: boolean, onArrive: () => void): void {
  const latest = useRef(onArrive);
  useEffect(() => {
    latest.current = onArrive;
  });
  useEffect(() => {
    if (ready) latest.current();
  }, [ready]);
}
