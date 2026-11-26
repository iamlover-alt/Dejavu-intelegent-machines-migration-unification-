"""Machine-readable catalog of the platform's schemas."""

from midplane.schema_registry.build import SchemaCatalog, load_schema_registry, repo_root
from midplane.schema_registry.models import SchemaEntry, SchemaField


def lookup_schema(schema_id: str) -> SchemaEntry | None:
    """Return one catalog entry, or None when the id is unknown."""

    return load_schema_registry().lookup(schema_id)


def search_schemas(query: str, limit: int = 8) -> tuple[SchemaEntry, ...]:
    """Rank catalog entries by id, domain, field names, and description."""

    return load_schema_registry().search(query, limit=limit)


__all__ = [
    "SchemaCatalog",
    "SchemaEntry",
    "SchemaField",
    "load_schema_registry",
    "lookup_schema",
    "repo_root",
    "search_schemas",
]
