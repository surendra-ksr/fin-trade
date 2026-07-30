"""Persistence layer: SQLite database manager and schema migrations."""

from .database import DatabaseManager, DatabaseError, get_database

__all__ = ["DatabaseManager", "DatabaseError", "get_database"]
