/**
 * Thin wrapper around fetch() for calling the FastAPI backend.
 *
 * In development Vite proxies /api → http://localhost:8000.
 * In production the built files are served by FastAPI itself, so relative
 * URLs work without any change.
 */

const API_BASE = "/api";

/**
 * Generic GET helper.  Throws on non-OK responses so callers can catch.
 */
async function get(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`GET ${path} failed with status ${res.status}`);
  }
  return res.json();
}

/** Fetch all orders (newest first). */
export function fetchOrders() {
  return get("/orders");
}

/** Fetch all inventory rows. */
export function fetchInventory() {
  return get("/inventory");
}
