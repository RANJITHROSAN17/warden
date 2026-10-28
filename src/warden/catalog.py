"""A small catalogue so the demo has facts to reason about.

Vendor age is the interesting field: a thirty-day-old mandate that only checks amounts will pay a
vendor created last week. Maturity is what stops an agent from being talked into a new payee.
"""

from __future__ import annotations

from warden.models import LineItem, Vendor

VENDORS: dict[str, Vendor] = {
    "V-ACME": Vendor(
        id="V-ACME", name="Acme Office Supply", age_days=1402, country="US", approved=True
    ),
    "V-NORTHWIND": Vendor(
        id="V-NORTHWIND", name="Northwind Paper Co", age_days=811, country="US", approved=False
    ),
    "V-FRESH": Vendor(
        id="V-FRESH", name="FreshCart Deals", age_days=12, country="US", approved=False
    ),
    "V-KITCHEN": Vendor(
        id="V-KITCHEN", name="Kitchen & Bar Supply", age_days=640, country="US", approved=False
    ),
}

PRODUCTS: dict[str, dict] = {
    "SKU-PAPER": {
        "description": "Printer paper, A4, 500 sheets",
        "category": "office_supplies",
        "unit_price": 7.5,
    },
    "SKU-TONER": {
        "description": "Laser printer toner cartridge, black",
        "category": "office_supplies",
        "unit_price": 89.0,
    },
    "SKU-DESK": {
        "description": "Sit-stand desk, electric",
        "category": "furniture",
        "unit_price": 320.0,
    },
    "SKU-ESPRESSO": {
        "description": "Espresso machine, commercial, 2-group",
        "category": "electronics",
        "unit_price": 480.0,
    },
    "SKU-PENS": {
        "description": "Gel pens, box of 12",
        "category": "office_supplies",
        "unit_price": 9.25,
    },
}


def item(sku: str, qty: int = 1) -> LineItem:
    product = PRODUCTS[sku]
    return LineItem(
        sku=sku,
        description=product["description"],
        category=product["category"],
        qty=qty,
        unit_price=product["unit_price"],
    )


def vendor(vendor_id: str) -> Vendor:
    return VENDORS[vendor_id]
