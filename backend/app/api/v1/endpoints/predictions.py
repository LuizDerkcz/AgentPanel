from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps.auth import get_current_demo_user
from app.core.errors import api_error
from app.db.session import get_db
from app.models.prediction import PredictionMarket, PredictionOption, PredictionVote
from app.models.user import User


router = APIRouter(prefix="/predictions", tags=["predictions"])


class PredictionOptionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=120)

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class PredictionMarketCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    market_type: Literal["single", "multiple"] = "single"
    is_vote_changeable: bool = True
    reveal_results_after_vote: bool = False
    ends_at: datetime | None = None
    options: list[PredictionOptionCreate] = Field(min_length=2, max_length=10)

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class PredictionVoteInput(BaseModel):
    option_ids: list[int] = Field(min_length=1, max_length=10)


class PredictionOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    option_text: str
    sort_order: int
    vote_count: int


class PredictionMarketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    creator_user_id: int | None
    title: str
    description: str | None = None
    market_type: str
    is_vote_changeable: bool
    reveal_results_after_vote: bool
    status: str
    ends_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    options: list[PredictionOptionOut]
    my_option_ids: list[int] = []


def _serialize_market(
    market: PredictionMarket,
    options: list[PredictionOption],
    my_option_ids: list[int],
) -> PredictionMarketOut:
    return PredictionMarketOut.model_validate(
        {
            "id": market.id,
            "creator_user_id": market.creator_user_id,
            "title": market.title,
            "description": market.description,
            "market_type": market.market_type,
            "is_vote_changeable": market.is_vote_changeable,
            "reveal_results_after_vote": market.reveal_results_after_vote,
            "status": market.status,
            "ends_at": market.ends_at,
            "created_at": market.created_at,
            "updated_at": market.updated_at,
            "options": options,
            "my_option_ids": my_option_ids,
        }
    )


def _validate_market_open(market: PredictionMarket) -> None:
    if market.status != "open":
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="PREDICTION_MARKET_CLOSED",
            message="Prediction market is not open.",
        )
    if market.ends_at and market.ends_at <= datetime.now(timezone.utc):
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="PREDICTION_MARKET_ENDED",
            message="Prediction market already ended.",
        )


@router.post(
    "", response_model=PredictionMarketOut, status_code=status.HTTP_201_CREATED
)
def create_prediction_market(
    payload: PredictionMarketCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> PredictionMarketOut:
    if not bool(getattr(current_user, "is_verified", False)):
        raise api_error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="PREDICTION_CREATE_REQUIRES_VERIFIED",
            message="Only verified users can create prediction markets.",
        )

    unique_texts = set()
    for option in payload.options:
        normalized = option.text.strip().lower()
        if normalized in unique_texts:
            raise api_error(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="PREDICTION_OPTION_DUPLICATED",
                message="Prediction options must be unique.",
            )
        unique_texts.add(normalized)

    market = PredictionMarket(
        creator_user_id=int(current_user.id),
        title=payload.title,
        description=payload.description,
        market_type=payload.market_type,
        is_vote_changeable=payload.is_vote_changeable,
        reveal_results_after_vote=payload.reveal_results_after_vote,
        status="open",
        ends_at=payload.ends_at,
    )
    db.add(market)
    db.flush()

    options: list[PredictionOption] = []
    for index, option in enumerate(payload.options):
        row = PredictionOption(
            market_id=int(market.id),
            option_text=option.text,
            sort_order=index,
            vote_count=0,
        )
        options.append(row)
        db.add(row)

    db.commit()
    return _serialize_market(market, options, [])


