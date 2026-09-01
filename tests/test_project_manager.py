import pytest

from pipeline.errors import DuplicateProjectError
from pipeline.project_manager import PROJECT_SUBFOLDERS, ProjectManager


class FakeDriveClient:
    """In-memory fake standing in for the real Drive API — enough behavior to
    exercise ProjectManager's folder-tree logic without any network access."""

    def __init__(self):
        self._folders = {}  # (name, parent_id) -> id
        self._next_id = 1

    def _new_id(self):
        fid = f"id{self._next_id}"
        self._next_id += 1
        return fid

    def find_folder(self, name, parent_id):
        fid = self._folders.get((name, parent_id))
        return {"id": fid, "name": name} if fid else None

    def create_folder(self, name, parent_id):
        fid = self._new_id()
        self._folders[(name, parent_id)] = fid
        return {"id": fid, "name": name}

    def ensure_folder(self, name, parent_id):
        existing = self.find_folder(name, parent_id)
        return existing if existing else self.create_folder(name, parent_id)

    def list_subfolders(self, parent_id):
        return [
            {"id": fid, "name": name}
            for (name, pid), fid in self._folders.items()
            if pid == parent_id
        ]


def test_create_project_builds_full_folder_tree():
    client = FakeDriveClient()
    manager = ProjectManager(client=client)
    manager.create_project("YouTube_003")

    root = client.find_folder("Content Creation", None)
    assert root is not None
    projects = client.find_folder("Projects", root["id"])
    assert projects is not None
    archive = client.find_folder("Archive", root["id"])
    assert archive is not None
    project = client.find_folder("YouTube_003", projects["id"])
    assert project is not None
    for subfolder in PROJECT_SUBFOLDERS:
        assert client.find_folder(subfolder, project["id"]) is not None


def test_create_project_duplicate_name_raises():
    client = FakeDriveClient()
    manager = ProjectManager(client=client)
    manager.create_project("YouTube_003")
    with pytest.raises(DuplicateProjectError):
        manager.create_project("YouTube_003")


def test_create_project_does_not_recreate_base_structure_on_second_project():
    client = FakeDriveClient()
    manager = ProjectManager(client=client)
    manager.create_project("Project_A")
    manager.create_project("Project_B")

    root = client.find_folder("Content Creation", None)
    # ensure_folder should have found-not-recreated Content Creation/Projects/Archive
    # both times — only one root folder should exist.
    matching_roots = [k for k in client._folders if k[0] == "Content Creation"]
    assert len(matching_roots) == 1
    projects = client.find_folder("Projects", root["id"])
    assert client.find_folder("Project_A", projects["id"]) is not None
    assert client.find_folder("Project_B", projects["id"]) is not None


def test_list_projects_returns_sorted_names():
    client = FakeDriveClient()
    manager = ProjectManager(client=client)
    manager.create_project("Zebra")
    manager.create_project("Apple")
    manager.create_project("Mango")
    assert manager.list_projects() == ["Apple", "Mango", "Zebra"]


def test_list_projects_empty_when_none_created():
    client = FakeDriveClient()
    manager = ProjectManager(client=client)
    assert manager.list_projects() == []
