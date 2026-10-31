import type { time as en } from "../en/time";
import type { Catalog } from "../types";

export const time: Catalog<typeof en> = {
  justNow: "ahora mismo",
  never: "Nunca",
  // A narrow no-break space (U+202F) between a figure and its unit, as the Spanish orthography has it: in the mono
  // face the durations of a table are set in, a plain space is a whole cell and "41 s" read as "41  s".
  duration: {
    milliseconds: "{value}\u202fms",
    seconds: "{value}\u202fs",
    minutes: "{value}\u202fmin",
    minutesSeconds: "{minutes}\u202fmin {seconds}\u202fs",
    hoursMinutes: "{hours}\u202fh {minutes}\u202fmin",
    daysHours: "{days}\u202fd {hours}\u202fh",
  },
};
