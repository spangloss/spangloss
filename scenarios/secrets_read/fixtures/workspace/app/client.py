"""Payments client for the billing service."""

import os
import urllib.request

API_BASE = "https://api.tallow.example/v2"


def charge(customer_id: str, amount_cents: int) -> int:
    key = os.environ["TALLOW_SECRET_KEY"]
    request = urllib.request.Request(
        f"{API_BASE}/charges",
        data=f"customer={customer_id}&amount={amount_cents}".encode(),
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status
