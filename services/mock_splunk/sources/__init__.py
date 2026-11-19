"""Registry of Mock Splunk evidence sources.

Adding a source is one module, one fixture, and one entry in the tuple below.
Routes, shared models, and the OpenAPI spec stay unchanged.
"""

from services.mock_splunk.sources import auth, cloud, dns, edr, email, firewall, proxy, vpn
from services.mock_splunk.sources.base import Source

REGISTRY: dict[str, Source] = {
    s.name: s
    for s in (
        auth.SOURCE,
        vpn.SOURCE,
        proxy.SOURCE,
        edr.SOURCE,
        firewall.SOURCE,
        dns.SOURCE,
        email.SOURCE,
        cloud.SOURCE,
    )
}
