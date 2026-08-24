"""Pydantic models for request validation."""

from pydantic import BaseModel, Field, field_validator


class SearchQuery(BaseModel):
    q: str = Field(default="", description="Search query string")
    tags: list[int] = Field(default=[], description="List of tag IDs to filter by")
    category: int | None = Field(default=None, description="Category ID to filter by")

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, v: str | int | None) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (ValueError, TypeError) as e:
            raise ValueError("category must be a valid integer") from e
