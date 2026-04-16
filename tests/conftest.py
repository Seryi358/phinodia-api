"""Test fixtures for the httpx + PostgREST architecture.

The app talks to Supabase via httpx; tests mock the SupabaseClient with
in-memory dicts to avoid network calls. SQLAlchemy is no longer used.
"""
import os

# Set test env vars before any app imports
os.environ.setdefault("KIE_API_KEY", "test-kie-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("WOMPI_PRIVATE_KEY", "prv_test_abc")
os.environ.setdefault("WOMPI_PUBLIC_KEY", "pub_test_abc")
os.environ.setdefault("WOMPI_EVENTS_SECRET", "test_events_secret")
os.environ.setdefault("WOMPI_INTEGRITY_SECRET", "test_integrity_secret")
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "test-refresh-token")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
