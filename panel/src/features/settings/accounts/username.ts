/**
 * The account name a person's email proposes: what is before the `@`, in lower case, with accents taken off
 * and everything the server's rule does not allow turned into a dot, so "Lucía.Ferrer+ops@example.com" becomes
 * `lucia.ferrer.ops`. It is only a proposal: the person inviting can change it, and the server judges the
 * result (`USERNAME_PATTERN`: a letter or digit first, then letters, digits, `.`, `_` and `-`, up to 64).
 */
export function suggestUsername(email: string): string {
  const local = email.split("@", 1)[0] ?? "";
  const plain = local
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, ".")
    .replace(/\.{2,}/g, ".")
    .replace(/^[._-]+/, "")
    .replace(/[._-]+$/, "");
  return plain.slice(0, 64);
}

/** Whether a name is one the server's rule would take, so the form says what is wrong before it sends. */
export function isUsername(name: string): boolean {
  return /^[a-z0-9][a-z0-9._-]{1,63}$/.test(name);
}
