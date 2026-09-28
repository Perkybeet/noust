/** Times and spans of time (lib/format.ts, RelativeTime). Numbers arrive already formatted. */
export const time = {
  justNow: "just now",
  never: "Never",
  duration: {
    milliseconds: "{value} ms",
    seconds: "{value}s",
    minutes: "{value}m",
    minutesSeconds: "{minutes}m {seconds}s",
    hoursMinutes: "{hours}h {minutes}m",
    daysHours: "{days}d {hours}h",
  },
} as const;
