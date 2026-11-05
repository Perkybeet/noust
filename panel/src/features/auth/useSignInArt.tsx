import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { signInAppearanceQuery } from "../../api/queries/signInImage";
import { SignInArt } from "../../components/illustrations/SignInArt";
import { SignInPicture } from "../../components/illustrations/SignInPicture";

/**
 * What fills the picture's slot of the screens before the console: the picture the operator chose
 * (item 129) or, with none, nothing, so the slab draws the noust it always did. While the answer is not
 * known the slot holds the drawing unseen, which is exactly the room the picture takes, so the slab does
 * not move when the answer comes and a company's screen never flashes Noust's drawing first. An answer
 * that does not come (the server is unreachable, a sealed central) is the drawing.
 *
 * @returns The slot's content, or undefined for the default drawing.
 */
export function useSignInArt(): ReactNode | undefined {
  const { data, isPending } = useQuery(signInAppearanceQuery());
  if (isPending) {
    return (
      <div className="invisible w-full">
        <SignInArt />
      </div>
    );
  }
  const image = data?.image;
  if (image === null || image === undefined) return undefined;
  return <SignInPicture key={image.url} src={image.url} fit={image.fit === "cover" ? "cover" : "contain"} alt={image.alt} width={image.width} height={image.height} />;
}
