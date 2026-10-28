import { SegmentedControl } from "../../components/page/SegmentedControl";
import { LayoutGlyph } from "../../components/ui/LayoutGlyph";
import { Tooltip } from "../../components/ui/Tooltip";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { setNavPresentation, usePreferences } from "../preferences";
import type { NavPresentation } from "../preferences";

/**
 * The choice between the double bar and one bar, in the bar itself and drawn: two small windows, one
 * with two bars at its side and one with one, so it is chosen by seeing it (docs/DESIGN.md, NV-20). It is
 * the same choice in both bars, and the preference it sets is the browser's (app/preferences.ts).
 *
 * `compact` is for the main bar folded to icons, which has the room for one button: it names and draws
 * the other presentation, and pressing it goes there.
 */
export function PresentationSwitch({ presentation, compact = false, className }: { presentation: NavPresentation; compact?: boolean; className?: string }) {
  const t = useT();
  // The preference is what is saved; the window may be showing the other (below 1280 px it is always one bar).
  const saved = usePreferences().presentation;
  if (compact) {
    const other: NavPresentation = presentation === "double" ? "single" : "double";
    const name = t(other === "double" ? "nav.presentation.double" : "nav.presentation.single");
    return (
      <Tooltip content={name} side="right">
        <button
          type="button"
          aria-label={name}
          onClick={() => {
            setNavPresentation(other);
          }}
          className={cx(
            "elev-lift flex size-11 cursor-pointer items-center justify-center rounded-control text-fg-faint outline-none hover:bg-surface-raised hover:text-fg-muted",
            "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus",
            className,
          )}
        >
          <LayoutGlyph bars={other === "double" ? 2 : 1} className="size-icon-lg" />
        </button>
      </Tooltip>
    );
  }
  return (
    <SegmentedControl<NavPresentation>
      label={t("nav.presentation.label")}
      value={saved}
      onValueChange={setNavPresentation}
      fill
      {...(className !== undefined ? { className } : {})}
      options={[
        { value: "double", label: t("nav.presentation.double"), icon: <LayoutGlyph bars={2} /> },
        { value: "single", label: t("nav.presentation.single"), icon: <LayoutGlyph bars={1} /> },
      ]}
    />
  );
}
