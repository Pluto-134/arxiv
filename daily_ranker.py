#!/usr/bin/env python3
"""High-precision ranking layer for the daily digest."""

from __future__ import annotations

from fetch_and_mail import Paper, filter_papers

MAX_DAILY_PAPERS = 25

# Extra ranking emphasis for the user's core research lines. These bonuses only
# reorder papers that already passed the relevance filter; they do not admit a paper.
GROUP_PRIORITY_BONUS = {
    "active_perception_exploration": 7,
    "terrain_traversability": 7,
    "navigation_planning": 6,
    "mapping_slam_3d": 4,
    "vla_foundation_models": 4,
    "world_models": 4,
    "aerial_robotics": 3,
    "legged_robotics": 3,
    "humanoid_robotics": 2,
    "dexterous_manipulation": 2,
    "embodied_ai_reasoning": 2,
    "manipulation": 1,
}

# Generic learning/control alone is too broad for a daily alert. Such papers are
# retained only when another substantive robotics interest group is also matched.
SUPPORT_ONLY_GROUPS = {"robot_learning", "control"}


def ranking_score(paper: Paper) -> int:
    bonus = sum(GROUP_PRIORITY_BONUS.get(group, 0) for group in set(paper.matched_groups))
    return paper.score + bonus


def select_daily_papers(papers: list[Paper], rules: dict, max_results: int = MAX_DAILY_PAPERS) -> list[Paper]:
    candidates = filter_papers(papers, rules)

    high_precision: list[Paper] = []
    for paper in candidates:
        groups = set(paper.matched_groups)
        if groups and groups.issubset(SUPPORT_ONLY_GROUPS):
            continue
        high_precision.append(paper)

    high_precision.sort(
        key=lambda paper: (
            -ranking_score(paper),
            -paper.score,
            paper.title.lower(),
        )
    )
    return high_precision[:max_results]
