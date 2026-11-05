import { useEffect, useState } from "react";

/**
 * A value that follows another after it has stood still for `ms`: what a read made of what is being typed asks for, so
 * a person typing a port does not send one request for each digit.
 */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
