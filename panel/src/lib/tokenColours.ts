/**
 * Design tokens as concrete colours, for what is drawn on a canvas.
 *
 * A canvas knows nothing of CSS custom properties, and the tokens are `light-dark()` pairs whose
 * value depends on the colour scheme in force where they are read, not on the root. So a probe
 * element is put where the drawing will be, given `color: var(--token)`, and the browser's
 * computed colour is read back as an RGB string. A chart and the terminal both draw this way; this is
 * the one place that does it.
 */

export interface TokenReader {
  /** The colour a token has here, as the browser computes it, an RGB string. */
  read: (token: string) => string;
  /** Removes the probe. Call it once everything needed has been read. */
  release: () => void;
}

/**
 * Starts reading tokens as they are at `host`.
 *
 * Tokens are written `--name`. A token that is not defined reads as whatever the host's own
 * colour is, which is why callers name only tokens that exist (a test holds them to it).
 */
export function tokenReader(host: HTMLElement): TokenReader {
  const probe = document.createElement("span");
  // A transition on colour would make the read below return the previous token.
  probe.style.transition = "none";
  host.append(probe);
  return {
    read: (token) => {
      probe.style.color = `var(${token})`;
      return getComputedStyle(probe).color;
    },
    release: () => {
      probe.remove();
    },
  };
}
