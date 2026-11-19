"""Stream template mix bands (S2)."""

from __future__ import annotations

import pytest

from services.mock_splunk.stream import generate, load_templates


@pytest.mark.parametrize("count", [100, 200, 500])
def test_template_shares_seed42(count: int) -> None:
    items = generate(42, count)
    routine = sum(1 for item in items if item.pool == "routine")
    hitl = sum(1 for item in items if item.pool == "hitl")
    assert 0.75 <= routine / count <= 0.85
    assert 0.15 <= hitl / count <= 0.20
    uplifts = {template.uplift for template in load_templates() if template.uplift}
    assert "low->high" in uplifts and "medium->high" in uplifts
    hitl_templates = {item.template_id for item in items if item.pool == "hitl"}
    low_high = "hitl_low_high"
    med_high = "hitl_med_high"
    assert low_high in hitl_templates
    assert med_high in hitl_templates