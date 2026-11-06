import { NoustSide } from "./NoustSide";

/**
 * What fills the picture's slot on the screens before the console (T7's `art`): the noust from the
 * side. It is a component of its own, apart from the slab's composition, so that the slot can hold
 * something else (a company's logo, an image) without the slab changing: the slot gives whatever
 * is in it the width of the hollow, and the logo, the sentence, the operator's line and the licence
 * notice are outside it.
 */
export function SignInArt() {
  return <NoustSide className="h-auto w-full" />;
}
