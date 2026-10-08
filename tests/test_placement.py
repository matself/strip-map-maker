# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for core.placement (need qgis.core, no GUI)."""
import math
import sys
from pathlib import Path

import pytest

pytest.importorskip("qgis.core")

from qgis.core import QgsGeometry, QgsPointXY  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strip_map_maker.core.placement import (  # noqa: E402
    PlacementError,
    place_frames,
    rectangle,
    uncovered_stretches,
)


def line(points):
    return QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in points])


def arc(radius, degrees, start_deg=0.0, n=360):
    return line(
        [
            (
                radius * math.cos(math.radians(start_deg + degrees * i / n)),
                radius * math.sin(math.radians(start_deg + degrees * i / n)),
            )
            for i in range(n + 1)
        ]
    )


def assert_covered(guide, placement, width):
    polygons = [f.geometry for f in placement.frames]
    assert uncovered_stretches(guide, polygons, width * 1e-3) == []


def test_straight_line_exact_fit():
    guide = line([(0, 0), (1000, 0)])
    result = place_frames(guide, 100, 60, 10)
    assert len(result.frames) == 11
    assert result.spacing == pytest.approx(90)
    assert result.overlap_pct == pytest.approx(10)
    assert result.frames[0].from_m == pytest.approx(0)
    assert result.frames[-1].to_m == pytest.approx(1000)
    assert [f.id for f in result.frames] == list(range(1, 12))
    assert result.uncovered == []


def test_leftover_is_spread_evenly_never_less_overlap():
    guide = line([(0, 0), (1005, 0)])
    result = place_frames(guide, 100, 60, 10)
    assert len(result.frames) == 12
    assert result.spacing == pytest.approx(905 / 11)
    assert result.overlap_pct >= 10
    centres = [f.x for f in result.frames]
    gaps = [b - a for a, b in zip(centres, centres[1:])]
    # even to within a few percent of the frame width (the last step is a touch shorter)
    assert max(gaps) - min(gaps) < 0.03 * 100
    assert result.frames[0].from_m == pytest.approx(0)
    assert result.frames[-1].to_m == pytest.approx(1005)


def test_short_line_gets_one_frame():
    result = place_frames(line([(0, 0), (60, 0)]), 100, 60, 10)
    assert len(result.frames) == 1
    assert (result.frames[0].x, result.frames[0].y) == pytest.approx((30, 0))
    assert result.frames[0].from_m == pytest.approx(0)
    assert result.frames[0].to_m == pytest.approx(60)


def test_azimuth_convention_north_clockwise():
    east = place_frames(line([(0, 0), (500, 0)]), 100, 60, 0).frames[0]
    north = place_frames(line([(0, 0), (0, 500)]), 100, 60, 0).frames[0]
    south = place_frames(line([(0, 0), (0, -500)]), 100, 60, 0).frames[0]
    assert east.azi == pytest.approx(90)
    assert north.azi == pytest.approx(0)
    assert south.azi == pytest.approx(180)


def test_rectangle_dimensions_follow_azimuth():
    box = rectangle(0, 0, 90, 100, 40)  # long side east-west
    bbox = box.boundingBox()
    assert bbox.width() == pytest.approx(100)
    assert bbox.height() == pytest.approx(40)
    assert box.area() == pytest.approx(4000)


def test_gentle_arc_is_covered_without_extra_frames():
    guide = arc(300, 180)
    result = place_frames(guide, 100, 80, 10)
    nominal = math.ceil((guide.length() - 100) / 90) + 1
    assert len(result.frames) == nominal
    assert_covered(guide, result, 100)


def test_tight_curve_gets_more_frames_and_is_covered():
    guide = arc(60, 270)
    result = place_frames(guide, 150, 40, 10)
    nominal = math.ceil((guide.length() - 150) / 135) + 1
    assert len(result.frames) > nominal
    assert result.uncovered == []
    assert_covered(guide, result, 150)


def test_impossible_curve_reports_uncovered_instead_of_failing():
    guide = arc(100, 359)
    result = place_frames(guide, 100, 0.2, 10)
    assert result.uncovered


def test_s_curve_covered_and_chainage_monotonic():
    pts = [(x, 80 * math.sin(x / 60)) for x in range(0, 800, 4)]
    guide = line(pts)
    result = place_frames(guide, 120, 120, 15)
    assert_covered(guide, result, 120)
    starts = [f.from_m for f in result.frames]
    assert starts == sorted(starts)
    assert result.frames[0].from_m == pytest.approx(0, abs=1e-6)
    assert result.frames[-1].to_m == pytest.approx(guide.length(), abs=1e-6)


def test_closed_loop_is_covered():
    ring = line([(0, 0), (300, 0), (300, 300), (0, 300), (0, 0)])
    result = place_frames(ring, 100, 60, 10)
    assert_covered(ring, result, 100)
    assert all(0 <= f.azi < 360 for f in result.frames)


def test_frames_are_centred_sideways_on_a_curve():
    guide = arc(100, 90)
    frame = place_frames(guide, 100, 60, 0).frames[0]
    # centred on the route's span, so the centre is off the route by about half the sagitta
    distance = guide.distance(QgsGeometry.fromPointXY(QgsPointXY(frame.x, frame.y)))
    sagitta = 100 - math.sqrt(100**2 - 50**2)
    assert distance == pytest.approx(sagitta / 2, rel=0.2)


def test_invalid_input_raises():
    guide = line([(0, 0), (100, 0)])
    with pytest.raises(PlacementError):
        place_frames(guide, 0, 10, 10)
    with pytest.raises(PlacementError):
        place_frames(guide, 10, 10, 100)
    with pytest.raises(PlacementError):
        place_frames(guide, 10, 10, -1)


def test_cover_line_decides_what_must_be_covered():
    river = line([(x, 60 * math.sin(x / 50)) for x in range(0, 600, 5)])
    straight = line([(0, 0), (600, 0)])
    plain = place_frames(straight, 100, 40, 10)
    assert uncovered_stretches(river, [f.geometry for f in plain.frames], 0.1)
    covered = place_frames(straight, 100, 40, 10, cover=river)
    assert len(covered.frames) >= len(plain.frames)
    assert isinstance(covered.uncovered, list)


def test_bends_overlap_evenly_and_never_below_request():
    guide = line([(60 * math.sin(i / 6), i * 40) for i in range(60)])
    result = place_frames(guide, 280, 180, 10)
    assert not result.uncovered
    frames = result.frames
    area = frames[0].geometry.area()
    overlaps = [
        frames[i].geometry.intersection(frames[i + 1].geometry).area() / area * 100
        for i in range(len(frames) - 1)
    ]
    assert min(overlaps) >= 10 - 0.5
    # the last pair is no longer an outlier: the leftover is shared by all overlaps
    assert max(overlaps) - min(overlaps) < 3
