"""미국 양도소득세 연단위 네팅(손익통산) 테스트."""

import pytest

from src.momentum import us_capgains_tax

DED = 2_500_000   # 연 기본공제
R = 0.22


def test_single_year_above_deduction():
    # 차익 1000만 → (1000-250)만 × 22% = 165만
    tax = us_capgains_tax([("2025", 10_000_000)])
    assert tax == pytest.approx((10_000_000 - DED) * R)


def test_netting_gain_and_loss_same_year():
    # 같은 해 +800만, -300만 → net 500만 → (500-250)×22% = 55만
    tax = us_capgains_tax([("2025", 8_000_000), ("2025", -3_000_000)])
    assert tax == pytest.approx((5_000_000 - DED) * R)


def test_below_deduction_no_tax():
    assert us_capgains_tax([("2025", 2_000_000)]) == 0.0


def test_loss_year_no_tax():
    assert us_capgains_tax([("2025", -1_000_000)]) == 0.0


def test_per_year_deduction_applies_separately():
    # 2024 500만, 2025 500만 → 각 (500-250)×22%=55만, 합 110만 (공제가 연마다 적용)
    tax = us_capgains_tax([("2024", 5_000_000), ("2025", 5_000_000)])
    assert tax == pytest.approx(2 * (5_000_000 - DED) * R)
