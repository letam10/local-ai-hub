"""Regression coverage for the installed creative-project list boundary."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.routes.jobs import job_detail
from src.services.api.routes.projects import list_projects, project_export


class ProjectsRouteContractTests(unittest.TestCase):
    def test_list_projects_uses_composed_manager_object(self) -> None:
        manager = Mock()
        manager.list_projects.return_value = {
            "status": "completed",
            "projects": [],
            "recent_projects": [],
            "recovery": {"status": "clean"},
        }
        context = ApiContext({"project_manager": manager})

        response = list_projects(
            ApiRequest(method="GET", path="/api/projects", query={}, headers={}),
            context,
            {},
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["status"], "completed")
        manager.list_projects.assert_called_once_with()

    def test_project_export_missing_id_is_a_structured_not_found(self) -> None:
        manager = Mock()
        manager.export_project.side_effect = KeyError("missing")
        context = ApiContext({"project_manager": manager})

        response = project_export(
            ApiRequest(method="GET", path="/api/projects/missing/export", query={}, headers={}),
            context,
            {"project_id": "missing"},
        )

        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"status": "error", "error": "project_not_found"})

    def test_job_detail_missing_id_is_a_structured_not_found(self) -> None:
        context = ApiContext({"get_job": lambda _job_id: None})

        response = job_detail(
            ApiRequest(method="GET", path="/jobs/missing", query={}, headers={}),
            context,
            {"job_id": "missing"},
        )

        self.assertEqual(response.status, 404)
        self.assertEqual(response.payload, {"status": "error", "error": "job_not_found"})


if __name__ == "__main__":
    unittest.main()
