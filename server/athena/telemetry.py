"""Athena telemetry + sign-up client.

Sends anonymous usage stats to Supabase. All telemetry is opt-in.
No content, file names, URLs, or PII is ever transmitted.

Uses curl subprocess for HTTPS — handles SSL natively, no Python HTTP library quirks.
"""

import os
import json
import subprocess
import threading

# Supabase project credentials.
# The anon key is a PUBLISHABLE key — designed to be in client code.
# Security is enforced by Row Level Security (RLS) policies on the database,
# not by key secrecy. This key only permits: INSERT to telemetry/feedback tables.
# See: https://supabase.com/docs/guides/api/api-keys
_SUPABASE_HOST = 'dprjdhvzzjbgsiczvzah.supabase.co'
# nosemgrep: generic.secrets.security.detected-jwt-token
_SUPABASE_ANON_KEY = (  # noqa: S105 — publishable key, not a secret
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.'
    'eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRwcmpkaHZ6empiZ3NpY3p2emFoIiwi'
    'cm9sZSI6ImFub24iLCJpYXQiOjE3NzU3MTAwNDQsImV4cCI6MjA5MTI4NjA0NH0.'
    'ujZIyu3yqq53j0tKN02FOKpWScMnQf6vxfwIzyEKmeE'
)

# Static HTTPS endpoints — hardcoded, never from user input
_ENDPOINTS = {
    'telemetry': f'https://{_SUPABASE_HOST}/rest/v1/telemetry',
    'feedback': f'https://{_SUPABASE_HOST}/rest/v1/feedback',
    'shared_rules': f'https://{_SUPABASE_HOST}/rest/v1/shared_rules',
    'magiclink': f'https://{_SUPABASE_HOST}/auth/v1/magiclink',
}


def _get_config(vault_root):
    """Load .athena/config.json."""
    config_path = os.path.join(vault_root, '.athena', 'config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _get_vault_id(vault_root):
    """Get or create a stable anonymous vault identifier."""
    config = _get_config(vault_root)
    vault_id = config.get('vault_id')
    if not vault_id:
        import hashlib
        vault_id = hashlib.sha256(os.path.realpath(vault_root).encode()).hexdigest()[:16]
        _save_config_key(vault_root, 'vault_id', vault_id)
    return vault_id


def _save_config_key(vault_root, key, value):
    """Save a key to .athena/config.json."""
    config_path = os.path.join(vault_root, '.athena', 'config.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    config = _get_config(vault_root)
    config[key] = value
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)


def is_telemetry_enabled(vault_root):
    """Check if user opted in to telemetry."""
    return _get_config(vault_root).get('telemetry', False)


def _curl_post(endpoint_key, data, auth_token=None):
    """POST JSON to a Supabase endpoint via curl. Silent on failure."""
    url = _ENDPOINTS.get(endpoint_key)
    if not url:
        return None, 'Unknown endpoint'
    token = auth_token or _SUPABASE_ANON_KEY
    try:
        result = subprocess.run(
            [
                'curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                '-X', 'POST', url,
                '-H', 'Content-Type: application/json',
                '-H', f'apikey: {_SUPABASE_ANON_KEY}',
                '-H', f'Authorization: Bearer {token}',
                '-H', 'Prefer: return=minimal',
                '-d', json.dumps(data),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip(), None
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return None, 'Request failed'


def _curl_post_with_body(endpoint_key, data):
    """POST JSON and return the response body."""
    url = _ENDPOINTS.get(endpoint_key)
    if not url:
        return None, None
    try:
        result = subprocess.run(
            [
                'curl', '-s',
                '-X', 'POST', url,
                '-H', 'Content-Type: application/json',
                '-H', f'apikey: {_SUPABASE_ANON_KEY}',
                '-d', json.dumps(data),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode, result.stdout
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError):
        return None, None


def send_event(vault_root, event, data=None):
    """Send a telemetry event (async, non-blocking).

    Args:
        vault_root: vault path
        event: event name ('search', 'add', 'query', 'export', 'index', 'lint')
        data: dict of anonymous metadata (page counts, feature flags — never content)
    """
    if not is_telemetry_enabled(vault_root):
        return

    vault_id = _get_vault_id(vault_root)
    config = _get_config(vault_root)

    payload = {
        'vault_id': vault_id,
        'event': event,
        'data': data or {},
    }

    user_token = config.get('auth', {}).get('access_token')
    if config.get('auth', {}).get('user_id'):
        payload['user_id'] = config['auth']['user_id']

    # Fire and forget — never block the user
    thread = threading.Thread(
        target=_curl_post,
        args=('telemetry', payload, user_token),
        daemon=True,
    )
    thread.start()


def send_feedback(vault_root, feedback_type, message='', query='', context=None):
    """Send user feedback (async, non-blocking).

    Args:
        feedback_type: 'search_helpful', 'search_unhelpful', 'bug', 'feature', 'general'
        message: user's feedback text
        query: the search/query that prompted feedback
        context: dict of anonymous context
    """
    config = _get_config(vault_root)
    vault_id = _get_vault_id(vault_root)

    payload = {
        'vault_id': vault_id,
        'feedback_type': feedback_type,
        'query': query,
        'message': message,
        'context': context or {},
    }

    user_token = config.get('auth', {}).get('access_token')
    if config.get('auth', {}).get('user_id'):
        payload['user_id'] = config['auth']['user_id']

    thread = threading.Thread(
        target=_curl_post,
        args=('feedback', payload, user_token),
        daemon=True,
    )
    thread.start()


def sign_up(email, vault_root):
    """Sign up for an Athena account via Supabase magic link.

    Sends a magic link to the email. User clicks it to verify.
    Returns (success, message).
    """
    returncode, body = _curl_post_with_body('magiclink', {'email': email})

    if returncode is None:
        return False, 'Could not reach Athena servers. Check your internet connection.'

    if body and body.strip().startswith('{'):
        try:
            resp = json.loads(body)
            if resp.get('error'):
                return False, resp.get('error_description', resp.get('msg', 'Sign-up failed'))
        except json.JSONDecodeError:
            pass

    # Save email locally
    _save_config_key(vault_root, 'auth', {
        'email': email,
        'status': 'pending_verification',
    })
    return True, f'Magic link sent to {email}. Check your inbox to verify.'


def get_user_status(vault_root):
    """Check if user is signed up and verified."""
    config = _get_config(vault_root)
    auth = config.get('auth', {})
    if not auth:
        return 'not_signed_up'
    if auth.get('access_token'):
        return 'verified'
    if auth.get('email'):
        return 'pending_verification'
    return 'not_signed_up'
