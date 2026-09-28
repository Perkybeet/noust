/**
 * Strings of the console's auth area: sign-in, two-factor, a locked-out attempt, a session
 * that expired, and "Confirm it's you" (sudo mode).
 */
export const auth = {
  area: "Sign in",
  subtitle: "Use this server's access token to open its console.",
  sessionExpired: "Your session expired. Sign in again to continue where you left off.",
  accessToken: "Access token",
  accessTokenHint: "Print it on the server with {command}",
  enterToken: "Enter the access token.",
  enterCode: "Enter the code.",
  tokenAccepted: "Access token {status}",
  accepted: "accepted",
  tokenAcceptedAnnounce: "Token accepted. Enter your two-factor code.",
  useDifferentToken: "Use a different token",
  twoFactorCode: "Two-factor code",
  twoFactorHint: "The 6-digit code from your authenticator app, or one of your backup codes.",
  signIn: "Sign in",
  verify: "Verify",
  signInFailed: "Signing in failed. The server said:",
  whatServerSaid: "What the server said",
  tooManyAttempts: "Too many failed attempts",
  lockedFor: "Sign-in is locked for this address. Try again in {time}.",
  tryAgainIn: "Try again in {time}.",
  lockedMinutes: { one: "{count} minute", other: "{count} minutes" },
  signedIn: "Signed in",
  elevate: {
    title: "Confirm it's you",
    descriptionTotp:
      "This action needs a recent confirmation. Enter a code from your authenticator app or one of your backup codes. It covers the next 10 minutes.",
    descriptionToken:
      "This action needs a recent confirmation. Enter the access token of this server. It covers the next 10 minutes.",
    authenticationCode: "Authentication code",
    cancel: "Cancel",
    confirm: "Confirm",
    confirmFailed: "The confirmation failed. The system said:",
    whatSystemSaid: "What the system said",
  },
  signOutFailed: "Sign out failed",
  signedOut: "Signed out",
} as const;
