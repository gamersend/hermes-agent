"""Explicit Anthropic host credentials do not depend on Claude Code's login."""
from unittest.mock import Mock
import pytest
from hermes_cli.config import save_env_value


@pytest.fixture
def isolated_model_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    save_env_value("ANTHROPIC_TOKEN", "")
    save_env_value("ANTHROPIC_API_KEY", "")
    external_store = Mock(side_effect=AssertionError("Claude Code store must not be read"))
    oauth_setup = Mock(return_value=False)
    model_picker = Mock(return_value=None)
    monkeypatch.setattr("agent.anthropic_adapter.read_claude_code_credentials", external_store)
    monkeypatch.setattr("hermes_cli.main._run_anthropic_oauth_flow", oauth_setup)
    monkeypatch.setattr("hermes_cli.auth._prompt_model_selection", model_picker)
    return external_store, oauth_setup, model_picker


@pytest.mark.parametrize("variable", ["ANTHROPIC_TOKEN", "ANTHROPIC_API_KEY"])
def test_operator_can_reuse_explicit_host_credential(variable, isolated_model_flow, monkeypatch, capsys):
    from hermes_cli.main import _model_flow_anthropic
    external_store, oauth_setup, model_picker = isolated_model_flow
    save_env_value(variable, "sk-ant-oat01-fixture-host-credential")
    monkeypatch.setattr("hermes_cli.model_setup_flows._prompt_auth_credentials_choice", lambda *args, **kwargs: "use")
    _model_flow_anthropic({})
    oauth_setup.assert_not_called()
    external_store.assert_not_called()
    model_picker.assert_called_once()
    assert "sk-ant-oat01" not in capsys.readouterr().out


def test_operator_can_choose_reauthentication_for_existing_host_token(isolated_model_flow, monkeypatch):
    from hermes_cli.main import _model_flow_anthropic
    external_store, oauth_setup, model_picker = isolated_model_flow
    save_env_value("ANTHROPIC_TOKEN", "sk-ant-oat01-fixture-host-token")
    monkeypatch.setattr("hermes_cli.model_setup_flows._prompt_auth_credentials_choice", lambda *args, **kwargs: "reauth")
    monkeypatch.setattr("builtins.input", lambda *args: "1")
    _model_flow_anthropic({})
    oauth_setup.assert_called_once()
    external_store.assert_not_called()
    model_picker.assert_not_called()


def test_missing_host_credential_offers_setup_without_reading_claude(isolated_model_flow, monkeypatch):
    from hermes_cli.main import _model_flow_anthropic
    external_store, oauth_setup, model_picker = isolated_model_flow
    monkeypatch.setattr("builtins.input", lambda *args: "1")
    _model_flow_anthropic({})
    oauth_setup.assert_called_once()
    external_store.assert_not_called()
    model_picker.assert_not_called()


def test_missing_host_credential_can_cancel_without_changing_login(isolated_model_flow, monkeypatch):
    from hermes_cli.main import _model_flow_anthropic
    external_store, oauth_setup, model_picker = isolated_model_flow
    monkeypatch.setattr("builtins.input", lambda *args: "3")
    _model_flow_anthropic({})
    oauth_setup.assert_not_called()
    external_store.assert_not_called()
    model_picker.assert_not_called()


def test_valid_explicit_host_setup_token_reused_without_oauth_setup(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from hermes_cli.main import _model_flow_anthropic
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    save_env_value("ANTHROPIC_TOKEN", "sk-ant-oat01-fixture-host-setup-token")
    save_env_value("ANTHROPIC_API_KEY", "")
    external_store = Mock(return_value=None)
    oauth_setup = Mock(return_value=False)
    monkeypatch.setattr("agent.anthropic_adapter.read_claude_code_credentials", external_store)
    monkeypatch.setattr("hermes_cli.main._run_anthropic_oauth_flow", oauth_setup)
    monkeypatch.setattr("hermes_cli.model_setup_flows._prompt_auth_credentials_choice", lambda *args, **kwargs: "use")
    monkeypatch.setattr("hermes_cli.auth._prompt_model_selection", lambda *args, **kwargs: None)
    monkeypatch.setattr("builtins.input", lambda *args: "1")
    _model_flow_anthropic({})
    oauth_setup.assert_not_called()
    external_store.assert_not_called()
