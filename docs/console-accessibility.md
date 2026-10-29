# Accessibility

Part of [The console, page by page](console.md).

The console targets WCAG 2.2 level AA, and that is tested rather than declared:

- The end-to-end suite runs axe (WCAG 2.0, 2.1 and 2.2, levels A and AA) on every page, in
  both themes, and fails on any violation. It also fails on any Content Security Policy
  violation or console error.
- Colour contrast of the design tokens is checked by unit tests in both themes: 4.5:1 for
  text, 3:1 for interface elements.
- Everything works from the keyboard. Focus is always visible, a "Skip to content" link comes
  first, dialogs trap focus and return it, and after navigating focus moves to the new page's
  title.
- State is never colour alone: it always has a shape and a text label.
- Deploy transitions are announced politely and failures assertively through live regions.
  Log lines are never announced.
- Every chart has a text summary, works from the keyboard (arrow keys step through readings,
  announced in a live region), and its expanded view has a Data tab with the readings as a
  table.
- A site's diagram has a written summary under it and the same connections as a table; every
  element can be focused from the keyboard and has an accessible name that includes its state.
- With `prefers-reduced-motion`, transitions are instant and the moving dashes of the diagram
  stand still.

If something in the console is not usable with your assistive technology, please open an
issue on GitHub.
