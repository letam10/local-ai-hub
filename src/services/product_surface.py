"""Current product-surface composition facade.

V5 productization remains a compatibility implementation for durable-job
records.  New API composition imports this narrow facade so the transport no
longer treats the legacy module as a route registry or server authority.
"""

from __future__ import annotations

from typing import Any

from .api.v5_productization import project_product_surface as _project_product_surface


def project_product_surface(**kwargs: Any) -> dict[str, Any]:
    return _project_product_surface(**kwargs)


__all__ = ["project_product_surface"]
