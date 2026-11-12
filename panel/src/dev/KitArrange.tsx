import { Button } from "../components/ui";
import { toast } from "../components/ui/toast";
import { OBJECT_ICONS } from "../components/ui/objectIcons";
import { DropPlace, Grip, SizeOutline } from "../components/widgets/ArrangeParts";
import { widgetOf } from "../components/widgets/catalog";
import { GalleryCard } from "../components/widgets/GalleryCard";
import { WidgetFrame } from "../components/widgets/WidgetFrame";
import type { WidgetFace } from "../components/widgets/types";
import { BothThemes, Item, Section } from "./gallery";

const RESOURCES: WidgetFace = {
  title: "Resources",
  icon: OBJECT_ICONS.resources,
  sizes: ["small", "medium"],
  link: undefined,
  body: () => <p className="text-13 text-fg-muted">CPU 22%</p>,
};

const NOTHING = (): void => undefined;

/** A widget in a cell of the panel, the height of two rows, as the panel draws it while it is arranged. */
function Cell({ children }: { children?: React.ReactNode }) {
  return (
    <div className="relative h-44 w-56">
      <WidgetFrame face={RESOURCES} size="small" editing />
      {children}
    </div>
  );
}

/** Arranging a panel (docs/DESIGN.md, 6.17): what is drawn for a place, a size and a way back. */
export function KitArrange() {
  const figures = widgetOf("figures");
  return (
    <Section
      id="p-arrange"
      title="Arranging a panel"
      description="The panel of Home is arranged by taking its widgets by hand. What is held leaves the outline of the place it will land, at its real size and with the name of that size; an edge or a corner that is held draws the room the size it is on its way to will take; and a change that was made on purpose offers to be undone. All of it is the accent, the colour of what can be acted on."
    >
      <BothThemes>
        <div className="flex flex-col gap-8">
          <Item label="The place a held widget will land: 1px accent on the soft accent, the name of the size inside. Never dashed: that is the mark of an empty list">
            <div className="relative h-44 w-56">
              <DropPlace size="small" />
            </div>
          </Item>
          <Item label="The widget in the hand: itself, with the accent outline, a little lifted and turned">
            <div className="h-44 w-56 rotate-1">
              <WidgetFrame face={RESOURCES} size="small" editing className="outline-2 outline-accent" />
            </div>
          </Item>
          <Item label="Grips while it is arranged, a knob with an edge, and the one that is held">
            <Cell>
              <Grip handle="right" active={false} onStart={NOTHING} />
              <Grip handle="bottom" active onStart={NOTHING} />
              <Grip handle="corner" active={false} onStart={NOTHING} />
            </Cell>
          </Item>
          <Item label="The size an edge is on its way to: 2px accent over what is there, no ground, and its name">
            <div className="relative h-44 w-56">
              <WidgetFrame face={RESOURCES} size="small" editing />
              <SizeOutline size="medium" className="top-0 left-0 h-44 w-96" />
            </div>
          </Item>
          <Item label="A way back: the toast that says what was done offers to undo it">
            <Button onClick={() => toast.info("Moved Resources", { timeout: 8000, action: { label: "Undo", onClick: () => toast.dismiss() } })}>Move a widget</Button>
          </Item>
          {figures !== undefined ? (
            <Item label="A card of the gallery: the description and the sizes take the width of the card, nothing is cut">
              <ul className="w-56">
                <GalleryCard widget={figures} present={false} full={false} grabbable onAdd={NOTHING} onPointerDown={undefined} />
              </ul>
            </Item>
          ) : null}
        </div>
      </BothThemes>
    </Section>
  );
}
