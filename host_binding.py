import ipaddress
import logging


logger = logging.getLogger(__name__)


def warn_if_non_loopback_host(host):
    normalized_host = host.strip().strip("[]")
    try:
        is_loopback = ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        is_loopback = normalized_host.rstrip(".").casefold() == "localhost"
    if not is_loopback:
        logger.warning(
            "WARNING: INSIGHTER_HOST is non-loopback (%s). This publishes "
            "unauthenticated routes; do not expose the development server to "
            "untrusted networks.",
            host,
        )
