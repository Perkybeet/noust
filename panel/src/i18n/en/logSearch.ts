/**
 * The name of the Logs destination, which the sidebar and the palette draw on every page: it is
 * the only part of the Logs texts the frame needs, so the rest is the `logs` namespace and loads
 * with the page.
 */
export const logSearch = {
  nav: {
    label: "Logs",
    keywords: "logs search journal grep errors lines applications containers output find message",
  },
} as const;
