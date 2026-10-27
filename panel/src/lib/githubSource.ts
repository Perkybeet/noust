/**
 * Whether an application's source is a repository on github.com.
 *
 * It decides which hint a page shows (pull request previews need the GitHub App), so it is judged
 * from the start of the source: a host named somewhere inside a URL (`https://example.com/github.com/x`,
 * `https://github.com.evil.example/x`) is not GitHub.
 */
const GITHUB_SOURCE = /^(?:github:|(?:(?:https?|ssh|git):\/\/(?:[^/@]*@)?|git@)(?:www\.)?github\.com[:/])/i;

export function isGithubSource(source: string | null | undefined): boolean {
  return GITHUB_SOURCE.test(source ?? "");
}
