import { SegmentedControl } from "../../components/page/SegmentedControl";
import { LayoutGlyph } from "../../components/ui/LayoutGlyph";
import { IconButton } from "../../components/ui/IconButton";
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
      <IconButton
        label={name}
        icon={<LayoutGlyph bars={other === "double" ? 2 : 1} className="size-icon-lg" />}
        onClick={() => {
          setNavPresentation(other);
        }}
        className={cx("size-11", className)}
      />
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
