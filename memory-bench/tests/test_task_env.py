"""Network-policy seam tests (mem-yeoz): full harbor network_mode enum + replay allowlist.

Every replayed fix landed weeks earlier on a public GitHub repo named in-band by the
baked snapshot, so real runs must never get public egress by default — the allowlist
covers only the agent's own hosts and the rigs' package registries, and github.com is
deliberately absent.
"""

import pytest

from membench.harbor.task_env import REPLAY_ALLOWED_HOSTS, environment_network


def test_public_and_no_network_modes_map_without_hosts():
    assert environment_network("public") == {"network_mode": "public"}
    assert environment_network("no-network") == {"network_mode": "no-network"}


def test_allowlist_mode_carries_replay_hosts():
    frag = environment_network("allowlist")
    assert frag["network_mode"] == "allowlist"
    assert frag["allowed_hosts"] == list(REPLAY_ALLOWED_HOSTS)


def test_allowlist_hosts_are_overridable():
    frag = environment_network("allowlist", allowed_hosts=("pypi.org",))
    assert frag["allowed_hosts"] == ["pypi.org"]


def test_replay_allowlist_covers_agent_and_registries_but_never_github():
    # The Anthropic API plus the package registries the replayed rigs' builds
    # resolve from (node / go / python / rust toolchains).
    for host in (
        "api.anthropic.com",
        "registry.npmjs.org",
        "proxy.golang.org",
        "pypi.org",
        "crates.io",
    ):
        assert host in REPLAY_ALLOWED_HOSTS
    # ...and never the host the landed gold diff lives on.
    assert "github.com" not in REPLAY_ALLOWED_HOSTS
    assert "*.github.com" not in REPLAY_ALLOWED_HOSTS


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="network mode"):
        environment_network("open")
