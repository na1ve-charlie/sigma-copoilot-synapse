from synapse.domains.observation.catalog import (
    ObservationCatalog,
    ObservationCatalogEntry,
    facets_for,
)


def test_catalog_dedupes_single_sensor_field() -> None:
    catalog = ObservationCatalog(
        [
            ObservationCatalogEntry(sensors="Vib1"),
            ObservationCatalogEntry(sensors="Vib1"),
            ObservationCatalogEntry(sensors="Vib2"),
        ]
    )

    assert catalog.distinct_entries(("sensors",)) == [
        {"sensors": "Vib1"},
        {"sensors": "Vib2"},
    ]


def test_catalog_dedupes_single_test_segment_field() -> None:
    catalog = ObservationCatalog(
        [
            ObservationCatalogEntry(test_segments="runup"),
            ObservationCatalogEntry(test_segments="runup"),
            ObservationCatalogEntry(test_segments="coast"),
        ]
    )

    assert catalog.distinct_entries(("test_segments",)) == [
        {"test_segments": "runup"},
        {"test_segments": "coast"},
    ]


def test_catalog_dedupes_sensor_and_test_segment_tuples() -> None:
    catalog = ObservationCatalog(
        [
            ObservationCatalogEntry(sensors="Vib1", test_segments="runup"),
            ObservationCatalogEntry(sensors="Vib1", test_segments="runup"),
            ObservationCatalogEntry(sensors="Vib1", test_segments="coast"),
            ObservationCatalogEntry(sensors="Vib2", test_segments="runup"),
        ]
    )

    assert catalog.distinct_entries(("sensors", "test_segments")) == [
        {"sensors": "Vib1", "test_segments": "runup"},
        {"sensors": "Vib1", "test_segments": "coast"},
        {"sensors": "Vib2", "test_segments": "runup"},
    ]


def test_catalog_dedupes_sensor_segment_indicator_tuples() -> None:
    catalog = ObservationCatalog(
        [
            ObservationCatalogEntry(
                sensors="Vib1",
                test_segments="runup",
                indicator_names="RMS",
            ),
            ObservationCatalogEntry(
                sensors="Vib1",
                test_segments="runup",
                indicator_names="RMS",
            ),
            ObservationCatalogEntry(
                sensors="Vib1",
                test_segments="runup",
                indicator_names="Peak",
            ),
        ]
    )

    assert catalog.distinct_entries(
        ("sensors", "test_segments", "indicator_names")
    ) == [
        {
            "sensors": "Vib1",
            "test_segments": "runup",
            "indicator_names": "RMS",
        },
        {
            "sensors": "Vib1",
            "test_segments": "runup",
            "indicator_names": "Peak",
        },
    ]


def test_facets_are_derived_per_column_without_encoding_combinations() -> None:
    entries = [
        {"sensors": "Vib1", "test_segments": "runup"},
        {"sensors": "Vib2", "test_segments": "coast"},
    ]

    assert facets_for(entries, ("sensors", "test_segments")) == [
        {"slot_name": "sensors", "candidates": ["Vib1", "Vib2"]},
        {"slot_name": "test_segments", "candidates": ["runup", "coast"]},
    ]


def test_empty_catalog_returns_empty_entries_and_facets() -> None:
    catalog = ObservationCatalog([])
    entries = catalog.distinct_entries(("sensors", "test_segments"))

    assert entries == []
    assert facets_for(entries, ("sensors", "test_segments")) == []
