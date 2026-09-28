import type { time as en } from "../en/time";
import type { Catalog } from "../types";

export const time: Catalog<typeof en> = {
  justNow: "ahora mismo",
  never: "Nunca",
  duration: {
    milliseconds: "{value} ms",
    seconds: "{value} s",
    minutesSeconds: "{minutes} min {seconds} s",
    hoursMinutes: "{hours} h {minutes} min",
    daysHours: "{days} d {hours} h",
  },
};
