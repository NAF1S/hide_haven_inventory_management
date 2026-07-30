"""
app.py — FastAPI backend for WC Lite Sync

Receives signed WooCommerce order payloads, verifies HMAC SHA256, stores
orders in SQLite, and manages a simple inventory table.

Run: uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# =============================================================================
# Configuration
# =============================================================================

# The shared secret must match the one configured in the WC Lite Sync plugin.
# Set via environment variable on Render, or change the fallback default.
SHARED_SECRET: str = os.getenv("SHARED_SECRET", "change-me-to-a-random-string")

# SQLite database path.  On Render's free tier the disk is ephemeral, so
# data is lost on redeploy.  For persistence, mount a disk or switch to
# PostgreSQL and update DATABASE_URL accordingly.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///data.db")

# -----------------------------------------------------------------------------
# Logging — plain console output suitable for Render's log stream.
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger("wc-lite-sync")

# =============================================================================
# SQLAlchemy Engine & Session
# =============================================================================

# SQLite requires check_same_thread=False when used inside a threaded ASGI
# server like uvicorn.
_connect_args: Dict[str, Any] = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=False,  # set to True only for debugging SQL queries
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Enable WAL journal mode on SQLite for better read/write concurrency.
# This is safe on Render and avoids "database is locked" errors under
# modest load.
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Activate WAL mode when a new SQLite connection is opened."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.close()


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


# =============================================================================
# Database Models
# =============================================================================

class Order(Base):
    """
    Stores each WooCommerce order received by the backend.

    `wc_order_id` is unique — if the same order is sent again the row is
    updated rather than duplicated (idempotent processing).
    """

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wc_order_id = Column(Integer, unique=True, nullable=False, index=True)
    status = Column(String(50), nullable=False, default="")
    total = Column(String(20), nullable=False, default="")
    currency = Column(String(10), nullable=False, default="")
    customer_name = Column(String(200), nullable=False, default="")
    customer_phone = Column(String(50), nullable=False, default="")
    payload = Column(Text, nullable=False, default="{}")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class Inventory(Base):
    """
    Simple inventory table keyed by SKU.

    When a *new* order arrives, matching SKUs are decremented.  Quantity
    never drops below zero.  Unknown SKUs are silently skipped.
    """

    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(100), unique=True, nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)


# =============================================================================
# Pydantic Schemas
# =============================================================================

class OrderItemSchema(BaseModel):
    """Shape of a single line item inside the incoming JSON payload."""
    product_id: int
    sku: str
    name: str
    quantity: int
    total: str


class OrderPayloadSchema(BaseModel):
    """Full JSON payload sent by the WC Lite Sync WordPress plugin."""
    order_id: int
    status: str
    total: str
    currency: str
    customer: Dict[str, Any]
    items: List[OrderItemSchema]
    created_at: Optional[str] = None


class OrderResponse(BaseModel):
    """Response returned to the WordPress plugin after processing."""
    received: Optional[bool] = None   # True when a NEW order is inserted
    updated: Optional[bool] = None    # True when an EXISTING order is updated


class HealthResponse(BaseModel):
    """Response for the GET / health-check endpoint."""
    status: str
    orders_count: int


class OrderOut(BaseModel):
    """Serialized order row returned to the React UI (GET /api/orders)."""
    id: int
    wc_order_id: int
    status: str
    total: str
    currency: str
    customer_name: str
    customer_phone: str
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class InventoryOut(BaseModel):
    """Serialized inventory row returned to the React UI (GET /api/inventory)."""
    id: int
    sku: str
    quantity: int

    class Config:
        from_attributes = True


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="WC Lite Sync Backend",
    version="1.0.0",
    description="Receives signed WooCommerce orders and manages inventory.",
)

# Allow the React dev-server (and any frontend) to call the API endpoints.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Startup — Create Tables & Seed Sample Inventory
# =============================================================================

@app.on_event("startup")
def _on_startup():
    """
    Create all tables if they don't exist, then seed a handful of sample
    inventory rows so the decrement logic can be demonstrated right away.
    """
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ensured (created if missing).")

    # Seed sample inventory rows — only if the table is currently empty.
    with SessionLocal() as db:
        if db.query(Inventory).count() == 0:
            samples: List[Inventory] = [
                Inventory(sku="TSHIRT-BLUE", quantity=50),
                Inventory(sku="TSHIRT-RED", quantity=30),
                Inventory(sku="MUG-WHITE", quantity=100),
                Inventory(sku="HOODIE-BLACK", quantity=20),
            ]
            db.add_all(samples)
            db.commit()
            logger.info("Seeded %d sample inventory rows.", len(samples))


# =============================================================================
# Dependency — Database Session
# =============================================================================

def get_db() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy session and close it when the request finishes.

    Use this dependency in every endpoint that needs database access.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Dependency — HMAC Signature Verification
# =============================================================================

async def verify_hmac(request: Request) -> Dict[str, Any]:
    """
    Read the raw request body, verify the X-Signature header against
    HMAC-SHA256(body, SHARED_SECRET), and return the parsed JSON dict.

    Raises:
        HTTP 401 — if the X-Signature header is missing or invalid.
        HTTP 400 — if the body is not valid JSON.
    """
    body: bytes = await request.body()

    # -- Check for presence of the signature header --
    signature: Optional[str] = request.headers.get("X-Signature")
    if not signature:
        logger.warning("Missing X-Signature header from %s", request.client)
        raise HTTPException(status_code=401, detail="Missing X-Signature header")

    # -- Compute the expected HMAC --
    expected: str = hmac.new(
        SHARED_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    # -- Constant-time comparison to prevent timing attacks --
    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "Invalid signature from %s  (expected prefix %s…, got %s…)",
            request.client,
            expected[:12],
            signature[:12],
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    # -- Parse body as JSON now that it's trusted --
    try:
        data: Dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Malformed JSON body from %s", request.client)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    return data


# =============================================================================
# Helper — Inventory Decrement
# =============================================================================

def _decrement_inventory(db: Session, items: List[OrderItemSchema]) -> None:
    """
    For each item in the order, look up its SKU in the inventory table
    and reduce the quantity (never below zero).  SKUs not present in the
    inventory table are silently skipped — they do NOT create new rows.
    """
    for item in items:
        sku: str = (item.sku or "").strip()
        if not sku:
            continue  # skip items with blank SKUs

        inv: Optional[Inventory] = (
            db.query(Inventory).filter(Inventory.sku == sku).first()
        )
        if inv is None:
            logger.debug("SKU %r not found in inventory — skipped.", sku)
            continue

        old_qty: int = inv.quantity
        inv.quantity = max(0, old_qty - item.quantity)

        logger.info(
            "Inventory  sku=%s  %d → %d  (change: -%d)",
            sku,
            old_qty,
            inv.quantity,
            item.quantity,
        )


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    """
    Health-check endpoint — Render pings this at /health.

    Returns basic status and the number of orders currently stored.
    """
    count: int = db.query(Order).count()
    return HealthResponse(status="ok", orders_count=count)


@app.post("/orders", response_model=OrderResponse)
def receive_order(
    data: Dict[str, Any] = Depends(verify_hmac),
    db: Session = Depends(get_db),
) -> OrderResponse:
    """
    Receive a signed order from the WooCommerce WC Lite Sync plugin.

    Flow:
    1. `verify_hmac` dependency reads the raw body, checks the HMAC
       signature, and returns the parsed JSON.
    2. The JSON is validated against OrderPayloadSchema.
    3. If `wc_order_id` already exists → update the row (idempotent).
       If not → insert a new row AND decrement inventory for each item.
    4. Returns {"received": true} for new orders, {"updated": true}
       for existing orders.
    """
    # -- Validate incoming shape against Pydantic --
    try:
        payload = OrderPayloadSchema(**data)
    except Exception as exc:
        logger.warning("Payload validation failed: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))

    wc_order_id: int = payload.order_id
    is_new: bool = False

    # ---------- Upsert logic ----------
    existing: Optional[Order] = (
        db.query(Order).filter(Order.wc_order_id == wc_order_id).first()
    )

    if existing:
        # ---- Update an existing order (idempotent) ----
        existing.status = payload.status
        existing.total = payload.total
        existing.currency = payload.currency
        existing.customer_name = (
            payload.customer.get("name", "")
            if isinstance(payload.customer, dict)
            else ""
        )
        existing.customer_phone = (
            payload.customer.get("phone", "")
            if isinstance(payload.customer, dict)
            else ""
        )
        existing.payload = json.dumps(data, ensure_ascii=False)
        db.add(existing)
        db.commit()
        logger.info("Order #%d  UPDATED  (status=%s)", wc_order_id, payload.status)
        return OrderResponse(updated=True)

    # ---- Insert a brand-new order ----
    order = Order(
        wc_order_id=wc_order_id,
        status=payload.status,
        total=payload.total,
        currency=payload.currency,
        customer_name=(
            payload.customer.get("name", "")
            if isinstance(payload.customer, dict)
            else ""
        ),
        customer_phone=(
            payload.customer.get("phone", "")
            if isinstance(payload.customer, dict)
            else ""
        ),
        payload=json.dumps(data, ensure_ascii=False),
        created_at=(
            datetime.fromisoformat(payload.created_at)
            if payload.created_at
            else datetime.now(timezone.utc)
        ),
    )
    db.add(order)
    db.commit()
    is_new = True
    logger.info("Order #%d  CREATED  (status=%s)", wc_order_id, payload.status)

    # ---------- Inventory update (new orders only) ----------
    if is_new:
        _decrement_inventory(db, payload.items)
        db.commit()

    return OrderResponse(received=True)


# =============================================================================
# UI API Endpoints (read-only, no HMAC — consumed by the React dashboard)
# =============================================================================

@app.get("/api/orders", response_model=List[OrderOut])
def list_orders(db: Session = Depends(get_db)) -> List[OrderOut]:
    """Return all orders, newest first."""
    orders: List[Order] = (
        db.query(Order).order_by(Order.created_at.desc()).all()
    )
    return [
        OrderOut(
            id=o.id,
            wc_order_id=o.wc_order_id,
            status=o.status,
            total=o.total,
            currency=o.currency,
            customer_name=o.customer_name,
            customer_phone=o.customer_phone,
            created_at=o.created_at.isoformat() if o.created_at else None,
        )
        for o in orders
    ]


@app.get("/api/inventory", response_model=List[InventoryOut])
def list_inventory(db: Session = Depends(get_db)) -> List[InventoryOut]:
    """Return all inventory rows, sorted alphabetically by SKU."""
    items: List[Inventory] = (
        db.query(Inventory).order_by(Inventory.sku).all()
    )
    return [
        InventoryOut(id=i.id, sku=i.sku, quantity=i.quantity)
        for i in items
    ]


# =============================================================================
# Serve the React SPA in production (only when the build exists)
# =============================================================================

_FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "frontend", "dist")

if os.path.isdir(_FRONTEND_DIST):
    # Serve static assets (JS, CSS, images) from the Vite build output.
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="frontend_assets",
    )

    # Serve the React dashboard at the root URL.
    @app.get("/")
    async def serve_index():
        """Serve the React dashboard at the root URL."""
        return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))

    # Catch-all: serve index.html for any unmatched path so client-side
    # routing (e.g. React Router) works after page reloads.
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for any non-API path."""
        if full_path.startswith("api/") or full_path == "health":
            raise HTTPException(status_code=404, detail="Not found")

        index_path = os.path.join(_FRONTEND_DIST, "index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Frontend not built")


# =============================================================================
# Local development entry point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    port: int = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
