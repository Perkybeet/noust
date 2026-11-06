import { render, screen } from "@testing-library/react";
import { Suspense, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { lazyWithPreload } from "./lazyWithPreload";

function Column({ name }: { name: string }) {
  return <p>{`Sections of ${name}`}</p>;
}

function drawn(Component: ReturnType<typeof lazyWithPreload<{ name: string }>>) {
  return render(
    <Suspense fallback={<p>Loading the column</p>}>
      <Component name="shop.example.net" />
    </Suspense>,
  );
}

describe("a lazy component that can be loaded before it is drawn", () => {
  it("draws no fallback for a component whose chunk was preloaded: the first picture is the component", async () => {
    const load = vi.fn(() => Promise.resolve({ default: Column }));
    const Lazy = lazyWithPreload(load);
    await Lazy.preload();
    drawn(Lazy);
    // Synchronously after the render: React alone would have drawn the fallback here, because it cannot know that the module
    // it is handed a promise for is already in.
    expect(screen.queryByText("Loading the column")).toBeNull();
    expect(screen.getByText("Sections of shop.example.net")).toBeInTheDocument();
  });

  it("is the fallback and then the component when nothing read the chunk ahead", async () => {
    const Lazy = lazyWithPreload(() => Promise.resolve({ default: Column }));
    drawn(Lazy);
    expect(screen.getByText("Loading the column")).toBeInTheDocument();
    expect(await screen.findByText("Sections of shop.example.net")).toBeInTheDocument();
  });

  it("asks for the chunk once, however many times it is preloaded and drawn", async () => {
    const load = vi.fn(() => Promise.resolve({ default: Column }));
    const Lazy = lazyWithPreload(load);
    await Promise.all([Lazy.preload(), Lazy.preload()]);
    drawn(Lazy);
    drawn(Lazy);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("does not remount what it drew when the chunk arrives later: the state of the tree under it stays", async () => {
    let release: (module: { default: typeof Counter }) => void = () => undefined;
    function Counter() {
      const [count, setCount] = useState(0);
      return (
        <button type="button" onClick={() => setCount(count + 1)}>
          {`pressed ${String(count)}`}
        </button>
      );
    }
    const Lazy = lazyWithPreload<object>(() => new Promise((resolve) => (release = resolve)));
    const { rerender } = render(
      <Suspense fallback={<p>Loading the column</p>}>
        <Lazy />
      </Suspense>,
    );
    release({ default: Counter });
    const button = await screen.findByRole("button", { name: "pressed 0" });
    button.click();
    expect(await screen.findByRole("button", { name: "pressed 1" })).toBe(button);
    rerender(
      <Suspense fallback={<p>Loading the column</p>}>
        <Lazy />
      </Suspense>,
    );
    expect(screen.getByRole("button", { name: "pressed 1" })).toBe(button);
  });

  it("asks again for a chunk that failed, for whoever needs it next, and says nothing of it until someone does", async () => {
    const load = vi
      .fn<() => Promise<{ default: typeof Column }>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({ default: Column });
    const Lazy = lazyWithPreload(load);
    await expect(Lazy.preload()).rejects.toThrow("offline");
    await Lazy.preload();
    expect(load).toHaveBeenCalledTimes(2);
    drawn(Lazy);
    expect(screen.getByText("Sections of shop.example.net")).toBeInTheDocument();
  });
});
