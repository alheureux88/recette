"""Pydantic models for request validation."""

from typing import Any, TypeAlias

from pydantic import BaseModel, Field

JsonDict: TypeAlias = dict[str, object]
"""Dictionnaire JSON générique (lignes DB, payloads, contextes templates)."""


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


class BulkRetagUpdate(BaseModel):
    ids: list[int] = Field(description="Recipe IDs to retag")


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


class PreferencesUpdate(BaseModel):
    language: str | None = Field(default=None, description="UI language: fr or en")
    units: str | None = Field(
        default=None, description="Units system: original, metric, or imperial"
    )
    theme: str | None = Field(default=None, description="Theme: light, dark, or system")
    print_images: bool | None = Field(default=None, description="Print images by default")
    print_tags: bool | None = Field(default=None, description="Print tags by default")
    print_description: bool | None = Field(default=None, description="Print description by default")
    print_links: bool | None = Field(default=None, description="Print external links by default")


class DepartmentOrderUpdate(BaseModel):
    order: list[str] = Field(description="Department technical names in display order")


class ThemeUpdate(BaseModel):
    theme: str = Field(description="Theme: light, dark, or system")


class RecipeIngredientsToShopping(BaseModel):
    ingredient_indices: list[int] = Field(description="Indices of ingredients to add")
    list_id: int | None = Field(default=None, description="Existing list ID, or None to create new")
    new_list_name: str | None = Field(
        default=None, description="Name for new list if list_id is None"
    )
    template_id: int | None = Field(default=None, description="Template ID to seed a new list with")
    multiplier: float | None = Field(
        default=None, description="Multiplier for ingredient quantities"
    )
    units: str | None = Field(
        default=None, description="Units system: original, metric, or imperial"
    )


class CollectionCreate(BaseModel):
    name: str = Field(description="Collection name")
    description: str = Field(default="", description="Short description text")


class CollectionUpdate(BaseModel):
    name: str = Field(description="Collection name")
    description: str = Field(default="", description="Short description text")


class CollectionRecipeAdd(BaseModel):
    recipe_id: int = Field(description="Recipe ID to add")
