"""URL safety for the camera test-ping (v3, SSRF protection).

A logged-in teacher may only ping a real IP-webcam address:
  - http:// or https:// only
  - a valid hostname or IP address (never "localhost")
  - ports 80, 443 or 8080 only (IP Webcam's default)
  - loopback (127.0.0.0/8, ::1), link-local (169.254.0.0/16 — this is
    where the cloud metadata service 169.254.169.254 lives), multicast
    and unspecified addresses are blocked
  - private LAN addresses (10/8, 172.16/12, 192.168/16) are ALLOWED —
    that is exactly where a phone running IP Webcam sits
"""
import ipaddress
from urllib.parse import urlparse


ALLOWED_PORTS = {80, 443, 8080}


def validate_camera_url(raw_url):
    """Return (ok, clean_url_or_None, error_message_or_None)."""
    url = (raw_url or '').strip()
    if not url:
        return False, None, 'No URL provided.'
    if not url.startswith(('http://', 'https://')):
        url = 'http://' + url

    try:
        parsed = urlparse(url)
    except ValueError:
        return False, None, 'That URL is not valid.'

    if parsed.scheme not in ('http', 'https'):
        return False, None, 'Only http:// and https:// URLs are allowed.'

    host = (parsed.hostname or '').strip().lower()
    if not host or host in ('localhost', 'localhost.localdomain'):
        return False, None, 'Localhost URLs are not allowed.'

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == 'https' else 80
    if port not in ALLOWED_PORTS:
        return False, None, (
            f'Port {port} is not allowed. Use port 80, 443 or 8080 '
            '(IP Webcam uses 8080).')

    # If the host is an IP literal, check it directly.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if (ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_unspecified or ip.is_reserved):
            return False, None, (
                'That address is not allowed (loopback / link-local '
                'addresses such as 127.0.0.1 and 169.254.169.254 are blocked).')
        # Private LAN (10/172.16/192.168) and public IPs are both fine.

    clean = parsed._replace(
        netloc=f'{host}:{port}' if port not in (80, 443) else host
    ).geturl()
    return True, clean, None
