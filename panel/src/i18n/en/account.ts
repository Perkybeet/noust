/**
 * The signed-in person's own page (features/account): who they are on this console, their
 * password, and how they prove it - authenticator, passkeys and where they are signed in. The
 * sections' own words are the ones they always had (`settings.security.*`, `auth.security.*`);
 * what is here is the frame around them and what the account page adds. Theme and language are
 * preferences of the browser, not of the account: they live in the account menu only.
 */
export const account = {
  title: "Account",
  description: "Who you are on this console and how you prove it. Theme and language are in the account menu.",
  documentTitle: "Account",
  securityDocumentTitle: "Sign-in security",
  tokens: {
    title: "API tokens",
    description: "Tokens for scripts and CI are made in Settings, where each one is listed with its owner.",
    open: "Open API tokens",
  },
} as const;
