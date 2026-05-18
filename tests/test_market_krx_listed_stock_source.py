"""Tests for KRX listed-stock master source."""

from __future__ import annotations

import pytest

from market import (
    KrxListedStockSource,
    infer_krx_preferred_stock,
    infer_krx_spac,
)


class FakeResponse:
    def __init__(self, payload, *, status_error: Exception | None = None) -> None:
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = []

    def post(self, url, *, data, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse(self.payload)


def test_krx_listed_stock_source_loads_kospi_and_kosdaq_rows():
    session = FakeSession(
        {
            "block1": [
                {
                    "short_code": "005930",
                    "codeName": "Samsung Electronics",
                    "marketCode": "STK",
                },
                {
                    "short_code": "060310",
                    "codeName": "3S",
                    "marketCode": "KSQ",
                },
                {
                    "short_code": "123456",
                    "codeName": "Konex Sample",
                    "marketCode": "KNX",
                },
                {
                    "short_code": "00104K",
                    "codeName": "Alphabetic Code Preferred",
                    "marketCode": "STK",
                },
            ]
        }
    )

    items = KrxListedStockSource(session=session).load()

    assert [(item.symbol, item.name, item.market) for item in items] == [
        ("005930", "Samsung Electronics", "KOSPI"),
        ("060310", "3S", "KOSDAQ"),
    ]
    assert session.calls[0]["data"]["bld"] == "dbms/comm/finder/finder_stkisu"
    assert session.calls[0]["data"]["mktsel"] == "ALL"


def test_krx_listed_stock_source_skips_non_numeric_symbols():
    session = FakeSession(
        {
            "block1": [
                {
                    "short_code": "00104K",
                    "codeName": "Alphabetic Code Preferred",
                    "marketCode": "STK",
                },
                {
                    "short_code": "005930",
                    "codeName": "Samsung Electronics",
                    "marketCode": "STK",
                },
            ]
        }
    )

    items = KrxListedStockSource(session=session).load()

    assert [item.symbol for item in items] == ["005930"]


def test_krx_listed_stock_source_filters_requested_markets():
    session = FakeSession(
        {
            "block1": [
                {
                    "short_code": "005930",
                    "codeName": "Samsung Electronics",
                    "marketCode": "STK",
                },
                {
                    "short_code": "060310",
                    "codeName": "3S",
                    "marketCode": "KSQ",
                },
            ]
        }
    )

    items = KrxListedStockSource(
        session=session,
        markets={"KOSDAQ"},
    ).load()

    assert [item.symbol for item in items] == ["060310"]


def test_krx_listed_stock_source_infers_preferred_stock_and_spac():
    session = FakeSession(
        {
            "block1": [
                {
                    "short_code": "005935",
                    "codeName": "Samsung Electronics Preferred",
                    "marketCode": "STK",
                },
                {
                    "short_code": "123450",
                    "codeName": "ABC SPAC",
                    "marketCode": "KSQ",
                },
            ]
        }
    )

    items = KrxListedStockSource(session=session).load()

    assert items[0].is_preferred_stock is False
    assert items[1].is_spac is True


def test_krx_name_inference_helpers_cover_korean_suffixes():
    assert infer_krx_preferred_stock("Samsung Electronics Preferred") is False
    assert infer_krx_preferred_stock("LG\uc804\uc790\uc6b0") is True
    assert infer_krx_preferred_stock("\ud604\ub300\ucc282\uc6b0B") is True
    assert infer_krx_spac("\uc0d8\ud50c\uc2a4\ud329") is True
    assert infer_krx_spac("Sample SPAC") is True


def test_krx_listed_stock_source_rejects_bad_shape():
    session = FakeSession({"not_block1": []})

    with pytest.raises(ValueError, match="block1"):
        KrxListedStockSource(session=session).load()


def test_krx_listed_stock_source_rejects_duplicate_symbol():
    session = FakeSession(
        {
            "block1": [
                {
                    "short_code": "005930",
                    "codeName": "Samsung Electronics",
                    "marketCode": "STK",
                },
                {
                    "short_code": "005930",
                    "codeName": "Samsung Duplicate",
                    "marketCode": "STK",
                },
            ]
        }
    )

    with pytest.raises(ValueError, match="Duplicate"):
        KrxListedStockSource(session=session).load()
