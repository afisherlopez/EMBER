"""Tests for the slim burn-severity tile service."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from rio_tiler.errors import EmptyMosaicError

from core.tiler import main


def test_healthz_reports_ready() -> None:
    assert main.healthz() == {"status": "ok"}


def test_tile_endpoint_orders_selected_years_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[tuple[int, ...]] = []
    monkeypatch.setattr(
        main,
        "_burn_severity_assets",
        lambda: {
            2020: "gs://bucket/2020.tif",
            2022: "gs://bucket/2022.tif",
            2024: "gs://bucket/2024.tif",
        },
    )
    monkeypatch.setattr(
        main,
        "_render_burn_severity_tile",
        lambda _z, _x, _y, years: rendered.append(years) or b"png",
    )

    response = main.burn_severity_tile(5, 10, 12, "2020,2024,2022")

    assert rendered == [(2024, 2022, 2020)]
    assert response.body == b"png"
    assert response.media_type == "image/png"


@pytest.mark.parametrize(
    ("years", "status_code"),
    [
        ("not-a-year", 422),
        ("2023", 404),
        (",", 422),
    ],
)
def test_tile_endpoint_rejects_invalid_years(
    monkeypatch: pytest.MonkeyPatch,
    years: str,
    status_code: int,
) -> None:
    monkeypatch.setattr(
        main,
        "_burn_severity_assets",
        lambda: {2024: "gs://bucket/2024.tif"},
    )

    with pytest.raises(HTTPException) as error:
        main.burn_severity_tile(5, 10, 12, years)

    assert error.value.status_code == status_code


def test_empty_mosaic_returns_transparent_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main,
        "_burn_severity_assets",
        lambda: {2024: "gs://bucket/2024.tif"},
    )

    def empty_mosaic(*_args: object, **_kwargs: object) -> None:
        raise EmptyMosaicError

    monkeypatch.setattr(main, "mosaic_reader", empty_mosaic)
    main._render_burn_severity_tile.cache_clear()
    try:
        tile = main._render_burn_severity_tile(5, 10, 12, (2024,))
    finally:
        main._render_burn_severity_tile.cache_clear()

    assert tile == main._TRANSPARENT_TILE
