/**
 * Free Configs (fork feature).
 *
 * Deliberately thin. The page itself is served by this fork's backend at
 * /free-configs/panel and embedded here, rather than written as React
 * components, for two reasons:
 *
 *  - it keeps the fork's diff against upstream's dashboard down to this file,
 *    one route and one nav entry, so rebasing on a new PasarGuard release does
 *    not mean re-resolving a page's worth of conflicts;
 *  - the embedded page is plain HTML and can be exercised on its own, which the
 *    fork's toolchain can do and a React page inside this app could not.
 *
 * The iframe is same-origin, so its script authenticates with the very token
 * this dashboard already holds in localStorage, and every endpoint it calls is
 * still owner-only.
 */
export default function FreeConfigsPage() {
  return (
    <div className="h-[calc(100vh-64px)] w-full">
      <iframe
        src="/free-configs/panel"
        title="Free Configs"
        className="h-full w-full border-0"
      />
    </div>
  )
}
