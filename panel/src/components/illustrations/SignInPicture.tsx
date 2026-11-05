import { useState } from "react";

import { cx } from "../../lib/cx";
import { SignInArt } from "./SignInArt";

export interface SignInPictureProps {
  /** Where the picture is: the console's own address (`?v=<sha256>`) or, in a preview, a `data:` URL. */
  src: string;
  /** `cover` fills the space and crops what does not fit; `contain` shows the whole picture in it. */
  fit: "cover" | "contain";
  /** What a screen reader says in its place; empty says it is decoration. Already the operator's words. */
  alt: string;
  /** The picture's own size when it is known, so the browser keeps its proportions before it has loaded. */
  width?: number;
  height?: number;
  /** Told once when the picture does not load: the drawing is shown instead, and a preview may say why. */
  onFailed?: () => void;
}

/**
 * What an operator's picture is in the picture's slot of the screens before the console (T7's `art`),
 * where Noust draws a noust from the side: the same box as the drawing (400 by 230), so the slab does not
 * change size with the picture, and the picture in it, filling it or whole on the hollow's own surface.
 * A picture that does not load is the drawing: a screen that cannot be drawn is worse than one that is
 * not branded. It is a slot's content and nothing else: the logo of Noust, its sentence, the operator's
 * line and the licence notice are not in it.
 *
 * Give it a `key` of the address, so a new picture starts from a picture that has not failed.
 */
export function SignInPicture({ src, fit, alt, width, height, onFailed }: SignInPictureProps) {
  const [failed, setFailed] = useState(false);
  if (failed) return <SignInArt />;
  return (
    <div data-fit={fit} className={cx("aspect-[400/230] w-full overflow-hidden rounded-card", fit === "contain" && "p-3")}>
      <img
        src={src}
        alt={alt}
        {...(width !== undefined ? { width } : {})}
        {...(height !== undefined ? { height } : {})}
        decoding="async"
        fetchPriority="high"
        onError={() => {
          setFailed(true);
          onFailed?.();
        }}
        className={cx("size-full", fit === "cover" ? "object-cover" : "object-contain")}
      />
    </div>
  );
}
