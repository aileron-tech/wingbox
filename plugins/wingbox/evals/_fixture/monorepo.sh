#!/bin/bash
# Fixture for the eval cases: a small TypeScript and Python monorepo in the
# current (empty) workspace, committed to git. Called by each case's scaffold.sh.
set -euo pipefail
mkdir -p packages/core/src packages/billing/src packages/checkout/src services/api/app
cat > package.json <<'JSON'
{ "name": "shop", "private": true, "workspaces": ["packages/*"] }
JSON
cat > packages/core/package.json <<'JSON'
{ "name": "@shop/core", "version": "1.0.0", "main": "src/index.ts" }
JSON
cat > packages/billing/package.json <<'JSON'
{ "name": "@shop/billing", "version": "1.0.0", "dependencies": { "@shop/core": "1.0.0" } }
JSON
cat > packages/checkout/package.json <<'JSON'
{ "name": "@shop/checkout", "version": "1.0.0", "dependencies": { "@shop/core": "1.0.0", "@shop/billing": "1.0.0" } }
JSON
cat > packages/core/src/money.ts <<'TS'
export type Money = { amount: number; currency: string };

export function calculateTotal(prices: Money[]): Money {
  const amount = prices.reduce((sum, price) => sum + price.amount, 0);
  return { amount, currency: prices[0]?.currency ?? "EUR" };
}
TS
cat > packages/core/src/order.ts <<'TS'
import { Money } from "./money";

export type OrderLine = { sku: string; price: Money; quantity: number };
export type Order = { id: string; lines: OrderLine[] };
TS
cat > packages/core/src/index.ts <<'TS'
export * from "./money";
export * from "./order";
TS
cat > packages/billing/src/invoice.ts <<'TS'
import { calculateTotal, Order } from "@shop/core";

export function makeInvoice(order: Order) {
  const total = calculateTotal(order.lines.map((line) => ({ ...line.price, amount: line.price.amount * line.quantity })));
  return { orderId: order.id, total };
}
TS
cat > packages/checkout/src/checkout.ts <<'TS'
import { Order } from "@shop/core";
import { makeInvoice } from "@shop/billing/src/invoice";

export function checkout(order: Order) {
  return makeInvoice(order);
}
TS
cat > services/api/app/__init__.py <<'PY'
PY
cat > services/api/app/pricing.py <<'PY'
def apply_discount(amount: float, percent: float) -> float:
    return round(amount * (1 - percent / 100), 2)
PY
cat > services/api/app/routes.py <<'PY'
from app.pricing import apply_discount


def price_route(amount: float, code: str) -> dict:
    percent = 10.0 if code == "WELCOME" else 0.0
    return {"amount": apply_discount(amount, percent)}
PY
git init -q .
git add .
git -c user.name=eval -c user.email=eval@example.com commit -q -m "shop monorepo"
