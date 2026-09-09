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
