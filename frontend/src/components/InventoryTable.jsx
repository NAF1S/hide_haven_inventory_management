import { useEffect, useState } from "react";
import { fetchInventory } from "../api";

export default function InventoryTable() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetchInventory()
      .then((data) => {
        if (!cancelled) {
          setItems(data);
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
        <p>Loading inventory…</p>
      </div>
    );
  }

  // ── Error state ──
  if (error) {
    return (
      <div className="state-message error">
        <p>⚠️ Failed to load inventory: {error}</p>
      </div>
    );
  }

  // ── Empty state ──
  if (items.length === 0) {
    return (
      <div className="state-message">
        <p>No inventory records found.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <h2>Inventory</h2>
        <span className="badge">{items.length} SKUs</span>
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>SKU</th>
              <th>Quantity</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const isLow = item.quantity <= 5;
              return (
                <tr key={item.id}>
                  <td>
                    <strong>{item.sku}</strong>
                  </td>
                  <td className={isLow ? "qty-low" : "qty-ok"}>
                    {item.quantity}
                  </td>
                  <td>
                    {isLow ? (
                      <span className="status-pill status-failed">
                        Low stock
                      </span>
                    ) : (
                      <span className="status-pill status-completed">
                        In stock
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
