"""Tests for daily candle trade_value parsing."""

from __future__ import annotations

from broker.kis.models import KisResponse
from broker.kis.parsers import parse_daily_candles


def test_parse_daily_candles_includes_trade_value_column():
    response = KisResponse(
        body={
            "output2": [
                {
                    "stck_bsop_date": "20260413",
                    "stck_oprc": "70000",
                    "stck_hgpr": "71000",
                    "stck_lwpr": "69000",
                    "stck_clpr": "70500",
                    "acml_vol": "15000000",
                    "acml_tr_pbmn": "950000000000",
                }
            ]
        },
        rt_cd="0",
        msg_cd="0",
        msg="ok",
        tr_cont="",
        tr_id="FHKST03010100",
        http_status=200,
    )

    df = parse_daily_candles(response)

    assert list(df.columns) == [
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_value",
    ]
    assert int(df.iloc[0]["trade_value"]) == 950_000_000_000
