from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contract import MAX_AMOUNT, AuctionCreate, BidCreate

BID_ID = "5b0e8a8e-6a3c-4a8e-9f0e-1c2d3e4f5a6b"


@pytest.mark.parametrize("amount", [True, "100", 100.0, 0, -5, MAX_AMOUNT + 1])
def test_bid_amount_rejects_non_integers_and_out_of_range(amount: object) -> None:
    with pytest.raises(ValidationError):
        BidCreate.model_validate({"bid_id": BID_ID, "amount": amount})


@pytest.mark.parametrize("amount", [1, MAX_AMOUNT])
def test_bid_amount_accepts_integers_in_range(amount: int) -> None:
    assert BidCreate.model_validate({"bid_id": BID_ID, "amount": amount}).amount == amount


def test_bid_amount_from_json_must_be_a_json_integer() -> None:
    with pytest.raises(ValidationError):
        BidCreate.model_validate_json(f'{{"bid_id": "{BID_ID}", "amount": "100"}}')
    assert BidCreate.model_validate_json(f'{{"bid_id": "{BID_ID}", "amount": 100}}').amount == 100


@pytest.mark.parametrize("start_price", [True, "0", 1.0, -1])
def test_start_price_rejects_non_integers_and_negatives(start_price: object) -> None:
    with pytest.raises(ValidationError):
        AuctionCreate.model_validate(
            {"title": "Lamp", "start_price": start_price, "end_at": "2030-01-01T00:00:00Z"}
        )


def test_auction_create_normalises_end_at_to_utc() -> None:
    auction = AuctionCreate.model_validate(
        {"title": " Lamp ", "start_price": 0, "end_at": "2030-01-01T05:30:00+05:30"}
    )

    assert auction.title == "Lamp"
    assert auction.end_at == datetime(2030, 1, 1, tzinfo=UTC)
    assert auction.end_at.tzinfo is UTC
