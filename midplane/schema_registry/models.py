"""Typed catalog entries for platform schemas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SchemaField:
    name: str
    type: str
    nullable: bool
    required: bool = True
    source_file: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        payload = {
            "name": self.name,
            "type": self.type,
            "nullable": self.nullable,
            "required": self.required,
            "source_file": self.source_file,
        }
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True)
class SchemaEntry:
    id: str
    domain: str
    version: str
    fields: tuple[SchemaField, ...]
    source_file: str
    reconcile: tuple[str, ...]
    description: str = ""
    source_files: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "domain": self.domain,
            "version": self.version,
            "fields": [field.to_dict() for field in self.fields],
            "source_file": self.source_file,
            "source_files": list(self.source_files or (self.source_file,)),
            "reconcile": list(self.reconcile),
            "description": self.description,
        }
