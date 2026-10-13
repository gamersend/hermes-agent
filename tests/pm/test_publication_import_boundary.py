"""Plugin metadata publication must run without application authentication imports."""
from __future__ import annotations

import json

from pm.plugin_inputs import StagedUpdate
from pm.store import tree_digest
from tests.pm._fixtures import client, isolated_python, worker_toolchain  # noqa: F401
from tests.pm.test_worker import _current_environment


def test_plugin_publication_does_not_import_application_auth(client, tmp_path, monkeypatch, isolated_python):
    worker_toolchain(client, monkeypatch, isolated_python, injection='''
class ApplicationAuthUnavailable:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "hermes_cli.auth":
            raise RuntimeError("PM publication imported application authentication")
        return None
sys.meta_path.insert(0, ApplicationAuthUnavailable())
''')
    _current_environment(tmp_path, monkeypatch, [])
    home = tmp_path / "home"
    target = home / "plugins" / "example"
    target.mkdir(parents=True)
    (home / "config.yaml").write_text("plugins:\n  enabled: []\n", encoding="utf-8")
    (target / "plugin.yaml").write_text("name: example\n", encoding="utf-8")
    (target / "code.py").write_text("old code", encoding="utf-8")
    metadata = target.parent / ".install-metadata.json"
    metadata.write_text('{"example":{"revision":"old"}}\n', encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "plugin.yaml").write_text("name: example\n", encoding="utf-8")
    (staged / "code.py").write_text("new code", encoding="utf-8")

    client.sync_venv(explicit=True, plugins=StagedUpdate({
        "staged": str(staged), "target": str(target), "target_digest": tree_digest(target),
        "old_metadata": {"example": {"revision": "old"}},
        "new_metadata": {"example": {"revision": "new"}},
    }))

    assert (target / "code.py").read_text(encoding="utf-8") == "new code"
    assert json.loads(metadata.read_text(encoding="utf-8"))["example"]["revision"] == "new"
    assert not staged.exists()
