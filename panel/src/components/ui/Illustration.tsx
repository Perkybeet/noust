import { cx } from "../../lib/cx";

export type IllustrationName = "first-steps" | "empty-slab";

export interface IllustrationProps {
  name: IllustrationName;
  /** Says what is drawn, when it says something the page does not ("The noust with a boat in it"). Without it the drawing is decoration. */
  label?: string;
  className?: string;
}

const STROKE = "fill-none stroke-il-stroke stroke-3 [stroke-linecap:round] [stroke-linejoin:round]";
const STROKE_LIGHT = "fill-none stroke-il-stroke-2 stroke-2 [stroke-linecap:round] [stroke-linejoin:round]";

/**
 * The drawings of the first time of a main function (docs/DESIGN.md 2.11): one grey stone stroke
 * and the hull's violet, the only coloured thing in them. Both follow the theme through the
 * tokens (`--il-*`), so there is one file of each and not one per theme.
 *
 * - `first-steps`: a noust, the hollow in the shore where boats are kept, with the hull in it and
 *   a course of dry stone on each side. For the first thing a person does in the console.
 * - `empty-slab`: an empty slab, dashed, waiting to be filled. For a list that has nothing yet.
 */
export function Illustration({ name, label, className }: IllustrationProps) {
  const a11y = label !== undefined ? ({ role: "img", "aria-label": label } as const) : ({ "aria-hidden": true } as const);
  if (name === "empty-slab") {
    return (
      <svg viewBox="0 0 200 120" width="200" height="120" focusable="false" xmlns="http://www.w3.org/2000/svg" className={cx("shrink-0", className)} {...a11y}>
        <path d="M10 100H70C80 100 84 108 94 108H106C116 108 120 100 130 100H190" className={STROKE} />
        <rect x="52" y="56" width="96" height="34" rx="17" strokeDasharray="3 7" className={STROKE} />
        <path d="M100 66v14M93 73h14" className="fill-none stroke-accent stroke-3 [stroke-linecap:round]" />
        <rect x="16" y="84" width="22" height="10" rx="5" className={STROKE_LIGHT} />
        <rect x="164" y="86" width="16" height="8" rx="4" className={STROKE_LIGHT} />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 360 190" width="360" height="190" focusable="false" xmlns="http://www.w3.org/2000/svg" className={cx("shrink-0", className)} {...a11y}>
      <rect x="14" y="116" width="96" height="22" rx="11" className={STROKE} />
      <rect x="26" y="92" width="72" height="22" rx="11" className={STROKE} />
      <rect x="38" y="68" width="48" height="22" rx="11" className={STROKE} />
      <rect x="250" y="116" width="96" height="22" rx="11" className={STROKE} />
      <rect x="262" y="92" width="72" height="22" rx="11" className={STROKE} />
      <rect x="274" y="68" width="48" height="22" rx="11" className={STROKE} />
      <rect x="118" y="132" width="26" height="12" rx="6" className={STROKE_LIGHT} />
      <rect x="224" y="128" width="20" height="10" rx="5" className={STROKE_LIGHT} />
      <rect x="2" y="146" width="22" height="10" rx="5" className={STROKE_LIGHT} />
      <rect x="340" y="146" width="18" height="10" rx="5" className={STROKE_LIGHT} />
      <path d="M4 140H112C134 140 140 162 164 162H196C220 162 226 140 248 140H356" className={STROKE} />
      <path
        transform="translate(180 154) scale(1.75) translate(-70 -31)"
        className="fill-accent"
        fillRule="evenodd"
        d="M42.75 5.5A1.25 1.25 0 0 1 45 4.75C48 8.75 52 16 70 16C88 16 92 8.75 95 4.75A1.25 1.25 0 0 1 97.25 5.5C97.25 24 88 31 70 31C52 31 42.75 24 42.75 5.5ZM46.5 12C51 17 57 20 70 20C83 20 89 17 93.5 12C89 18 83 21.5 70 21.5C57 21.5 51 18 46.5 12ZM50.5 20C54 24 59 25.5 70 25.5C81 25.5 86 24 89.5 20C86 25 81 27 70 27C59 27 54 25 50.5 20Z"
      />
      <path d="M132 178q8-6 16 0t16 0t16 0t16 0t16 0" className={STROKE_LIGHT} />
    </svg>
  );
}
