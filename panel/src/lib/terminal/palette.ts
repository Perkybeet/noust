/**
 * The 256 colours of a terminal that are not the theme's.
 *
 * The first sixteen are the theme's (`components/ui/terminal/theme.ts` reads them from tokens,
 * so a program's red is the console's fail colour, as it is in the log viewer). The other 240
 * are fixed by xterm's definition: a 6 x 6 x 6 colour cube of levels 0, 95, 135, 175, 215 and
 * 255, then 24 greys from 8 to 238 in steps of 10. A program picks them by number precisely
 * because they are the same everywhere.
 */

import { TRUE_COLOUR } from "./screen";

/** The red, green and blue of a colour of the cube or the grey ramp (index 16 to 255). */
export function fixedColour(index: number): [number, number, number] {
  if (index >= 232) {
    const grey = 8 + (index - 232) * 10;
    return [grey, grey, grey];
  }
  const cube = index - 16;
  const level = (step: number): number => (step === 0 ? 0 : 55 + step * 40);
  return [level(Math.floor(cube / 36) % 6), level(Math.floor(cube / 6) % 6), level(cube % 6)];
}

/** The red, green and blue of a true colour. */
export function trueColour(colour: number): [number, number, number] {
  return [(colour >> 16) & 0xff, (colour >> 8) & 0xff, colour & 0xff];
}

/** Whether a colour value is a true colour rather than a palette index or the default. */
export function isTrueColour(colour: number): boolean {
  return colour >= TRUE_COLOUR;
}

/** A CSS colour for red, green and blue. */
export function cssRgb(red: number, green: number, blue: number): string {
  return `rgb(${String(red)} ${String(green)} ${String(blue)})`;
}
