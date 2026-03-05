from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user
from app.api.v1.shared import AuthorSummaryOut, build_author_map
from app.core.error_codes import (
    COLUMN_DELETE_FORBIDDEN,
    COLUMN_MODIFY_FORBIDDEN,
    COLUMN_NOT_FOUND,
)
from app.core.errors import api_error
from app.db.session import get_db
from app.models.forum import Column
from app.models.user import User


router = APIRouter(prefix="/forum/columns", tags=["columns"])

COLUMN_BODY_MAX_LENGTH = 100_000


class ColumnCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=COLUMN_BODY_MAX_LENGTH)
    abstract: str | None = Field(default=None, max_length=500)
    status: Literal["draft", "published"] = "published"

    @field_validator("title", "body", mode="before")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("must be a string")
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("abstract", mode="before")
    @classmethod
    def _strip_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class ColumnUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(
        default=None, min_length=1, max_length=COLUMN_BODY_MAX_LENGTH
    )
    abstract: str | None = Field(default=None, max_length=500)
    status: Literal["draft", "published", "locked", "deleted"] | None = None

    @field_validator("title", "body", mode="before")
    @classmethod
    def _strip_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("abstract", mode="before")
    @classmethod
    def _strip_optional_abstract(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class ColumnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    title: str
    abstract: str | None = None
    body: str
    summary: str | None = None
    status: str
    comment_count: int
    like_count: int
    view_count: int
    published_at: datetime | None = None
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime
    author: AuthorSummaryOut | None = None


class ColumnPageOut(BaseModel):
    items: list[ColumnOut]
    total: int
    page: int
    page_size: int


_SORT_COLUMNS = {
    "latest": Column.last_activity_at.desc(),
    "hot": Column.like_count.desc(),
    "most_comments": Column.comment_count.desc(),
}


def _serialize_column(col: Column, author_map: dict) -> ColumnOut:
    out = ColumnOut.model_validate(col)
    out.author = author_map.get(col.author_id)
    return out


def _get_column(db: Session, column_id: int) -> Column:
    col = db.get(Column, column_id)
    if not col or col.status == "deleted":
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code=COLUMN_NOT_FOUND,
            message="Column post not found.",
        )
    return col


@router.get("", response_model=ColumnPageOut)
def list_columns(
    sort_by: Literal["latest", "hot", "most_comments"] = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ColumnPageOut:
    order_col = _SORT_COLUMNS[sort_by]
    base = select(Column).where(Column.status == "published")
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    columns = list(
        db.scalars(
            base.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
        ).all()
    )
    author_map = build_author_map(db, {c.author_id for c in columns})
    return ColumnPageOut(
        items=[_serialize_column(c, author_map) for c in columns],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ColumnOut, status_code=status.HTTP_201_CREATED)
def create_column(
    payload: ColumnCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> ColumnOut:
    now = datetime.now(timezone.utc)
    col = Column(
        author_id=user.id,
        title=payload.title,
        abstract=payload.abstract,
        body=payload.body,
        body_length=len(payload.body),
        status=payload.status,
        comment_count=0,
        like_count=0,
        view_count=0,
        published_at=now if payload.status == "published" else None,
        last_activity_at=now,
    )
    db.add(col)
    db.commit()
    db.refresh(col)
    author_map = build_author_map(db, {col.author_id})
    return _serialize_column(col, author_map)


@router.post(
    "/{column_id}/view",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def increment_column_view(column_id: int, db: Session = Depends(get_db)) -> Response:
    col = db.get(Column, column_id)
    if col and col.status != "deleted":
        col.view_count += 1
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{column_id}", response_model=ColumnOut)
def get_column(column_id: int, db: Session = Depends(get_db)) -> ColumnOut:
    col = _get_column(db, column_id)
    author_map = build_author_map(db, {col.author_id})
    return _serialize_column(col, author_map)


@router.patch("/{column_id}", response_model=ColumnOut)
def update_column(
    column_id: int,
    payload: ColumnUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> ColumnOut:
    col = _get_column(db, column_id)

    if col.author_id != user.id and user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=COLUMN_MODIFY_FORBIDDEN,
            message="No permission to modify this column post.",
        )

    update_data = payload.model_dump(exclude_unset=True)
    if "body" in update_data:
        update_data["body_length"] = len(update_data["body"])

    new_status = update_data.pop("status", None)
    for field, value in update_data.items():
        setattr(col, field, value)
    if new_status is not None:
        if new_status == "published" and col.published_at is None:
            col.published_at = datetime.now(timezone.utc)
        col.status = new_status

    db.commit()
    db.refresh(col)
    author_map = build_author_map(db, {col.author_id})
    return _serialize_column(col, author_map)


@router.delete(
    "/{column_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_column(
    column_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_demo_user),
) -> Response:
    col = _get_column(db, column_id)

    if col.author_id != user.id and user.user_type != "admin":
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code=COLUMN_DELETE_FORBIDDEN,
            message="No permission to delete this column post.",
        )

    col.status = "deleted"
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
