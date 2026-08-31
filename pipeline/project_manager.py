"""Folder-tree logic for the Content Creation pipeline.

Structure managed in My Drive:

    Content Creation/
      Projects/
        <ProjectName>/
          Original/
          Proxy/
          Audio/
          Resolve/
      Archive/
"""

from pipeline.drive_client import DriveClient
from pipeline.errors import DuplicateProjectError

ROOT_FOLDER_NAME = "Content Creation"
PROJECTS_FOLDER_NAME = "Projects"
ARCHIVE_FOLDER_NAME = "Archive"
PROJECT_SUBFOLDERS = ["Original", "Proxy", "Audio", "Resolve"]


class ProjectManager:
    def __init__(self, client: DriveClient | None = None):
        self._client = client or DriveClient()

    def _ensure_base_structure(self) -> dict:
        """Ensure Content Creation/Projects and Content Creation/Archive exist.

        Returns the Projects folder's metadata.
        """
        root = self._client.ensure_folder(ROOT_FOLDER_NAME, parent_id=None)
        self._client.ensure_folder(ARCHIVE_FOLDER_NAME, parent_id=root["id"])
        projects = self._client.ensure_folder(PROJECTS_FOLDER_NAME, parent_id=root["id"])
        return projects

    def create_project(self, project_name: str) -> str:
        """Create a new project's folder set. Returns the project folder's Drive id.

        Raises DuplicateProjectError if a project with this name already exists.
        """
        projects_folder = self._ensure_base_structure()

        existing = self._client.find_folder(project_name, parent_id=projects_folder["id"])
        if existing:
            raise DuplicateProjectError(
                f"A project named '{project_name}' already exists in Drive."
            )

        project_folder = self._client.create_folder(project_name, parent_id=projects_folder["id"])
        for subfolder_name in PROJECT_SUBFOLDERS:
            self._client.create_folder(subfolder_name, parent_id=project_folder["id"])

        return project_folder["id"]

    def list_projects(self) -> list[str]:
        """Return the names of all existing projects, sorted alphabetically."""
        projects_folder = self._ensure_base_structure()
        folders = self._client.list_subfolders(projects_folder["id"])
        return sorted(f["name"] for f in folders)
