"""Erreurs DB (remplace postgrest.exceptions.APIError après migration Neon)."""


class APIError(Exception):
    """Compat — requêtes table() échouées."""
