import { createElement, lazy, useState } from "react";
import type { ComponentType, ElementType, ReactElement } from "react";

/** A lazy component that can be loaded before anything draws it. */
export type PreloadableComponent<P> = ((props: P) => ReactElement) & {
  /** Loads the chunk, once; resolves when the component can be drawn without waiting. */
  preload: () => Promise<void>;
};

/**
 * `lazy` for a component whose chunk can be read ahead of the move that draws it. React suspends the first time it renders a
 * lazy component even when its module is already in the browser (it cannot tell that the promise is settled), so a hand-over
 * that had preloaded the chunk still showed the `Suspense` fallback for a frame. Once `preload` has resolved, a component that
 * mounts afterwards draws the loaded one in the same pass and no fallback is drawn. One that mounted before keeps what it
 * mounted with, so a chunk arriving never remounts the tree under it.
 *
 * @param load The dynamic import, mapped to a module with a default export. Name the component's props as the type
 *   argument when the import is mapped (`.then((module) => ({ default: module.Column }))`): they are not inferred through it.
 * @returns The component, with the `preload` that loads it.
 */
export function lazyWithPreload<P extends object>(load: () => Promise<{ default: ComponentType<P> }>): PreloadableComponent<P> {
  let loaded: ComponentType<P> | null = null;
  let loading: Promise<{ default: ComponentType<P> }> | null = null;
  const start = (): Promise<{ default: ComponentType<P> }> => {
    loading ??= load().then(
      (module) => {
        loaded = module.default;
        return module;
      },
      (error: unknown) => {
        // A chunk that failed is asked for again by whoever needs it next, not remembered as failed.
        loading = null;
        throw error;
      },
    );
    return loading;
  };
  const Lazy = lazy(start);
  const Component = (props: P): ReactElement => {
    const [chosen] = useState<ElementType>(() => loaded ?? Lazy);
    return createElement(chosen, props);
  };
  return Object.assign(Component, {
    preload: async (): Promise<void> => {
      await start();
    },
  });
}
