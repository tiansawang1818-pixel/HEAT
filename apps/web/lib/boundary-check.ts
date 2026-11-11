import { adminPost } from "@/lib/api";

const CATALOG_URL = "https://data.go.th/api/3/action/package_show?id=663ed528-97d7-4dd2-a463-be865e6fda28";
const MESSAGE_TYPE = "heatshift-boundary-catalog";

/**
 * Read the official district boundary catalog from this browser.
 *
 * data.go.th blocks the hosting region and sends no CORS headers, but it
 * supports JSONP. The JSONP script runs in a sandboxed iframe with an opaque
 * origin, so it cannot reach this page, its cookies or the admin session; it
 * can only post the catalog back, and the server validates it again.
 */
export function fetchBoundaryCatalogInBrowser(timeoutMs = 20_000): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts");
    frame.style.display = "none";
    frame.srcdoc = `<script>function heatshiftCatalog(d){parent.postMessage({type:${JSON.stringify(MESSAGE_TYPE)},catalog:d},"*")}<\/script>`
      + `<script src="${CATALOG_URL}&callback=heatshiftCatalog"><\/script>`;
    const cleanup = () => { window.removeEventListener("message", onMessage); clearTimeout(timer); frame.remove(); };
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.contentWindow) return;
      const data = event.data as { type?: unknown; catalog?: unknown };
      if (data?.type !== MESSAGE_TYPE || !data.catalog || typeof data.catalog !== "object") return;
      cleanup();
      resolve(data.catalog as Record<string, unknown>);
    };
    const timer = setTimeout(() => { cleanup(); reject(new Error("อ่านแค็ตตาล็อก data.go.th จากเบราว์เซอร์นี้ไม่สำเร็จ")); }, timeoutMs);
    window.addEventListener("message", onMessage);
    document.body.appendChild(frame);
  });
}

export interface BoundaryBrowserCheckResult {
  status: string;
  current_version_confirmed: boolean;
  checked_at: string;
}

export async function confirmBoundariesFromBrowser(credential: string): Promise<BoundaryBrowserCheckResult> {
  const catalog = await fetchBoundaryCatalogInBrowser();
  return adminPost<BoundaryBrowserCheckResult>("/api/admin/boundary-check", credential, { catalog });
}
