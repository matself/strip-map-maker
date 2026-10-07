# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for core.guide (need qgis.core, no GUI)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsGeometry, QgsPointXY  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strip_map_maker.core.guide import (  # noqa: E402
    GuideError,
    chain_lines,
    reverse_line,
    single_line,
    smooth_line,
)


def line(*points):
    return QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in points])


def coords(geometry):
    return [(p.x(), p.y()) for p in geometry.asPolyline()]


def test_single_line_is_returned_unchanged():
    result = chain_lines([line((0, 0), (10, 0))])
    assert coords(result.geometry) == [(0, 0), (10, 0)]
    assert result.pieces == 1 and result.gaps == []


def test_chains_in_order():
    result = chain_lines([line((0, 0), (10, 0)), line((10, 0), (20, 5))])
    assert coords(result.geometry) == [(0, 0), (10, 0), (20, 5)]
    assert result.pieces == 2 and result.max_gap == 0


def test_flips_a_reversed_part():
    result = chain_lines([line((0, 0), (10, 0)), line((20, 5), (10, 0))])
    assert coords(result.geometry) == [(0, 0), (10, 0), (20, 5)]


def test_selection_order_does_not_matter():
    # the first selected part is in the middle; the chain grows at both ends
    parts = [line((10, 0), (20, 0)), line((30, 0), (20, 0)), line((0, 0), (10, 0))]
    result = chain_lines(parts)
    assert coords(result.geometry) == [(0, 0), (10, 0), (20, 0), (30, 0)]
    assert result.pieces == 3


def test_direction_follows_first_part():
    result = chain_lines([line((10, 0), (0, 0)), line((10, 0), (20, 0))])
    assert coords(result.geometry) == [(20, 0), (10, 0), (0, 0)]


def test_small_gap_is_bridged_and_reported():
    result = chain_lines([line((0, 0), (10, 0)), line((10.5, 0), (20, 0))], tolerance=1.0)
    assert coords(result.geometry) == [(0, 0), (10, 0), (10.5, 0), (20, 0)]
    assert result.gaps == pytest.approx([0.5])


def test_gap_before_the_head_is_bridged():
    result = chain_lines([line((10, 0), (20, 0)), line((0, 0), (9.5, 0))], tolerance=1.0)
    assert coords(result.geometry) == [(0, 0), (9.5, 0), (10, 0), (20, 0)]
    assert result.gaps == pytest.approx([0.5])


def test_large_gap_raises():
    with pytest.raises(GuideError, match="not connected"):
        chain_lines([line((0, 0), (10, 0)), line((50, 0), (60, 0))], tolerance=1.0)


def test_multipart_feature_is_chained():
    multi = QgsGeometry.fromMultiPolylineXY(
        [
            [QgsPointXY(10, 0), QgsPointXY(20, 0)],
            [QgsPointXY(0, 0), QgsPointXY(10, 0)],
        ]
    )
    result = chain_lines([multi])
    assert coords(result.geometry) == [(0, 0), (10, 0), (20, 0)]
    assert result.pieces == 2


def test_closed_loop_single_feature():
    ring = line((0, 0), (10, 0), (10, 10), (0, 10), (0, 0))
    assert chain_lines([ring]).geometry.length() == pytest.approx(40)


def test_empty_and_non_line_input():
    with pytest.raises(GuideError):
        chain_lines([])
    with pytest.raises(GuideError, match="line"):
        chain_lines([QgsGeometry.fromPointXY(QgsPointXY(0, 0))])


def test_reverse_line():
    assert coords(reverse_line(line((0, 0), (5, 0), (5, 5)))) == [(5, 5), (5, 0), (0, 0)]


def test_single_line_rejects_multipart_and_zero_length():
    multi = QgsGeometry.fromMultiPolylineXY(
        [[QgsPointXY(0, 0), QgsPointXY(1, 0)], [QgsPointXY(5, 5), QgsPointXY(6, 5)]]
    )
    with pytest.raises(GuideError):
        single_line(multi)
    with pytest.raises(GuideError):
        single_line(line((1, 1), (1, 1)))


def test_smoothing_flattens_meanders_and_keeps_ends():
    wiggly = line(*[(x, 20 * (1 if (x // 20) % 2 else -1)) for x in range(0, 400, 5)])
    smooth = smooth_line(wiggly, window=100)
    ys = [p.y() for p in smooth.asPolyline()[5:-5]]
    assert max(abs(y) for y in ys) < 10  # amplitude 20 flattened
    assert coords(smooth)[0] == pytest.approx(coords(wiggly)[0])
    assert coords(smooth)[-1] == pytest.approx(coords(wiggly)[-1])


def test_zero_window_leaves_line_unchanged():
    original = line((0, 0), (10, 5), (20, 0))
    assert coords(smooth_line(original, 0)) == coords(original)
