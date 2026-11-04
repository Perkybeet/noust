/** Strings of Home's panels: the grid a person arranges, the sizes, the gallery they add from and each widget. */
export const widgets = {
  sizes: {
    small: "Small",
    medium: "Medium",
    large: "Large",
    tall: "Tall",
    wide: "Wide",
    xlarge: "Extra large",
  },
  grid: {
    label: "Widgets of this panel",
    move: "Move {title}",
    moveHelp: "Press Space to lift the widget, move it with the arrow keys, press Space again to drop it, or Escape to put it back.",
    lifted: "{title} lifted. The arrow keys move it, Space drops it and Escape puts it back.",
    position: "{title}: position {position} of {count}.",
    dropped: "{title} placed at position {position}.",
    cancelled: "{title} put back where it was.",
    sizeButton: "Size and position of {title}: {size}",
    sizeGroup: "Size",
    current: "current size",
    earlier: "Move earlier",
    later: "Move later",
    resized: "{title}: size {size}.",
    remove: "Remove {title}",
    removed: "{title} removed from the panel.",
    added: "{title} added at the end of the panel.",
  },
} as const;
