"""Milestone completion drives project progress."""

from superboss.core.security import utcnow
from superboss.modules.projects.models import Project, ProjectMilestone
from superboss.modules.projects.service import _sync_progress


def test_progress_is_the_completed_milestone_ratio() -> None:
    project = Project(name="星野合作")
    project.milestones = [
        ProjectMilestone(title="立项", sort_order=0, done_at=utcnow()),
        ProjectMilestone(title="交付", sort_order=1, done_at=None),
    ]
    _sync_progress(project)
    assert project.progress_percent == 50


def test_progress_stays_put_without_milestones() -> None:
    project = Project(name="空项目", progress_percent=12)
    project.milestones = []
    _sync_progress(project)
    assert project.progress_percent == 12
