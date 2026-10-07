# SPDX-License-Identifier: GPL-2.0-or-later
"""Place map frames along a guide line.

Every frame is a rectangle of fixed ground size. The first frame starts at the
beginning of the guide and the last one ends at its end. In between the frames are
spaced equally, so the leftover length is spread evenly over all overlaps (the
overlap is therefore never smaller than requested). Frames are oriented along the
chord of the route inside the frame and centred on the route sideways, so curves
stay inside the frame. If a curve is too tight for the requested spacing, more
frames are added until the whole guide is covered.

All lengths are in map units, so the guide must be in a projected CRS in metres.
"""
import math
from dataclasses import dataclass, field

from qgis.core import QgsGeometry, QgsPointXY

# How many points are sampled across a frame to centre it on the route sideways.
SAMPLES = 21
# Upper bound on extra frames added for tight curves (relative to the nominal count).
MAX_EXTRA_FACTOR = 4


class PlacementError(ValueError):
    """The requested frame size, overlap or guide line cannot be used."""


@dataclass
class Frame:
    id: int  # 1-based, in order along the guide
    x: float  # centre
    y: float
    azi: float  # bearing of the long (along-route) axis, degrees, 0 = north, clockwise
    from_m: float  # chainage where the guide enters the frame
    to_m: float  # chainage where the guide leaves the frame
    geometry: QgsGeometry


@dataclass
class Placement:
    frames: list[Frame]
    spacing: float  # distance between frame centres along the guide (0 for one frame)
    overlap_pct: float  # overlap actually achieved, as % of the frame width
    uncovered: list[tuple[float, float]] = field(default_factory=list)  # chainage stretches


def rectangle(cx: float, cy: float, azimuth_deg: float, width: float, height: float):
    """Rectangle centred on (cx, cy) whose ``width`` axis points along ``azimuth_deg``."""
    a = math.radians(azimuth_deg)
    ux, uy = math.sin(a), math.cos(a)  # along the route
    vx, vy = uy, -ux  # across the route
    hw, hh = width / 2, height / 2
    corners = [
        QgsPointXY(cx + ux * hw + vx * hh, cy + uy * hw + vy * hh),
        QgsPointXY(cx + ux * hw - vx * hh, cy + uy * hw - vy * hh),
        QgsPointXY(cx - ux * hw - vx * hh, cy - uy * hw - vy * hh),
        QgsPointXY(cx - ux * hw + vx * hh, cy - uy * hw + vy * hh),
    ]
    return QgsGeometry.fromPolygonXY([corners])


def _point(guide: QgsGeometry, distance: float) -> QgsPointXY:
    return guide.interpolate(distance).asPoint()


def _azimuth(guide: QgsGeometry, centre: float, width: float, length: float) -> float:
    """Bearing of the chord across the frame's span of the route (0..360)."""
    start = max(0.0, centre - width / 2)
    end = min(length, centre + width / 2)
    p0, p1 = _point(guide, start), _point(guide, end)
    dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    if math.hypot(dx, dy) < width * 1e-6:  # a closed loop: chord is degenerate
        delta = width * 0.01
        p0 = _point(guide, max(0.0, centre - delta))
        p1 = _point(guide, min(length, centre + delta))
        dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    return math.degrees(math.atan2(dx, dy)) % 360


def _frame_at(guide, centre, width, height, length):
    azi = _azimuth(guide, centre, width, length)
    anchor = _point(guide, centre)
    a = math.radians(azi)
    vx, vy = math.cos(a), -math.sin(a)
    start = max(0.0, centre - width / 2)
    end = min(length, centre + width / 2)
    offsets = []
    for i in range(SAMPLES):
        p = _point(guide, start + (end - start) * i / (SAMPLES - 1))
        offsets.append((p.x() - anchor.x()) * vx + (p.y() - anchor.y()) * vy)
    shift = (min(offsets) + max(offsets)) / 2
    cx, cy = anchor.x() + vx * shift, anchor.y() + vy * shift
    return cx, cy, azi, rectangle(cx, cy, azi, width, height)


def _chainage(guide: QgsGeometry, point: QgsPointXY) -> float:
    return guide.lineLocatePoint(QgsGeometry.fromPointXY(point))


def _line_parts(geometry: QgsGeometry) -> list[list[QgsPointXY]]:
    if geometry is None or geometry.isEmpty():
        return []
    if geometry.isMultipart():
        return [[QgsPointXY(p) for p in part] for part in geometry.asMultiPolyline()]
    return [[QgsPointXY(p) for p in geometry.asPolyline()]]


