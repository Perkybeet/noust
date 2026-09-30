/**
 * Passkeys against the server (`/api/auth/passkeys`): the three ceremonies - sign in, confirm
 * it's you, add one - each an options call, the browser's prompt and the answer, plus the
 * list, renaming and removing. A passkey belongs to whoever is signed in: a person's account,
 * or the master token itself.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { createPasskey, getPasskey } from "./webauthn";
import type { GetOptions } from "./webauthn";

export type PasskeyList = ResponseOf<"/api/auth/passkeys", "get">;
export type Passkey = PasskeyList["passkeys"][number];
export type PasskeyAvailability = PasskeyList["availability"];
export type PasskeyRegistered = ResponseOf<"/api/auth/passkeys/registration", "post">;
export type PasskeyLogin = ResponseOf<"/api/auth/passkeys/login", "post">;

export const passkeyKeys = {
  list: ["auth", "passkeys"] as const,
};

/** The signed-in owner's passkeys, and whether passkeys work from this page at all. */
export const passkeysQuery = () =>
  queryOptions({
    queryKey: passkeyKeys.list,
    queryFn: ({ signal }) => request("get", "/api/auth/passkeys", { signal }),
  });

/**
 * Signs in with a passkey: a complete sign-in, no name or password. With `conditional`, the
 * browser offers the passkey in the username field's autofill and the promise settles only
 * when the operator picks one (or the signal aborts it).
 */
export async function signInWithPasskey({ mediation, signal }: GetOptions = {}): Promise<PasskeyLogin> {
  const conditional = mediation === "conditional";
  const options = await request("post", "/api/auth/passkeys/login/options", { body: { conditional }, ...(signal ? { signal } : {}) });
  const credential = await getPasskey(options.public_key, { mediation, signal });
  return request("post", "/api/auth/passkeys/login", { body: { credential, bearer: false } });
}

/** Confirms it's you with one of this owner's passkeys: opens sudo mode. */
export async function elevateWithPasskey(): Promise<string> {
  const options = await request("post", "/api/auth/passkeys/elevate/options");
  const credential = await getPasskey(options.public_key);
  const { elevated_until } = await request("post", "/api/auth/passkeys/elevate", { body: { credential } });
  return elevated_until;
}

/**
 * Adds a passkey. Outside an account's first second factor the server asks for "Confirm it's
 * you" first, which the API client handles before the browser's prompt ever opens.
 */
export async function registerPasskey(name: string): Promise<PasskeyRegistered> {
  const options = await request("post", "/api/auth/passkeys/registration/options");
  const credential = await createPasskey(options.public_key);
  return request("post", "/api/auth/passkeys/registration", { body: { credential, name } });
}

export function renamePasskey(id: number, name: string) {
  return request("patch", "/api/auth/passkeys/{passkey_id}", { params: { passkey_id: id }, body: { name } });
}

export function removePasskey(id: number) {
  return request("delete", "/api/auth/passkeys/{passkey_id}", { params: { passkey_id: id } });
}
