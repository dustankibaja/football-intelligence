"""Point-in-time research features and competing score models.

The CSV provider supplies dates, not verified kickoff times. Entire same-day
groups are withheld until the following day to prevent result leakage.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import groupby

from app.research.data import Match

DAY_SECONDS = 86400.0
POISSON_PSEUDO = 5.0
DECAY_HALF_LIFE = 180.0
DC_RHO = -0.08  # fixed research hypothesis, not calibrated on holdout


@dataclass(frozen=True, slots=True)
class FeatureRow:
    match: Match
    x: tuple[float, ...]
    probabilities: dict[str, tuple[float, float, float]]
    history_count: int
    home_venue_count: int
    away_venue_count: int

    @property
    def label(self) -> int:
        return self.match.outcome


def _poisson(k: int, rate: float) -> float:
    result = math.exp(-rate)
    for i in range(1, k + 1):
        result *= rate / i
    return result


def goal_probabilities(lh: float, la: float, rho: float = 0.0) -> tuple[float, float, float]:
    if not (math.isfinite(lh) and math.isfinite(la) and .1 <= lh <= 5 and .1 <= la <= 5):
        raise ValueError("Invalid expected goals")
    outcome = [0.0, 0.0, 0.0]
    total = 0.0
    for h in range(11):
        for a in range(11):
            tau = 1.0
            if h == 0 and a == 0:
                tau -= lh * la * rho
            elif h == 0 and a == 1:
                tau += lh * rho
            elif h == 1 and a == 0:
                tau += la * rho
            elif h == 1 and a == 1:
                tau -= rho
            p = _poisson(h, lh) * _poisson(a, la) * max(0.0, tau)
            outcome[0 if h > a else 1 if h == a else 2] += p
            total += p
    return tuple(x / total for x in outcome)


def _weighted_rate(matches: list[Match], value, baseline: float, day: date,
                   decay: bool, pseudo: float = POISSON_PSEUDO) -> float:
    numerator = baseline * pseudo
    denominator = pseudo
    for m in matches:
        weight = (2 ** (-(day - m.day).days / DECAY_HALF_LIFE)) if decay else 1.0
        numerator += weight * value(m)
        denominator += weight
    return numerator / denominator


def _league_mean(prior: list[Match], value, default: float, day: date,
                 decay: bool) -> float:
    if not prior:
        return default
    weights = [(2 ** (-(day - m.day).days / DECAY_HALF_LIFE)) if decay else 1.0
               for m in prior]
    return max(.35, sum(w * value(m) for w, m in zip(weights, prior)) / sum(weights))


def _goal_rates(prior: list[Match], match: Match,
                decay: bool) -> tuple[float, float, int, int, tuple[float, ...]]:
    day = match.day
    home_avg = _league_mean(prior, lambda m: m.home_goals, 1.45, day, decay)
    away_avg = _league_mean(prior, lambda m: m.away_goals, 1.15, day, decay)
    home = [m for m in prior if m.home == match.home]
    away = [m for m in prior if m.away == match.away]
    ha = _weighted_rate(home, lambda m: m.home_goals, home_avg, day, decay) / home_avg
    hd = _weighted_rate(home, lambda m: m.away_goals, away_avg, day, decay) / away_avg
    aa = _weighted_rate(away, lambda m: m.away_goals, away_avg, day, decay) / away_avg
    ad = _weighted_rate(away, lambda m: m.home_goals, home_avg, day, decay) / home_avg
    lh = min(4.0, max(.25, home_avg * ha * ad))
    la = min(4.0, max(.25, away_avg * aa * hd))
    return lh, la, len(home), len(away), (home_avg, away_avg, ha, hd, aa, ad)


def _form(prior: list[Match], team: str) -> tuple[float, float]:
    recent = [m for m in prior if team in (m.home, m.away)][-5:]
    if not recent:
        return .5, 21.0
    points = sum((3 if (m.outcome == 0 and m.home == team) or
                    (m.outcome == 2 and m.away == team) else
                  1 if m.outcome == 1 else 0) for m in recent)
    return points / (3 * len(recent)), float(len(recent))


def _rest(prior: list[Match], team: str, day: date) -> float:
    latest = next((m.day for m in reversed(prior) if team in (m.home, m.away)), None)
    return min(21.0, float((day - latest).days)) if latest else 21.0


def build_point_in_time_rows(matches: list[Match]) -> list[FeatureRow]:
    by_league: dict[str, list[Match]] = defaultdict(list)
    for match in matches:
        by_league[match.competition].append(match)
    rows: list[FeatureRow] = []
    for league, items in sorted(by_league.items()):
        ordered = sorted(items, key=lambda m: (m.day, m.home, m.away))
        prior: list[Match] = []
        elo: dict[str, float] = defaultdict(lambda: 1500.0)
        season = None
        for day, group_iter in groupby(ordered, key=lambda m: m.day):
            group = list(group_iter)
            if group[0].season != season:
                # Shrink past-season ratings to league mean before season begins.
                for team in elo:
                    elo[team] = 1500 + .75 * (elo[team] - 1500)
                season = group[0].season
            for m in group:
                base_lh, base_la, hn, an, rates = _goal_rates(prior, m, False)
                decay_lh, decay_la, _, _, _ = _goal_rates(prior, m, True)
                prior_outcomes = Counter(x.outcome for x in prior)
                n = len(prior)
                prior_p = tuple((prior_outcomes[i] + a) / (n + 10.0)
                                for i, a in enumerate((4.5, 2.7, 2.8)))
                base_p = goal_probabilities(base_lh, base_la)
                decay_p = goal_probabilities(decay_lh, decay_la)
                dc_p = goal_probabilities(decay_lh, decay_la, DC_RHO)
                home_form, _ = _form(prior, m.home)
                away_form, _ = _form(prior, m.away)
                features = (
                    (elo[m.home] - elo[m.away] + 70) / 400.0,
                    home_form - away_form,
                    rates[0], rates[1], rates[2], rates[3], rates[4], rates[5],
                    (_rest(prior, m.home, day) - _rest(prior, m.away, day)) / 21.0,
                    math.log1p(hn), math.log1p(an),
                    *base_p
                )
                rows.append(FeatureRow(m, features, {
                    "league_prior": prior_p,
                    "empirical_poisson": base_p,
                    "decay_poisson": decay_p,
                    "dixon_coles": dc_p
                }, n, hn, an))
            # Date-only evidence: no results from the same calendar date are
            # available to any match on that date, even if CSV row order differs.
            for m in group:
                eh = 1.0 / (1.0 + 10 ** (-(elo[m.home] + 70 - elo[m.away]) / 400.0))
                score = 1.0 if m.outcome == 0 else .5 if m.outcome == 1 else 0.0
                delta = 20 * (score - eh)
                elo[m.home] += delta
                elo[m.away] -= delta
            prior.extend(group)
    return sorted(rows, key=lambda r: (r.match.day, r.match.competition,
                                     r.match.home, r.match.away))


def selective_decision(row: FeatureRow, p: tuple[float, float, float]) -> str:
    """Research watchlist only, never a claim of positive betting EV."""
    if row.history_count < 60 or row.home_venue_count < 4 or row.away_venue_count < 4:
        return "ABSTAIN_INSUFFICIENT_HISTORY"
    top = sorted(enumerate(p), key=lambda x: x[1], reverse=True)
    if top[0][0] == 1 or top[0][1] <= .60 or top[0][1] - top[1][1] < .12 or p[1] > .28:
        return "ABSTAIN_UNCERTAIN"
    return "WATCH_ONLY_UNCALIBRATED"
