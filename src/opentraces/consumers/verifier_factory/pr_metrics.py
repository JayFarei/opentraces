"""PR-space discrimination metrics for near-one-class verifier calibration (br/69 Exp 6).

Under near-one-class data, ROC-AUC is deceptive (the FPR axis is dominated by the large
positive class) and a point estimate on a handful of points is indefensible [Saito 2015,
Davis 2006, Chicco 2020, Card 2020]. This module reports **AUPRC + MCC with prevalence and a
bootstrap interval**, and is honest at the edges: with zero negatives MCC/AUPRC are
mathematically undefined, which is exactly the BLOCKED signal — never a fabricated number.

Pure-Python + numpy (sklearn/scipy absent). The PPI++ amplifier (br/69 Exp 1) lives in
:mod:`ppi` and is applied only after a human-ratified gold anchor exists.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def prevalence(y_true: list[int]) -> float:
    return round(float(np.mean(y_true)), 6) if y_true else 0.0


def auprc(y_true: list[int], scores: list[float]) -> float | None:
    """Average precision (area under the PR curve). None if a class is absent (undefined)."""
    y = np.asarray(y_true, dtype=float)
    s = np.asarray(scores, dtype=float)
    if y.size == 0 or y.sum() == 0 or y.sum() == y.size:
        return None  # all-one-class -> AUPRC undefined (BLOCKED)
    order = np.argsort(-s, kind="mergesort")
    y = y[order]
    tp = np.cumsum(y)
    fp = np.cumsum(1.0 - y)
    precision = tp / (tp + fp)
    recall = tp / y.sum()
    # average precision = sum over thresholds of (R_k - R_{k-1}) * P_k
    rec_prev = np.concatenate([[0.0], recall[:-1]])
    ap = float(np.sum((recall - rec_prev) * precision))
    return round(ap, 6)


def mcc(y_true: list[int], y_pred: list[int]) -> float | None:
    """Matthews correlation coefficient. None when a margin is zero (undefined = BLOCKED)."""
    y = np.asarray(y_true)
    p = np.asarray(y_pred)
    if y.size == 0:
        return None
    tp = int(np.sum((p == 1) & (y == 1)))
    tn = int(np.sum((p == 0) & (y == 0)))
    fp = int(np.sum((p == 1) & (y == 0)))
    fn = int(np.sum((p == 0) & (y == 1)))
    denom = float((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    if denom <= 0:
        return None  # a zero margin (e.g. no negatives) -> undefined
    return round((tp * tn - fp * fn) / (denom ** 0.5), 6)


def _bca_interval(samples: np.ndarray, theta_hat: float, jackknife: np.ndarray,
                  alpha: float) -> tuple[float, float]:
    """Bias-corrected and accelerated (BCa) interval [Efron]. Falls back to percentile."""
    from math import erf, sqrt

    def _ppf(q):  # inverse normal CDF via bisection on erf
        lo, hi = -8.0, 8.0
        for _ in range(100):
            mid = (lo + hi) / 2
            if 0.5 * (1 + erf(mid / sqrt(2))) < q:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def _cdf(z):
        return 0.5 * (1 + erf(z / sqrt(2)))

    samples = samples[~np.isnan(samples)]
    if samples.size < 2:
        return (float("nan"), float("nan"))
    prop = np.mean(samples < theta_hat)
    prop = min(max(prop, 1e-6), 1 - 1e-6)
    z0 = _ppf(prop)
    jk = jackknife[~np.isnan(jackknife)]
    jbar = np.mean(jk) if jk.size else theta_hat
    num = np.sum((jbar - jk) ** 3)
    den = 6.0 * (np.sum((jbar - jk) ** 2) ** 1.5)
    a = num / den if den != 0 else 0.0
    out = []
    for q in (alpha / 2, 1 - alpha / 2):
        zq = _ppf(q)
        adj = z0 + (z0 + zq) / (1 - a * (z0 + zq))
        out.append(float(np.quantile(samples, _cdf(adj))))
    return (round(min(out), 6), round(max(out), 6))


def bootstrap_ci(metric: str, y_true: list[int], scores: list[float], *,
                 n_boot: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float | None, float | None]:
    """BCa bootstrap interval for 'auprc' or 'mcc'. (nan,nan) if the metric is undefined."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y_true)
    s = np.asarray(scores, dtype=float)
    n = y.size

    def _stat(yy, ss):
        if metric == "auprc":
            return auprc(list(yy), list(ss))
        return mcc(list(yy), [1 if v >= 0.5 else 0 for v in ss])

    theta = _stat(y, s)
    if theta is None:
        return (float("nan"), float("nan"))
    boot_vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        v = _stat(y[idx], s[idx])
        boot_vals.append(np.nan if v is None else v)
    jack_vals = []
    for i in range(n):
        v = _stat(np.delete(y, i), np.delete(s, i))
        jack_vals.append(np.nan if v is None else v)
    return _bca_interval(np.array(boot_vals, dtype=float), float(theta),
                         np.array(jack_vals, dtype=float), alpha)


@dataclass(frozen=True)
class DiscriminationReport:
    n: int
    n_pos: int
    n_neg: int
    prevalence: float
    auprc: float | None
    auprc_ci: tuple[float | None, float | None]
    mcc: float | None
    mcc_ci: tuple[float | None, float | None]
    defined: bool
    note: str

    def to_dict(self) -> dict:
        return {
            "n": self.n, "n_pos": self.n_pos, "n_neg": self.n_neg, "prevalence": self.prevalence,
            "auprc": self.auprc, "auprc_ci": list(self.auprc_ci),
            "mcc": self.mcc, "mcc_ci": list(self.mcc_ci),
            "defined": self.defined, "note": self.note,
        }


def discrimination_report(y_true: list[int], scores: list[float], *, n_boot: int = 1000,
                          seed: int = 0) -> DiscriminationReport:
    """The honest PR-space discrimination summary. ``defined=False`` (note=BLOCKED reason)
    when a class is absent — AUPRC/MCC are undefined, never faked."""
    n = len(y_true)
    n_pos = int(sum(y_true))
    n_neg = n - n_pos
    if n_neg == 0 or n_pos == 0:
        return DiscriminationReport(n, n_pos, n_neg, prevalence(y_true), None, (None, None),
                                    None, (None, None), False,
                                    f"undefined: a class is absent (n_pos={n_pos}, n_neg={n_neg}) — BLOCKED")
    ap = auprc(y_true, scores)
    pred = [1 if v >= 0.5 else 0 for v in scores]
    m = mcc(y_true, pred)
    return DiscriminationReport(
        n, n_pos, n_neg, prevalence(y_true), ap, bootstrap_ci("auprc", y_true, scores, n_boot=n_boot, seed=seed),
        m, bootstrap_ci("mcc", y_true, scores, n_boot=n_boot, seed=seed), True,
        f"AUPRC/MCC over {n} points (prevalence {prevalence(y_true)})",
    )
