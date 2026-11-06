/**
 * The picture an operator chose for the sign-in screen (item 129): what the screen draws in place of
 * Noust's own noust, how it is fitted, and the calls that set and remove it.
 *
 * Reading is public, because the screen is drawn before there is a session, and it never fails the
 * screen: an answer that does not come is the drawing. Setting and removing ask for the sign-in
 * policy's permission and for sudo mode (the console's "Confirm it's you" dialog answers the 403 and
 * retries once).
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type SignInAppearance = ResponseOf<"/api/auth/sign-in/appearance", "get">;
export type SignInPicture = NonNullable<SignInAppearance["image"]>;
export type SignInLimits = NonNullable<SignInAppearance["limits"]>;
export type SignInAppearanceBody = BodyOf<"/api/auth/sign-in/appearance", "put">;

/** How the picture fills its space: `cover` crops what does not fit, `contain` shows it whole. */
export type SignInFit = "cover" | "contain";

export const signInImageKeys = {
  appearance: ["auth", "sign-in", "appearance"] as const,
};

/**
 * What the sign-in screen draws. No retry and no refetch on focus: the answer is one small document
 * that an operator changes rarely, and a screen that waits to learn it is a screen that flashes the
 * wrong picture. A failed read is the drawing (the caller treats an error as "none").
 */
export const signInAppearanceQuery = () =>
  queryOptions({
    queryKey: signInImageKeys.appearance,
    queryFn: ({ signal }) => request("get", "/api/auth/sign-in/appearance", { signal }),
    retry: false,
    staleTime: 60_000,
  });

/** Sets the picture, or (without `image`) changes only how the one there is fitted and described. */
export function saveSignInImage(body: SignInAppearanceBody) {
  return request("put", "/api/auth/sign-in/appearance", { body });
}

/** Takes the picture off: the drawing comes back. */
export function removeSignInImage() {
  return request("delete", "/api/auth/sign-in/appearance");
}
