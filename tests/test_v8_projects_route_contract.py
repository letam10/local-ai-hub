"""Regression coverage for the installed creative-project list boundary."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from src.services.api.context import ApiContext
from src.services.api.router import ApiRequest
from src.services.api.routes.projects import list_projects


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


if __name__ == "__main__":
    unittest.main()
