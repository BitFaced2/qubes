"""
Built-in Relay List

Pre-loaded list of seed relays, following the same pattern as Electron Cash's
built-in Fulcrum server list. As the community grows, more relays will be added.

Users can add/remove any relay via Settings → Relay Node → Custom Relays.
Full user control — even seed relays can be removed.
"""

from typing import List, Dict, Any


# BitFaced-operated seed relays (always present, maintained long-term).
# BitFaced fills in real multiaddrs and peer IDs before deploying.
BUILTIN_RELAYS: List[Dict[str, Any]] = [
    # -------------------------------------------------------------------------
    # BitFaced Seed Relays (official)
    # -------------------------------------------------------------------------
    {
        "id": "QmRelayUS",
        "label": "BitFaced Relay US-East",
        "operator": "BitFaced",
        "region": "US-East",
        "multiaddrs": [
            "/ip4/relay-us.qube.cash/tcp/4001/p2p/QmRelayUS",
            "/ip4/relay-us.qube.cash/tcp/443/wss/p2p/QmRelayUS",
        ],
    },
    {
        "id": "QmRelayEU",
        "label": "BitFaced Relay EU",
        "operator": "BitFaced",
        "region": "EU",
        "multiaddrs": [
            "/ip4/relay-eu.qube.cash/tcp/4001/p2p/QmRelayEU",
            "/ip4/relay-eu.qube.cash/tcp/443/wss/p2p/QmRelayEU",
        ],
    },
    {
        "id": "QmRelayAS",
        "label": "BitFaced Relay Asia",
        "operator": "BitFaced",
        "region": "Asia",
        "multiaddrs": [
            "/ip4/relay-as.qube.cash/tcp/4001/p2p/QmRelayAS",
            "/ip4/relay-as.qube.cash/tcp/443/wss/p2p/QmRelayAS",
        ],
    },
    # -------------------------------------------------------------------------
    # Community Relay Slots
    # Open an issue with label 'relay-listing' to get your relay added here.
    # Requirements: 30+ days uptime, known operator, open to all Qubes users.
    # -------------------------------------------------------------------------
    # {
    #     "id": "QmCommRelay1",
    #     "label": "Community Relay 1",
    #     "operator": "TBD",
    #     "region": "TBD",
    #     "multiaddrs": [],
    # },
]


def get_all_bootstrap_multiaddrs() -> List[str]:
    """Return flat list of all multiaddrs from the built-in relay list."""
    addrs = []
    for relay in BUILTIN_RELAYS:
        addrs.extend(relay["multiaddrs"])
    return addrs


def get_relay_by_id(relay_id: str) -> Dict[str, Any] | None:
    """Look up a built-in relay by its ID."""
    for relay in BUILTIN_RELAYS:
        if relay["id"] == relay_id:
            return relay
    return None


def get_relay_labels() -> List[str]:
    """Return human-readable labels for all built-in relays."""
    return [r["label"] for r in BUILTIN_RELAYS]
