import { useEffect, useState } from "react";
import { fetchOrders } from "../api";

/**
 * Returns a CSS class name for the status pill based on the order status.
 */
function statusClass(status) {
  const map = {
    completed: "status-completed",
    processing: "status-processing",
    pending: "status-pending",
    failed: "status-failed",
    cancelled: "status-failed",
    "on-hold": "status-on-hold",
    refunded: "status-refunded",
  };
  return map[status] || "";
}

/**
 * Format an ISO date string into a human-readable local format.
 */
function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function OrdersTable() {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchOrders()
      .then((data) => {
        if (!cancelled) {
          setOrders(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Loading state ──
  if (loading) {
    return (
      <div className="state-message">
        <div className="spinner" />
        <p>Loading orders…</p>
      </div>
    );
  }

  // ── Error state ──
  if (error) {
    return (
      <div className="state-message error">
        <p>⚠️ Failed to load orders: {error}</p>
      </div>
    );
  }

  // ── Empty state ──
  if (orders.length === 0) {
    return (
      <div className="state-message">
        <p>No orders received yet. Orders will appear here once the WooCommerce plugin syncs them.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Orders</h2>
        <span className="badge">{orders.length} total</span>
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>WC Order #</th>
              <th>Status</th>
              <th>Total</th>
              <th>Customer</th>
              <th>Phone</th>
              <th>Received</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id}>
                <td>
                  <strong>#{o.wc_order_id}</strong>
                </td>
                <td>
                  <span className={`status-pill ${statusClass(o.status)}`}>
                    {o.status}
                  </span>
                </td>
                <td>
                  {o.total} {o.currency}
                </td>
                <td>{o.customer_name || "—"}</td>
                <td>{o.customer_phone || "—"}</td>
                <td>{formatDate(o.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
