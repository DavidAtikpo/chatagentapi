"""Compat: get_supabase() → client PostgreSQL Neon."""

from app.services.postgres_client import PostgresClient, get_supabase

__all__ = ["PostgresClient", "get_supabase"]
