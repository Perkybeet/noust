import { useLayoutEffect, useRef } from "react";

import { cx } from "../../lib/cx";
import { REDUCED_MOTION } from "./useMediaQuery";

/**
 * What marks the chosen item of a row of tabs, a group of segments or a list of navigation
 * items, in every attribute the kit and the router use for it.
 */
export const ACTIVE_ITEM =
  '[data-active], [aria-current="page"], [aria-pressed="true"], [aria-checked="true"], [aria-selected="true"], [data-checked], [data-status="active"]';

/** The room an item takes inside the container, in CSS pixels: where the indicator goes. */
export interface IndicatorBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Where `item` is inside `container`, measured the way layout lays it out (offsets, which ignore
 * the lift of a hovered neighbour and the transform of an ancestor) and not by what the screen
 * shows. An item inside other boxes (a link inside a `li`) counts every offset parent up to the
 * container; the container must be a positioned element (`relative`).
 */
export function measureItem(container: HTMLElement, item: HTMLElement): IndicatorBox {
  let x = 0;
  let y = 0;
  let node: HTMLElement | null = item;
  while (node !== null && node !== container) {
    x += node.offsetLeft;
    y += node.offsetTop;
    const parent: Element | null = node.offsetParent;
    node = parent instanceof HTMLElement ? parent : null;
  }
  if (node !== container) {
    // The item is not laid out inside the container (a portal, a detached tree): by the screen.
    const a = container.getBoundingClientRect();
    const b = item.getBoundingClientRect();
    return { x: b.left - a.left + container.scrollLeft, y: b.top - a.top + container.scrollTop, width: b.width, height: b.height };
  }
  return { x, y, width: item.offsetWidth, height: item.offsetHeight };
}

export interface SlidingIndicatorProps {
  /** The selector of the chosen item inside the container. Defaults to every attribute the kit and the router use. */
  activeSelector?: string;
  /**
   * `raised` is the chosen tab, segment or navigation item (the raised height); `flat` is the
   * highlight of an option being passed over in a menu or a list (a carved tone, no edge).
   */
  variant?: "raised" | "flat";
  /** Radius, ground and anything else that is the piece's own: `rounded-control bg-surface`. */
  className?: string;
}

/**
 * The raised piece of the chosen item, which slides to the one the person picks instead of
 * appearing there. One piece for horizontal rows (tabs, segments) and vertical lists (a
 * navigation bar), shared so that all of them move the same way.
 *
 * Draw it first inside the positioned element that holds the items (`relative isolate`: the rail,
 * the list); the items are `relative` too, so they paint above it. It measures the chosen item and writes where it is as custom properties on itself through the
 * CSSOM (`--indicator-x/-y/-width/-height`; never a style attribute, which the Content Security
 * Policy forbids), and `.sliding-indicator` (app.css) moves by `translate` and resizes by `width`
 * and `height`, which keeps the rounded corners as drawn. It follows the chosen item when it
 * changes by anything (a click, a key, the browser's back and forward, a route), and when the item
 * changes size (a font arrives, the bar folds, the window resizes).
 *
 * It slides only when the choice changes between two items that are both in the list. It does not
 * slide on the first frame, when the item changes size, when there was no chosen item (it is
 * hidden then, and appears in place) or with `prefers-reduced-motion`. Holding an arrow key does
 * not queue anything: a transition that is restarted goes from where the piece is, to the item of
 * the moment.
 */
export function SlidingIndicator({ activeSelector = ACTIVE_ITEM, variant = "raised", className }: SlidingIndicatorProps) {
  const indicatorRef = useRef<HTMLSpanElement>(null);

  useLayoutEffect(() => {
    const indicator = indicatorRef.current;
    // The container is the element it is drawn in, which is how the layout effect finds it: a
    // ref handed in by the parent is not attached yet when a child's layout effect runs.
    const container = indicator?.parentElement;
    if (!indicator || !container) return;

    let hadItem = false;
    let observed: Element | null = null;

    const place = (slide: boolean): void => {
      const item = container.querySelector<HTMLElement>(activeSelector);
      if (item === null) {
        indicator.setAttribute("data-state", "hidden");
        indicator.removeAttribute("data-animate");
        hadItem = false;
        return;
      }
      const box = measureItem(container, item);
      // Decided before the values change: the transition that runs is the one the new style says.
      if (slide && hadItem && !window.matchMedia(REDUCED_MOTION).matches) indicator.setAttribute("data-animate", "");
      else indicator.removeAttribute("data-animate");
      indicator.style.setProperty("--indicator-x", `${String(box.x)}px`);
      indicator.style.setProperty("--indicator-y", `${String(box.y)}px`);
      indicator.style.setProperty("--indicator-width", `${String(box.width)}px`);
      indicator.style.setProperty("--indicator-height", `${String(box.height)}px`);
      indicator.setAttribute("data-state", "visible");
      hadItem = true;
      if (observed !== item) {
        if (observed !== null) resize?.unobserve(observed);
        resize?.observe(item);
        observed = item;
      }
    };

    // A size that changes by itself (a font, the bar folding, a window) is followed at once: it
    // is not a choice, and a piece that trailed behind it would be off its item for 200ms.
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => place(false));
    resize?.observe(container);

    // The choice changes by an attribute of the items, whoever sets it: this component's owner, a
    // router that marks the current link, Base UI that marks the checked radio.
    const choice = new MutationObserver(() => place(true));
    choice.observe(container, { attributes: true, subtree: true, attributeFilter: attributesOf(activeSelector) });

    const onWindowResize = (): void => place(false);
    window.addEventListener("resize", onWindowResize);
    // Text is wider or narrower once its font arrives, which moves every item after the first.
    let alive = true;
    // jsdom has no FontFaceSet, so the type's "always there" is not the whole truth.
    const fonts = (document as { fonts?: FontFaceSet }).fonts;
    void fonts?.ready.then(() => {
      if (alive) place(false);
    });

    place(false);
    return () => {
      alive = false;
      resize?.disconnect();
      choice.disconnect();
      window.removeEventListener("resize", onWindowResize);
    };
  }, [activeSelector]);

  return (
    <span
      ref={indicatorRef}
      aria-hidden="true"
      data-slot="sliding-indicator"
      data-variant={variant}
      data-state="hidden"
      className={cx("sliding-indicator", className)}
    />
  );
}

/** The attributes a selector reads, so the observer wakes for the ones that can change the choice. */
export function attributesOf(selector: string): string[] {
  return [...new Set([...selector.matchAll(/\[([a-z-]+)/g)].map((match) => match[1] ?? ""))].filter((name) => name !== "");
}
