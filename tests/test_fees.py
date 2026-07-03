"""수수료·세금 프로파일 테스트."""

import pytest

from src import fees


def test_kr_stock_sell_tax():
    prof = fees.get_profile("kr_stock")
    c = fees.sell_costs(prof, gross_value=1_000_000, invested=900_000)
    assert c["sell_tax"] == pytest.approx(2000)    # 0.20%
    assert c["cap_gains_tax"] == 0
    assert c["total"] == pytest.approx(2000)


def test_kr_etf_is_tax_free():
    prof = fees.get_profile("kr_etf")
    c = fees.sell_costs(prof, gross_value=1_000_000, invested=500_000)
    assert c["total"] == 0                         # 거래세·양도세 모두 면제


def test_us_capital_gains_with_deduction():
    prof = fees.get_profile("us_stock")
    # gain $9000, 공제 2.5M/1000=$2500 → 과세 $6500 × 22% = $1430
    c = fees.sell_costs(prof, gross_value=10_000, invested=1_000, fx_krw=1000)
    assert c["sell_tax"] == 0
    assert c["cap_gains_tax"] == pytest.approx(1430)


def test_us_capital_gains_under_deduction_is_zero():
    prof = fees.get_profile("us_stock")
    # gain $1000 < 공제 $2500 → 양도세 0
    c = fees.sell_costs(prof, gross_value=2_000, invested=1_000, fx_krw=1000)
    assert c["cap_gains_tax"] == 0


def test_unknown_asset_class_raises():
    with pytest.raises(ValueError):
        fees.get_profile("crypto")