@router.get("", response_model=list[PredictionMarketOut])
def list_prediction_markets(
    status_filter: Literal["open", "closed", "resolved", "cancelled", "all"] = Query(
        default="open",
        alias="status",
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> list[PredictionMarketOut]:
    query = select(PredictionMarket).order_by(
        PredictionMarket.created_at.desc(),
        PredictionMarket.id.desc(),
    )
    if status_filter != "all":
        query = query.where(PredictionMarket.status == status_filter)

    markets = list(db.scalars(query.offset(offset).limit(limit)).all())
    if not markets:
        return []

    market_ids = [int(item.id) for item in markets]
    options = list(
        db.scalars(
            select(PredictionOption)
            .where(PredictionOption.market_id.in_(market_ids))
            .order_by(
                PredictionOption.market_id.asc(), PredictionOption.sort_order.asc()
            )
        ).all()
    )
    votes = list(
        db.scalars(
            select(PredictionVote).where(
                PredictionVote.market_id.in_(market_ids),
                PredictionVote.user_id == int(current_user.id),
            )
        ).all()
    )

    options_by_market: dict[int, list[PredictionOption]] = {}
    for option in options:
        options_by_market.setdefault(int(option.market_id), []).append(option)

    my_votes_by_market: dict[int, list[int]] = {}
    for vote in votes:
        my_votes_by_market.setdefault(int(vote.market_id), []).append(
            int(vote.option_id)
        )

    return [
        _serialize_market(
            market,
            options_by_market.get(int(market.id), []),
            my_votes_by_market.get(int(market.id), []),
        )
        for market in markets
    ]


@router.get("/{market_id}", response_model=PredictionMarketOut)
def get_prediction_market(
    market_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> PredictionMarketOut:
    market = db.get(PredictionMarket, int(market_id))
    if not market:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PREDICTION_MARKET_NOT_FOUND",
            message="Prediction market not found.",
        )

    options = list(
        db.scalars(
            select(PredictionOption)
            .where(PredictionOption.market_id == int(market_id))
            .order_by(PredictionOption.sort_order.asc())
        ).all()
    )
    my_votes = list(
        db.scalars(
            select(PredictionVote).where(
                PredictionVote.market_id == int(market_id),
                PredictionVote.user_id == int(current_user.id),
            )
        ).all()
    )
    return _serialize_market(
        market,
        options,
        [int(item.option_id) for item in my_votes],
    )


@router.post("/{market_id}/vote", response_model=PredictionMarketOut)
def vote_prediction_market(
    market_id: int,
    payload: PredictionVoteInput,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_demo_user),
) -> PredictionMarketOut:
    market = db.get(PredictionMarket, int(market_id))
    if not market:
        raise api_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PREDICTION_MARKET_NOT_FOUND",
            message="Prediction market not found.",
        )

    _validate_market_open(market)

    options = list(
        db.scalars(
            select(PredictionOption)
            .where(PredictionOption.market_id == int(market_id))
            .order_by(PredictionOption.sort_order.asc())
        ).all()
    )
    option_ids = {int(item.id) for item in options}

    selected_ids = [int(item) for item in payload.option_ids]
    selected_ids = list(dict.fromkeys(selected_ids))
    if market.market_type == "single" and len(selected_ids) != 1:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="PREDICTION_SINGLE_REQUIRES_ONE_OPTION",
            message="Single-choice market requires exactly one option.",
        )
    if market.market_type == "multiple" and len(selected_ids) < 1:
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="PREDICTION_MULTI_REQUIRES_OPTIONS",
            message="Multiple-choice market requires at least one option.",
        )

    if any(item not in option_ids for item in selected_ids):
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="PREDICTION_OPTION_NOT_IN_MARKET",
            message="Option does not belong to this market.",
        )

    existing_votes = list(
        db.scalars(
            select(PredictionVote).where(
                PredictionVote.market_id == int(market_id),
                PredictionVote.user_id == int(current_user.id),
            )
        ).all()
    )
    existing_by_option = {int(item.option_id): item for item in existing_votes}
    if (
        not bool(getattr(market, "is_vote_changeable", True))
        and len(existing_by_option) > 0
        and set(existing_by_option.keys()) != set(selected_ids)
    ):
        raise api_error(
            status_code=status.HTTP_409_CONFLICT,
            code="PREDICTION_VOTE_LOCKED",
            message="Votes for this market cannot be changed.",
        )

    to_remove = [
        item
        for option_id, item in existing_by_option.items()
        if option_id not in selected_ids
    ]
    to_add = [item for item in selected_ids if item not in existing_by_option]

    option_by_id = {int(item.id): item for item in options}

    for row in to_remove:
        option = option_by_id.get(int(row.option_id))
        if option:
            option.vote_count = max(0, int(option.vote_count or 0) - 1)
        db.delete(row)

    now = datetime.now(timezone.utc)
    for option_id in to_add:
        db.add(
            PredictionVote(
                market_id=int(market_id),
                option_id=int(option_id),
                user_id=int(current_user.id),
                created_at=now,
            )
        )
        option = option_by_id.get(int(option_id))
        if option:
            option.vote_count = int(option.vote_count or 0) + 1

    db.commit()

    my_option_ids = selected_ids
    return _serialize_market(
        market,
        options,
        my_option_ids,
    )
