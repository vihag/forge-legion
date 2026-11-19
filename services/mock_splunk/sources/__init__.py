"""Registry of Mock Splunk evidence sources.

Adding a source is one module, one fixture, and one entry in the tuple below.
Routes, shared models, and the OpenAPI spec stay unchanged.
"""

from services.mock_splunk.sources import auth, edr, proxy
from services.mock_splunk.sources.base import Source

REGISTRY: dict[str, Source] = {s.name: s for s in (auth.SOURCE, proxy.SOURCE, edr.SOURCE)}
