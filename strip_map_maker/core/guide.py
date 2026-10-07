# SPDX-License-Identifier: GPL-2.0-or-later
"""The guide line: the single line a map series follows.

A series has exactly one guide line. It is built either from one or more existing
line features (chained end to end) or from a line the user draws. The source
features are never modified; the guide is always a new, single-part geometry.
"""
import math
from dataclasses import dataclass, field

from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes

# Points closer than this (map units) are treated as the same point when joining.
COINCIDENT = 1e-9


class GuideError(ValueError):
    """The input cannot be turned into a single guide line."""


@dataclass
class ChainResult:
    """A chained guide line plus a report on how it was put together."""

    geometry: QgsGeometry
    pieces: int
    gaps: list[float] = field(default_factory=list)  # distance bridged at each join

    @property
    def max_gap(self) -> float:
        return max(self.gaps, default=0.0)


def _parts(geometry: QgsGeometry) -> list[list[QgsPointXY]]:
    """Return every line part of ``geometry`` as a list of 2D points."""
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return []
    if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.GeometryType.LineGeometry:
        raise GuideError("Only line geometries can be used as a guide.")
    if geometry.isMultipart():
        raw = geometry.asMultiPolyline()
    else:
        raw = [geometry.asPolyline()]
    parts = []
    for part in raw:
        points = [QgsPointXY(p) for p in part]
        if len(points) >= 2:
            parts.append(points)
    return parts


def chain_lines(geometries, tolerance: float = 1.0) -> ChainResult:
    """Join ``geometries`` into one line, regardless of selection order or direction.

    Starting from the first part, the chain is extended at whichever end has the
    nearest unused part, flipping parts as needed. The direction of the result
    follows the first part. Joins up to ``tolerance`` (map units) are bridged with
    a straight segment and reported in ``ChainResult.gaps``.

    Raises ``GuideError`` if nothing usable is given or a part is farther than
    ``tolerance`` from both ends of the chain (the lines do not form one route).
    """
    remaining = []
    for geometry in geometries:
        remaining.extend(_parts(geometry))
    if not remaining:
        raise GuideError("No line geometry to build a guide from.")

    chain = remaining.pop(0)
    gaps: list[float] = []
    pieces = 1

    while remaining:
        head, tail = chain[0], chain[-1]
        best = None  # (distance, index, attach_to_tail, flip)
        for i, part in enumerate(remaining):
            # attached to the tail a part must start at the tail, so flip it if its
            # last point is the near one; attached to the head it must end at the head
            candidates = (
                (tail.distance(part[0]), True, False),
                (tail.distance(part[-1]), True, True),
                (head.distance(part[-1]), False, False),
                (head.distance(part[0]), False, True),
            )
            for distance, attach_tail, flip in candidates:
                if best is None or distance < best[0]:
                    best = (distance, i, attach_tail, flip)
        distance, index, attach_tail, flip = best
        if distance > tolerance:
            raise GuideError(
                f"The selected lines are not connected: the nearest remaining line is "
                f"{distance:.2f} map units from the route (tolerance {tolerance:.2f})."
            )
        part = remaining.pop(index)
        if flip:
            part = part[::-1]
        if attach_tail:
            chain = chain + (part[1:] if distance <= COINCIDENT else part)
        else:
            chain = (part[:-1] if distance <= COINCIDENT else part) + chain
        if distance > COINCIDENT:
            gaps.append(distance)
        pieces += 1

    return ChainResult(QgsGeometry.fromPolylineXY(chain), pieces, gaps)


def reverse_line(geometry: QgsGeometry) -> QgsGeometry:
    """Return the guide line with its direction reversed (page 1 moves to the other end)."""
    parts = _parts(geometry)
    if len(parts) != 1:
        raise GuideError("A guide line must be a single part.")
    return QgsGeometry.fromPolylineXY(parts[0][::-1])


def single_line(geometry: QgsGeometry) -> QgsGeometry:
    """Validate a drawn or stored line as a guide and return it as a single 2D part."""
    parts = _parts(geometry)
    if len(parts) != 1:
        raise GuideError("A guide line must be a single connected line.")
    line = QgsGeometry.fromPolylineXY(parts[0])
    if line.length() <= 0:
        raise GuideError("The guide line has zero length.")
    return line


def smooth_line(geometry: QgsGeometry, window: float) -> QgsGeometry:
    """Smooth a guide with a moving average along the line.

    ``window`` is the averaging length in map units: meanders and wiggles shorter
    than about this length are flattened, larger bends are kept. Both end points stay
    fixed and the window shrinks towards them. ``window <= 0`` returns the line as is.
    """
    line = single_line(geometry)
    length = line.length()
    if window <= 0 or length <= 0:
        return line
    count = max(4, math.ceil(length / (window / 8)))  # about 8 samples per window
    step = length / count
    points = [line.interpolate(i * step).asPoint() for i in range(count + 1)]
    reach = round(window / 2 / step)
    smoothed = []
    for i in range(count + 1):
        r = min(reach, i, count - i)
        chunk = points[i - r : i + r + 1]
        smoothed.append(
            QgsPointXY(
                sum(p.x() for p in chunk) / len(chunk), sum(p.y() for p in chunk) / len(chunk)
            )
        )
    return QgsGeometry.fromPolylineXY(smoothed)
