/**
 * Strings of the console's common area: shared UI primitives and the page kit
 * (components/ui/**, components/page/**). A component that receives its text from a caller
 * (a title, a description) is not represented here - only each component's own built-in
 * defaults are.
 */
export const common = {
  area: "Loading",
  copyButton: {
    label: "Copy",
    copyCommand: "Copy command",
    copyOutput: "Copy output",
    copyValue: "Copy {value}",
    copied: "Copied to clipboard",
    failed: "Copy failed",
  },
  copyTextButton: {
    failed: "Copy failed. Select the text and copy it by hand.",
  },
  dialog: {
    close: "Close",
  },
  field: {
    optional: "Optional",
  },
  confirmDialog: {
    cancel: "Cancel",
    typeToConfirm: "Type {value} to confirm",
    failed: "The action failed. The system said:",
    whatSystemSaid: "What the system said",
  },
  select: {
    placeholder: "Select",
  },
  dataTable: {
    actions: "Actions",
  },
  logViewer: {
    label: "Log output",
    searchLabel: "Search output",
    noMatches: "No matches",
    matchPosition: "{current} of {total}",
    previousMatch: "Previous match",
    nextMatch: "Next match",
    follow: "Follow",
    wrapLines: "Wrap lines",
    downloadOutput: "Download output",
    fullLogHint: "Copies or downloads the entire log, not only the lines currently shown.",
    jumpToLatest: "Jump to latest",
    newLines: "new lines",
    empty: "No output yet.",
  },
  toast: {
    dismiss: "Dismiss notification",
    systemSaid: "What the system said",
    commandOutput: "The command's own output",
  },
  statusPill: {
    running: "Running",
    deploying: "Deploying",
    warning: "Warning",
    failed: "Failed",
    stopped: "Stopped",
    static: "Static",
    unknown: "Unknown",
  },
  errorBlock: {
    tryAgain: "Try again",
    systemSaidLabel: "{title}: what the system said",
    commandOutputLabel: "{title}: the command's own output",
  },
  queryState: {
    loading: "Loading {label}",
    couldNotLoad: "Could not load {label}",
    couldNotRefresh: "Could not refresh {label}. What follows is the last answer.",
  },
  dangerZone: {
    title: "Danger zone",
  },
  keyValueList: {
    empty: "Not set",
  },
  resourceMeter: {
    missing: "No reading",
    ofLimit: "{value} of {limit}",
    limit: "Limit {value}",
    noLimit: "No limit set",
  },
  chart: {
    noDataFor: "{label}: no data",
    seriesReading: "{label}: latest {latest}, low {low}, high {high}",
    markersInView: { one: "{count} marker in view.", other: "{count} markers in view." },
    seriesLabel: "Series",
    latest: "Latest",
    noReading: "no reading",
    time: "Time",
    markersHeading: "Markers",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    resetZoom: "Reset zoom",
    viewAsTable: "View as table",
    expand: "Expand {title}",
    dataRegion: "{title} data",
    newestFirst: "{title}, newest first",
    dragToZoom: "Drag across the chart to zoom into a stretch of time.",
    zoomedRange: "Showing {from} to {to}. Double-click the chart or reset the zoom to see all of it.",
    keyboardHint:
      "Left and right arrow keys step through the samples, Home and End go to the first and last, Escape clears.",
    keyboardHintZoomable: " Drag across the chart with a pointer to zoom, or use the zoom buttons.",
  },
} as const;
