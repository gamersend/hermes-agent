"""Hermes must not consume another application's rotating Claude login."""
import time
from unittest.mock import Mock
import pytest
from agent import anthropic_adapter as adapter
from agent import credential_pool as pool_mod

@pytest.fixture
def isolated_sources(monkeypatch):
    for name in ("ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    borrowed = Mock(side_effect=AssertionError("interactive Claude store was read"))
    monkeypatch.setattr(adapter, "read_claude_code_credentials", borrowed)
    return borrowed

def credential(source, *, expired=False):
    return pool_mod.PooledCredential(
        provider="anthropic", id=source, label=source, auth_type="oauth",
        priority=0, source=source, access_token="fixture-access",
        refresh_token="fixture-refresh",
        expires_at_ms=0 if expired else int(time.time() * 1000) + 3600000,
    )

def test_runtime_resolution_does_not_read_interactive_claude(isolated_sources):
    assert adapter.resolve_anthropic_token() is None
    isolated_sources.assert_not_called()

@pytest.mark.parametrize("name", ["ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"])
def test_explicit_host_credential_is_used_without_borrowing(monkeypatch, isolated_sources, name):
    monkeypatch.setenv(name, "fixture-host-credential")
    assert adapter.resolve_anthropic_token() == "fixture-host-credential"
    isolated_sources.assert_not_called()

def test_singleton_seeding_keeps_hermes_login_separate(monkeypatch, isolated_sources):
    from hermes_cli import auth
    monkeypatch.setattr(auth, "is_provider_explicitly_configured", lambda provider: True)
    monkeypatch.setattr(pool_mod, "_load_auth_store", lambda: {})
    monkeypatch.setattr(pool_mod, "load_env", lambda: {})
    monkeypatch.setattr(pool_mod, "_get_secret", lambda *args: "")
    monkeypatch.setattr(adapter, "read_hermes_oauth_credentials", lambda: {
        "accessToken": "fixture-own-access", "refreshToken": "fixture-own-refresh",
        "expiresAt": int(time.time() * 1000) + 3600000,
    })
    entries = []
    changed, sources = pool_mod._seed_from_singletons("anthropic", entries)
    assert changed
    assert sources == {"hermes_pkce"}
    assert [entry.source for entry in entries] == ["hermes_pkce"]
    isolated_sources.assert_not_called()

def test_persisted_borrowed_login_is_not_selectable(monkeypatch, isolated_sources):
    pool = pool_mod.CredentialPool("anthropic", [credential("claude_code")])
    monkeypatch.setattr(pool, "_persist", lambda: None)
    assert not pool.has_credentials()
    assert pool.select() is None
    assert pool.entries()[0].source == "claude_code"
    isolated_sources.assert_not_called()

def test_borrowed_login_cannot_refresh_or_write(monkeypatch, isolated_sources):
    entry = credential("claude_code", expired=True)
    pool = pool_mod.CredentialPool("anthropic", [entry])
    monkeypatch.setattr(pool, "_persist", lambda: None)
    refresh = Mock(side_effect=AssertionError("borrowed token was refreshed"))
    write = Mock(side_effect=AssertionError("Claude credential store was written"))
    monkeypatch.setattr(adapter, "refresh_anthropic_oauth_pure", refresh)
    monkeypatch.setattr(adapter, "_write_claude_code_credentials", write)
    assert pool._refresh_entry(entry, force=True) is None
    refresh.assert_not_called()
    write.assert_not_called()
    isolated_sources.assert_not_called()

def test_hermes_owned_login_can_refresh_without_touching_claude(monkeypatch, isolated_sources):
    entry = credential("hermes_pkce", expired=True)
    pool = pool_mod.CredentialPool("anthropic", [entry])
    monkeypatch.setattr(pool, "_persist", lambda: None)
    refresh = Mock(return_value={
        "access_token": "fixture-new-own-access", "refresh_token": "fixture-new-own-refresh",
        "expires_at_ms": int(time.time() * 1000) + 3600000,
    })
    write = Mock(side_effect=AssertionError("Claude credential store was written"))
    monkeypatch.setattr(adapter, "refresh_anthropic_oauth_pure", refresh)
    monkeypatch.setattr(adapter, "_write_claude_code_credentials", write)
    updated = pool._refresh_entry(entry, force=True)
    assert updated.access_token == "fixture-new-own-access"
    refresh.assert_called_once_with("fixture-refresh", use_json=True)
    write.assert_not_called()
    isolated_sources.assert_not_called()


def test_legacy_reader_never_opens_claude_store(monkeypatch):
    keychain = Mock(side_effect=AssertionError("keychain was read"))
    monkeypatch.setattr(adapter, "_read_claude_code_credentials_from_keychain", keychain)
    assert adapter.read_claude_code_credentials() is None
    keychain.assert_not_called()


def test_legacy_refresh_never_consumes_borrowed_refresh_token(monkeypatch):
    refresh = Mock(side_effect=AssertionError("borrowed refresh token was consumed"))
    monkeypatch.setattr(adapter, "refresh_anthropic_oauth_pure", refresh)
    assert adapter._refresh_oauth_token({"accessToken": "fixture-access", "refreshToken": "fixture-refresh", "expiresAt": 0}) is None
    refresh.assert_not_called()


def test_legacy_writer_refuses_and_preserves_claude_file(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.Path, "home", lambda: tmp_path)
    credential_file = tmp_path / ".claude" / ".credentials.json"
    credential_file.parent.mkdir()
    credential_file.write_text('{"fixture":"unchanged"}')
    with pytest.raises(RuntimeError, match="Claude Code"):
        adapter._write_claude_code_credentials("fixture-access", "fixture-refresh", 0)
    assert credential_file.read_text() == '{"fixture":"unchanged"}'