def uncovered_stretches(guide, polygons, tolerance: float) -> list[tuple[float, float]]:
    """Chainage ranges of ``guide`` that lie outside every polygon (longer than ``tolerance``)."""
    if not polygons:
        return [(0.0, guide.length())]
    outside = guide.difference(QgsGeometry.unaryUnion(polygons))
    stretches = []
    for part in _line_parts(outside):
        if len(part) < 2 or QgsGeometry.fromPolylineXY(part).length() <= tolerance:
            continue
        a, b = _chainage(guide, part[0]), _chainage(guide, part[-1])
        stretches.append((min(a, b), max(a, b)))
    return sorted(stretches)


def _end_frame(guide, end_point, centre, direction, width, height, length, tolerance):
    """Frame near one end of the guide; slides outward until it contains the end point.

    On a curve the first or last frame, anchored half a width in, can miss the very
    end of the route. Moving it towards the end (``direction`` is -1 for the start,
    +1 for the finish) trades a little blank paper for full coverage.
    """
    target = QgsGeometry.fromPointXY(end_point)
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        shifted = centre + direction * width / 2 * fraction
        shifted = min(max(shifted, 0.0), length)
        result = _frame_at(guide, shifted, width, height, length)
        if result[3].distance(target) <= tolerance:
            return result
    return _frame_at(guide, centre, width, height, length)


def _build(guide, length, width, height, count, tolerance):
    """Frame data for ``count`` equally spaced centres; returns (frames_data, spacing)."""
    if count == 1:
        return [_frame_at(guide, length / 2, width, height, length)], 0.0
    spacing = (length - width) / (count - 1)
    centres = [width / 2 + i * spacing for i in range(count)]
    data = [_frame_at(guide, c, width, height, length) for c in centres]
    data[0] = _end_frame(
        guide, _point(guide, 0.0), centres[0], -1, width, height, length, tolerance
    )
    data[-1] = _end_frame(
        guide, _point(guide, length), centres[-1], 1, width, height, length, tolerance
    )
    return data, spacing


def place_frames(
    guide: QgsGeometry,
    width: float,
    height: float,
    overlap_pct: float,
    cover: QgsGeometry = None,
):
    """Place frames of ``width`` x ``height`` (ground units) along ``guide``.

    ``width`` is measured along the route. ``overlap_pct`` (0..<100) is the minimum
    overlap between neighbouring frames as a share of ``width``.

    ``guide`` decides where the frames go and how they are oriented (it may be a
    smoothed version of the real feature). ``cover`` is the line that must end up
    inside the frames, normally the unsmoothed original; it defaults to ``guide``.
    ``Placement.uncovered`` is measured along ``cover``.
    """
    if width <= 0 or height <= 0:
        raise PlacementError("Frame width and height must be positive.")
    if not 0 <= overlap_pct < 100:
        raise PlacementError("Overlap must be at least 0 % and less than 100 %.")
    if guide is None or guide.isEmpty() or guide.length() <= 0:
        raise PlacementError("The guide line is empty.")
    length = guide.length()
    tolerance = width * 1e-3
    cover = cover if cover is not None else guide

    if length <= width:
        counts = [1]
    else:
        nominal_step = width * (1 - overlap_pct / 100)
        nominal = math.ceil((length - width) / nominal_step - 1e-9) + 1
        counts = None

    def attempt(count):
        data, spacing = _build(guide, length, width, height, count, tolerance)
        gaps = uncovered_stretches(cover, [d[3] for d in data], tolerance)
        return data, spacing, gaps

    if counts is not None:
        best = attempt(1)
    else:
        best = attempt(nominal)
        if best[2]:
            # Too tight a curve: grow the count (doubling, then bisect) until covered.
            limit = nominal * MAX_EXTRA_FACTOR + 8
            low, step, found = nominal, 1, None
            while low + step <= limit:
                candidate = attempt(low + step)
                if not candidate[2]:
                    found = (low + step, candidate)
                    break
                low, step = low + step, step * 2
            if found is not None:
                high, high_result = found
                while high - low > 1:
                    mid = (low + high) // 2
                    candidate = attempt(mid)
                    if candidate[2]:
                        low = mid
                    else:
                        high, high_result = mid, candidate
                best = high_result
            else:
                best = attempt(limit)

    data, spacing, gaps = best
    frames = []
    for i, (cx, cy, azi, polygon) in enumerate(data, start=1):
        inside = guide.intersection(polygon)
        points = [p for part in _line_parts(inside) for p in part]
        if points:
            marks = [_chainage(guide, p) for p in points]
            lo, hi = min(marks), max(marks)
        else:
            lo = hi = 0.0
        frames.append(Frame(i, cx, cy, azi, lo, hi, polygon))
    overlap = 0.0 if spacing == 0 else max(0.0, (1 - spacing / width) * 100)
    return Placement(frames, spacing, overlap, gaps)
