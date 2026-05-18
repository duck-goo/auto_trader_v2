"""Tests for NaverDailyUniverseSource."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from market import (
    NaverDailyUniverseSource,
    UniverseMasterItem,
    parse_naver_daily_chart_xml,
)


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse(self.content)


def _chart_xml(*, count: int = 21) -> bytes:
    start = datetime(2026, 3, 1)
    items = []
    for offset in range(count):
        day = start + timedelta(days=offset)
        open_price = 1000 + offset
        high_price = 1100 + offset
        low_price = 900 + offset
        close_price = 1050 + offset
        volume = 10_000 + offset
        items.append(
            f'<item data="{day.strftime("%Y%m%d")}|{open_price}|{high_price}|'
            f'{low_price}|{close_price}|{volume}" />'
        )
    return (
        '<?xml version="1.0" encoding="EUC-KR" ?><protocol><chartdata>'
        + "".join(items)
        + "</chartdata></protocol>"
    ).encode("euc-kr")


def test_parse_naver_daily_chart_xml_computes_trade_value():
    df = parse_naver_daily_chart_xml(_chart_xml(count=1))

    assert len(df) == 1
    assert df.iloc[0]["close"] == 1050
    assert df.iloc[0]["trade_value"] == 10_125_000


def test_naver_daily_universe_source_builds_items():
    session = FakeSession(_chart_xml(count=25))
    source = NaverDailyUniverseSource(
        session=session,
        master_items=[
            UniverseMasterItem(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
                is_preferred_stock=True,
            )
        ],
        trade_date="2026-04-14",
        daily_count=40,
    )

    items = source.load()

    assert len(items) == 1
    assert items[0].symbol == "005930"
    assert items[0].is_preferred_stock is True
    assert items[0].avg_trade_value_20 > 0
    assert session.calls[0]["params"]["symbol"] == "005930"


def test_naver_daily_universe_source_can_skip_symbol_errors():
    source = NaverDailyUniverseSource(
        session=FakeSession(_chart_xml(count=19)),
        master_items=[
            UniverseMasterItem(
                symbol="005930",
                name="Samsung Electronics",
                market="KOSPI",
            )
        ],
        trade_date="2026-04-14",
        daily_count=40,
        skip_symbol_errors=True,
    )

    items = source.load()

    assert items == []
    assert source.skipped_items[0].symbol == "005930"
    assert source.skipped_items[0].error_type == "ValueError"


def test_parse_naver_daily_chart_xml_rejects_bad_item_shape():
    content = (
        '<?xml version="1.0" encoding="EUC-KR" ?><protocol><chartdata>'
        '<item data="20260301|1000" />'
        "</chartdata></protocol>"
    ).encode("euc-kr")

    with pytest.raises(ValueError, match="Unexpected Naver chart item"):
        parse_naver_daily_chart_xml(content)
