"""Unit tests for ChannelMetadata dataclass."""

import unittest

import pyarrow as pa

from libxrk.base import ChannelMetadata, LogFile


class TestChannelMetadataRoundtrip(unittest.TestCase):
    """Tests for ChannelMetadata serialization roundtrips."""

    def test_roundtrip_all_fields(self):
        """Construct → to_field_metadata() → attach to field → from_field() → equal."""
        original = ChannelMetadata(
            units="rpm",
            dec_pts=2,
            interpolate=True,
            function="Engine RPM",
            source_type=1,
            source_channel_id=42,
            device_tag="@AIM",
            cal_value_1=0.5,
            cal_value_2=2.0,
            display_range_min=-100.0,
            display_range_max=10000.0,
        )

        field = pa.field("test", pa.float32(), metadata=original.to_field_metadata())
        restored = ChannelMetadata.from_field(field)

        self.assertEqual(restored, original)

    def test_defaults_roundtrip(self):
        """Default ChannelMetadata should roundtrip correctly."""
        original = ChannelMetadata()

        field = pa.field("test", pa.float32(), metadata=original.to_field_metadata())
        restored = ChannelMetadata.from_field(field)

        self.assertEqual(restored, original)

    def test_interpolate_true_roundtrip(self):
        """interpolate=True should serialize as 'True' and roundtrip."""
        meta = ChannelMetadata(interpolate=True)
        raw = meta.to_field_metadata()
        self.assertEqual(raw[b"interpolate"], b"True")

        field = pa.field("test", pa.float32(), metadata=raw)
        restored = ChannelMetadata.from_field(field)
        self.assertTrue(restored.interpolate)

    def test_interpolate_false_roundtrip(self):
        """interpolate=False should serialize as 'False' and roundtrip."""
        meta = ChannelMetadata(interpolate=False)
        raw = meta.to_field_metadata()
        self.assertEqual(raw[b"interpolate"], b"False")

        field = pa.field("test", pa.float32(), metadata=raw)
        restored = ChannelMetadata.from_field(field)
        self.assertFalse(restored.interpolate)


class TestChannelMetadataFromField(unittest.TestCase):
    """Tests for ChannelMetadata.from_field() and from_channel_table()."""

    def test_from_field_missing_metadata(self):
        """from_field with no metadata should return defaults."""
        field = pa.field("test", pa.float32())
        meta = ChannelMetadata.from_field(field)

        self.assertEqual(meta, ChannelMetadata())

    def test_from_field_partial_metadata(self):
        """from_field with only some keys should fill defaults for the rest."""
        field = pa.field("test", pa.float32(), metadata={b"units": b"rpm", b"dec_pts": b"3"})
        meta = ChannelMetadata.from_field(field)

        self.assertEqual(meta.units, "rpm")
        self.assertEqual(meta.dec_pts, 3)
        self.assertFalse(meta.interpolate)
        self.assertEqual(meta.function, "")
        self.assertEqual(meta.cal_value_2, 1.0)

    def test_from_field_empty_string_values(self):
        """from_field should handle empty string values for numeric fields."""
        field = pa.field(
            "test",
            pa.float32(),
            metadata={b"dec_pts": b"", b"source_type": b"", b"cal_value_1": b""},
        )
        meta = ChannelMetadata.from_field(field)

        self.assertEqual(meta.dec_pts, 0)
        self.assertEqual(meta.source_type, 0)
        self.assertEqual(meta.cal_value_1, 0.0)

    def test_from_channel_table(self):
        """ChannelMetadata.from_channel_table extracts metadata from a table."""
        meta = ChannelMetadata(units="m/s", dec_pts=1, interpolate=True)
        table = pa.table(
            {
                "timecodes": pa.array([0, 100], type=pa.int64()),
                "speed": pa.array([1.0, 2.0], type=pa.float32()),
            }
        )
        field = table.schema.field("speed").with_metadata(meta.to_field_metadata())
        table = table.cast(pa.schema([table.schema.field("timecodes"), field]))

        restored = ChannelMetadata.from_channel_table(table, "speed")

        self.assertEqual(restored.units, "m/s")
        self.assertEqual(restored.dec_pts, 1)
        self.assertTrue(restored.interpolate)


class TestChannelMetadataToFieldMetadata(unittest.TestCase):
    """Tests for ChannelMetadata.to_field_metadata() serialization."""

    def test_all_keys_present(self):
        """to_field_metadata should include all 11 metadata keys."""
        meta = ChannelMetadata()
        raw = meta.to_field_metadata()

        expected_keys = {
            b"units",
            b"dec_pts",
            b"interpolate",
            b"function",
            b"source_type",
            b"source_channel_id",
            b"device_tag",
            b"cal_value_1",
            b"cal_value_2",
            b"display_range_min",
            b"display_range_max",
        }
        self.assertEqual(set(raw.keys()), expected_keys)

    def test_all_values_are_bytes(self):
        """to_field_metadata should produce all bytes keys and values."""
        meta = ChannelMetadata(
            units="rpm", dec_pts=2, interpolate=True, source_type=5, cal_value_1=1.5
        )
        raw = meta.to_field_metadata()

        for key, value in raw.items():
            self.assertIsInstance(key, bytes, f"Key {key!r} is not bytes")
            self.assertIsInstance(value, bytes, f"Value for {key!r} is not bytes")


