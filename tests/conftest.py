"""Test fixtures for the httpx + PostgREST architecture.

The app talks to Supabase via httpx; tests mock the SupabaseClient with
in-memory dicts to avoid network calls. SQLAlchemy is no longer used.
"""
import os

# Set test env vars before any app imports. Values must be >=16 chars to
# satisfy the secret-length validator added in iter 2.
os.environ.setdefault("KIE_API_KEY", "test-kie-key-1234567890")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-1234567890")
os.environ.setdefault("WOMPI_PRIVATE_KEY", "prv_test_abc_1234567890")
os.environ.setdefault("WOMPI_PUBLIC_KEY", "pub_test_abc")
os.environ.setdefault("WOMPI_EVENTS_SECRET", "test_events_secret_1234567890")
os.environ.setdefault("WOMPI_INTEGRITY_SECRET", "test_integrity_secret_1234567890")
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-client-secret-1234567890")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "test-refresh-token-1234567890")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key-1234567890")
