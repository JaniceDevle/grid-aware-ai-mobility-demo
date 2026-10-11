from typing import Dict, List, Tuple

from app.models import GridSignal, GridPolicy


def _normalize(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def score_site(signal: GridSignal, policy: GridPolicy) -> float:
    """
    分数越高越优。
    renewable 越高越好，price 越低越好，carbon 越低越好。
    """
    price_norm = _normalize(signal.price, 0.0, policy.price_ref_max)
    carbon_norm = _normalize(signal.carbon_intensity, 0.0, policy.carbon_ref_max)
    return (
        policy.w_renewable * signal.renewable_ratio
        - policy.w_price * price_norm
        - policy.w_carbon * carbon_norm
    )


def select_target(
    signals: Dict[str, GridSignal],
    policy: GridPolicy,
    current_site: str,
) -> Tuple[str, float, List[dict]]:
    """
    返回 (best_site, best_score, decision_log)。
    如果所有站点都被约束排除，就留在原地。
    """
    log: List[dict] = []
    candidates: List[Tuple[str, float]] = []

    for site, sig in signals.items():
        excluded = False
        reason = ""
        if sig.carbon_intensity > policy.max_carbon:
            excluded = True
            reason = f"carbon {sig.carbon_intensity:.0f} > max {policy.max_carbon:.0f}"
        elif sig.renewable_ratio < policy.min_renewable:
            excluded = True
            reason = f"renewable {sig.renewable_ratio:.2f} < min {policy.min_renewable:.2f}"

        score = score_site(sig, policy)

        log.append({
            "site": site,
            "score": round(score, 4),
            "renewable_ratio": sig.renewable_ratio,
            "price": sig.price,
            "carbon_intensity": sig.carbon_intensity,
            "excluded": excluded,
            "reason": reason,
            "is_current": site == current_site,
        })

        if not excluded:
            candidates.append((site, score))

    if not candidates:
        return current_site, 0.0, log

    best_site, best_score = max(candidates, key=lambda x: x[1])
    return best_site, best_score, log


def select_model_variant(signal: GridSignal, policy: GridPolicy) -> str:
    if signal.carbon_intensity > policy.large_model_allowed_below_carbon:
        return "small"
    return "large"