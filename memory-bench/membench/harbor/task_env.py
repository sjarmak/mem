"""Harbor task.toml ``[environment]`` network policy (harbor>=0.13 schema).

Harbor 0.13 deprecated the boolean ``allow_internet`` field in favour of
``[environment].network_mode`` (``public`` | ``no-network`` | ``allowlist``). We keep
a boolean ``allow_internet`` knob at our own API surface — internet on/off is the
only choice our rigs need — and translate it to the current harbor field HERE, at
the single TOML-emission seam, so the deprecated field is never written and no
DeprecationWarning fires when harbor validates the task config.
"""

from __future__ import annotations

# harbor.models.task.config.NetworkMode values; kept as bare strings so this module
# imports nothing from harbor (the skeleton must build without the optional pkg).
_PUBLIC = "public"
_NO_NETWORK = "no-network"


def network_mode(allow_internet: bool) -> str:
    """Map our boolean internet knob to harbor's ``network_mode`` enum value."""
    return _PUBLIC if allow_internet else _NO_NETWORK


def environment_network(allow_internet: bool) -> dict[str, str]:
    """The ``[environment]`` network-policy fragment for a harbor task.toml."""
    return {"network_mode": network_mode(allow_internet)}