class TestChannelMetadataProperties(unittest.TestCase):
    """Tests for ChannelMetadata dataclass properties."""

    def test_frozen(self):
        """ChannelMetadata should be immutable."""
        meta = ChannelMetadata(units="rpm")
        with self.assertRaises(AttributeError):
            meta.units = "m/s"  # type: ignore[misc]

    def test_equality(self):
        """Two ChannelMetadata with same fields should be equal."""
        a = ChannelMetadata(units="rpm", dec_pts=2, interpolate=True)
        b = ChannelMetadata(units="rpm", dec_pts=2, interpolate=True)
        self.assertEqual(a, b)

    def test_inequality(self):
        """Two ChannelMetadata with different fields should not be equal."""
        a = ChannelMetadata(units="rpm")
        b = ChannelMetadata(units="m/s")
        self.assertNotEqual(a, b)

    def test_default_values(self):
        """Default ChannelMetadata should have expected defaults."""
        meta = ChannelMetadata()
        self.assertEqual(meta.units, "")
        self.assertEqual(meta.dec_pts, 0)
        self.assertFalse(meta.interpolate)
        self.assertEqual(meta.function, "")
        self.assertEqual(meta.source_type, 0)
        self.assertEqual(meta.source_channel_id, 0)
        self.assertEqual(meta.device_tag, "")
        self.assertEqual(meta.cal_value_1, 0.0)
        self.assertEqual(meta.cal_value_2, 1.0)
        self.assertEqual(meta.display_range_min, 0.0)
        self.assertEqual(meta.display_range_max, 0.0)


class TestChannelMetadataPreservation(unittest.TestCase):
    """Tests that ChannelMetadata survives LogFile operations."""

    def _make_channel(self, name: str, meta: ChannelMetadata) -> pa.Table:
        table = pa.table(
            {
                "timecodes": pa.array([0, 100, 200], type=pa.int64()),
                name: pa.array([1.0, 2.0, 3.0], type=pa.float32()),
            }
        )
        field = table.schema.field(name).with_metadata(meta.to_field_metadata())
        return table.cast(pa.schema([table.schema.field("timecodes"), field]))

    def _make_log(self, channels: dict[str, pa.Table]) -> LogFile:
        return LogFile(
            channels=channels,
            laps=pa.table(
                {
                    "num": pa.array([0], type=pa.int32()),
                    "start_time": pa.array([0], type=pa.int64()),
                    "end_time": pa.array([300], type=pa.int64()),
                }
            ),
            metadata={},
            file_name="test.xrk",
        )

    def test_preserved_through_select_channels(self):
        """Metadata should survive select_channels."""
        meta = ChannelMetadata(units="rpm", dec_pts=2, interpolate=True, function="Engine RPM")
        log = self._make_log({"RPM": self._make_channel("RPM", meta)})

        result = log.select_channels(["RPM"])
        restored = ChannelMetadata.from_channel_table(result.channels["RPM"], "RPM")
        self.assertEqual(restored, meta)

    def test_preserved_through_filter_by_time_range(self):
        """Metadata should survive filter_by_time_range."""
        meta = ChannelMetadata(units="m/s", dec_pts=1, interpolate=True)
        log = self._make_log({"Speed": self._make_channel("Speed", meta)})

        result = log.filter_by_time_range(0, 150)
        restored = ChannelMetadata.from_channel_table(result.channels["Speed"], "Speed")
        self.assertEqual(restored, meta)

    def test_preserved_through_filter_by_lap(self):
        """Metadata should survive filter_by_lap."""
        meta = ChannelMetadata(units="deg", dec_pts=1, interpolate=True, device_tag="@AIM")
        log = self._make_log({"Steering": self._make_channel("Steering", meta)})

        result = log.filter_by_lap(0)
        restored = ChannelMetadata.from_channel_table(result.channels["Steering"], "Steering")
        self.assertEqual(restored, meta)

    def test_preserved_through_resample_to_timecodes(self):
        """Metadata should survive resample_to_timecodes."""
        meta = ChannelMetadata(units="rpm", dec_pts=0, interpolate=True, source_type=9)
        log = self._make_log({"RPM": self._make_channel("RPM", meta)})

        target = pa.array([0, 50, 100, 150, 200], type=pa.int64())
        result = log.resample_to_timecodes(target)
        restored = ChannelMetadata.from_channel_table(result.channels["RPM"], "RPM")
        self.assertEqual(restored, meta)

    def test_preserved_through_get_channels_as_table(self):
        """Metadata should survive get_channels_as_table merge."""
        meta = ChannelMetadata(units="bar", dec_pts=2, interpolate=True)
        log = self._make_log({"BRK": self._make_channel("BRK", meta)})

        result = log.get_channels_as_table()
        restored = ChannelMetadata.from_field(result.schema.field("BRK"))
        self.assertEqual(restored, meta)


if __name__ == "__main__":
    unittest.main()
