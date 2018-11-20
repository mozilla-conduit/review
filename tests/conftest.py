import imp
import mock
import os
import pytest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


@pytest.fixture
@mock.patch("mozphab.Git.git_out")
@mock.patch("mozphab.Git._get_current_head")
@mock.patch("mozphab.Config")
@mock.patch("mozphab.os.path")
@mock.patch("mozphab.which")
@mock.patch("mozphab.Repository._phab_url")
def git(
    m_repository_phab_url,
    m_which,
    m_os_path,
    m_config,
    m_git_get_current_head,
    m_git_git_out,
):
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_git_get_current_head.return_value = "branch"
    m_git_git_out.return_value = ["user.email=email"]
    return mozphab.Git("x")


@pytest.fixture
@mock.patch("mozphab.Mercurial.hg_out")
@mock.patch("mozphab.Config")
@mock.patch("mozphab.os.path")
@mock.patch("mozphab.which")
@mock.patch("mozphab.Repository._phab_url")
def hg(m_repository_phab_url, m_which, m_os_path, m_config, m_hg_hg_out, safe_environ):
    m_os_path.join = os.path.join
    m_os_path.exists.return_value = True
    m_which.return_value = True
    m_os_path.isfile.return_value = False
    m_hg_hg_out.side_effect = [
        "Mercurial Distributed SCM (version 4.7.1)",
        ["ui.username=username", "extensions.evolve="],
    ]
    return mozphab.Mercurial("x")


@pytest.fixture
def safe_environ(monkeypatch):
    # Make sure we preserve the system defaults.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    # Disable logging to keep the testrunner output clean
    monkeypatch.setattr(mozphab, "init_logging", mock.MagicMock())
