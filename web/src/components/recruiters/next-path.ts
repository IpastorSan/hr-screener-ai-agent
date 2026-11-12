// Pure redirect rules for the sign-in page, kept free of React so `node --test` runs them.

/** Where to go after signing in: only a dashboard path on this site, never another one. */
export function safeNext(next: string | null): string {
  return next && /^\/recruiters(\/[\w-]+)*$/.test(next) ? next : "/recruiters";
}

/** The sign-in page's URL, coming back to `pathname` afterwards. */
export function loginUrl(pathname: string | null): string {
  return pathname && pathname !== "/recruiters" && safeNext(pathname) === pathname
    ? `/login?next=${encodeURIComponent(pathname)}`
    : "/login";
}
