import { useState } from "react";
import OrdersTable from "./components/OrdersTable";
import InventoryTable from "./components/InventoryTable";

const TABS = [
  { key: "orders", label: "📦 Orders" },
  { key: "inventory", label: "📋 Inventory" },
];

export default function App() {
  const [activeTab, setActiveTab] = useState("orders");

  return (
    <div className="app">
      <header className="app-header">
        <h1>WC Lite Sync</h1>
        <span className="app-subtitle">Dashboard</span>
      </header>

      {/* ── Tab navigation ── */}
      <nav className="tabs">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            className={`tab-btn ${activeTab === tab.key ? "active" : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* ── Tab content ── */}
      <main className="tab-content">
        {activeTab === "orders" && <OrdersTable />}
        {activeTab === "inventory" && <InventoryTable />}
      </main>
    </div>
  );
}
