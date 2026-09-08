"""Pydantic models for request validation."""

from typing import Any

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


class InlineCategoryUpdate(BaseModel):
    category: str | None = Field(default=None, description="Category name, null to clear")


class InlineTagsUpdate(BaseModel):
    tags: list[str] = Field(default=[], description='Tag keys as "family:name"')


class BulkCategoryUpdate(BaseModel):
    ids: list[int] = Field(description="Recipe IDs to update")
    category: str | None = Field(default=None, description="Category name, null to clear")


class BulkTagsUpdate(BaseModel):
    ids: list[int] = Field(description="Recipe IDs to update")
    add: list[str] = Field(default=[], description='Tag keys to add as "family:name"')
    remove: list[str] = Field(default=[], description='Tag keys to remove as "family:name"')


class PushSubscriptionRegister(BaseModel):
    endpoint: str = Field(description="Push subscription endpoint URL")
    subscription: dict[str, Any] = Field(description="Full push subscription object")


class TimerScheduleRequest(BaseModel):
    recipe_id: int = Field(description="Recipe ID")
    step_index: int = Field(description="Step index (0-based)")
    duration_seconds: int = Field(description="Timer duration in seconds")
    endpoint: str = Field(description="Push subscription endpoint to notify")


class TimerCancelRequest(BaseModel):
    recipe_id: int = Field(description="Recipe ID")
    step_index: int = Field(description="Step index (0-based)")


class ShoppingListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="List name")


class ShoppingListRename(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="New list name")


class ShoppingListItemAdd(BaseModel):
    department_id: int = Field(description="Department ID")
    text: str = Field(min_length=1, max_length=500, description="Item text")
    quantity: str | None = Field(default=None, max_length=100, description="Optional quantity")


class ShoppingListItemUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=500, description="Item text")
    quantity: str | None = Field(default=None, max_length=100, description="Optional quantity")
    department_id: int | None = Field(default=None, description="Optional department change")


class RecipeIngredientsToShopping(BaseModel):
    ingredient_indices: list[int] = Field(description="Indices of ingredients to add")
    list_id: int | None = Field(default=None, description="Existing list ID, or None to create new")
    new_list_name: str | None = Field(
        default=None, description="Name for new list if list_id is None"
    )
    multiplier: float | None = Field(
        default=None, description="Multiplier for ingredient quantities"
    )
    units: str | None = Field(
        default=None, description="Units system: original, metric, or imperial"
    )
