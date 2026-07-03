"""매매 수수료·세금 프로파일 (2026년 기준, 단순화).

자산 종류별로 매수 수수료 / 매도 거래세 / 양도소득세를 정의한다.
백테스트에서 '세후 실수령 수익률'을 계산하는 데 쓴다.

근거(2026):
- 국내 주식: 매도 시 증권거래세 0.20%(농특세 포함). 상장주식 양도차익은 개인 비과세.
- 국내 주식형 ETF: 증권거래세 면제 + 매매차익 비과세 → 매도세 0.
- 미국 주식: 매도 거래세 없음. 양도소득세 22%(연 250만원 기본공제 후).
- 토스 매수 수수료(실 API): 국내 0%(프로모션), 미국 0.1%.

값은 호출부에서 덮어쓸 수 있다(프로모션 종료/세법 변경 대비).
"""

from __future__ import annotations

# 미국 양도소득세 연간 기본공제 (원화)
US_CAPGAINS_DEDUCTION_KRW = 2_500_000

PROFILES: dict[str, dict] = {
    "kr_stock": {
        "buy_commission": 0.0,      # 토스 프로모션 0%
        "sell_tax": 0.0020,         # 증권거래세 0.20%
        "cap_gains": 0.0,           # 상장주식 양도차익 비과세
    },
    "kr_etf": {
        "buy_commission": 0.0,
        "sell_tax": 0.0,            # 주식형 ETF 거래세 면제
        "cap_gains": 0.0,           # 국내주식형 ETF 매매차익 비과세
    },
    "us_stock": {
        "buy_commission": 0.001,    # 0.1%
        "sell_tax": 0.0,
        "cap_gains": 0.22,          # 양도소득세 22%
        "deduction_krw": US_CAPGAINS_DEDUCTION_KRW,
    },
}


def get_profile(asset_class: str, overrides: dict | None = None) -> dict:
    if asset_class not in PROFILES:
        raise ValueError(f"알 수 없는 asset_class: {asset_class} (가능: {list(PROFILES)})")
    prof = dict(PROFILES[asset_class])
    if overrides:
        prof.update(overrides)
    return prof


def sell_costs(profile: dict, gross_value: float, invested: float,
               fx_krw: float | None = None) -> dict:
    """청산(전량 매도) 시 발생하는 매도세 + 양도세 추정.

    gross_value/invested 는 자산의 거래통화 기준(미국=USD, 국내=KRW).
    fx_krw: 미국 양도세 공제(원화)를 거래통화로 환산하는 데 사용.
    """
    tax_trade = gross_value * profile.get("sell_tax", 0.0)

    gain = gross_value - invested
    cap_rate = profile.get("cap_gains", 0.0)
    tax_capgains = 0.0
    if cap_rate > 0 and gain > 0:
        deduction = 0.0
        ded_krw = profile.get("deduction_krw", 0)
        if ded_krw and fx_krw:
            deduction = ded_krw / fx_krw         # 원화 공제 → 거래통화 환산
        taxable = max(0.0, gain - deduction)
        tax_capgains = taxable * cap_rate

    return {
        "sell_tax": tax_trade,
        "cap_gains_tax": tax_capgains,
        "total": tax_trade + tax_capgains,
    }
