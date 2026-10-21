import { screen, waitFor } from "@testing-library/react";
import { expect } from "vitest";

/**
 * A region once its skeleton has been replaced by what it was loading. A list and its skeleton are
 * two elements, so a reference taken while it loads is gone by the time the rows arrive.
 */
export async function findLoadedRegion(name: string | RegExp): Promise<HTMLElement> {
  return waitFor(() => {
    const region = screen.getByRole("region", { name });
    expect(region.querySelector("[aria-busy='true']")).toBeNull();
    return region;
  });
}
