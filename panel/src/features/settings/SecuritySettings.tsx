/**
 * Settings > Security is gone: the signed-in person's own password, authenticator, passkeys and
 * sessions are the account's (features/account), and the compliance frameworks and the security
 * profile are Settings > Controls (features/settings/compliance/ControlsSettings.tsx). What is
 * left is the one function the ENS report still imports from here; its importer moves to
 * `./security` and this file goes with it.
 */
export { profileLabel } from "./security";
