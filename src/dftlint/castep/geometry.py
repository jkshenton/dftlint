"""Small pure-Python lattice geometry (no numpy needed)."""

from __future__ import annotations

import math
from itertools import product


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(v) -> float:
    return math.sqrt(_dot(v, v))


class Lattice:
    """Three lattice vectors in angstrom (rows)."""

    def __init__(self, vectors):
        self.vectors = tuple(tuple(float(x) for x in v) for v in vectors)

    @property
    def volume(self) -> float:
        a, b, c = self.vectors
        return _dot(a, _cross(b, c))

    def reciprocal(self):
        """Reciprocal vectors *without* the 2*pi factor: b_i . a_j = delta_ij (1/angstrom)."""
        a, b, c = self.vectors
        vol = self.volume
        return tuple(tuple(x / vol for x in v) for v in (_cross(b, c), _cross(c, a), _cross(a, b)))

    def minkowski_reduced(self) -> "Lattice":
        """An equivalent lattice whose shortest vectors are the first three (Minkowski reduction).

        In a reduced cell the minimum image of any vector is among the 27 combinations of
        {-1, 0, 1} cell vectors, which is what makes the +/-1 image search in `closest_pair`
        safe for strongly skewed cells (the same approach as ASE's `find_mic`).
        """
        basis = [list(v) for v in self.vectors]
        for _ in range(1000):
            basis.sort(key=lambda v: _dot(v, v))
            changed = False
            for i in (1, 2):
                for coeffs in product((-1, 0, 1), repeat=2):
                    others = [k for k in range(3) if k != i]
                    cand = [basis[i][x] + sum(c * basis[k][x] for c, k in zip(coeffs, others)) for x in range(3)]
                    if _dot(cand, cand) < _dot(basis[i], basis[i]) * (1 - 1e-12):
                        basis[i], changed = cand, True
            if not changed:
                break
        return Lattice(basis)

    def to_cart(self, frac):
        return tuple(sum(frac[k] * self.vectors[k][i] for k in range(3)) for i in range(3))

    def to_frac(self, cart):
        return tuple(_dot(cart, b) for b in self.reciprocal())


def lattice_from_abc(a, b, c, alpha, beta, gamma) -> Lattice | None:
    """Standard orientation (a along x, b in the xy plane). Angles in degrees."""
    if min(a, b, c) <= 0:
        return None
    ca, cb, cg = (math.cos(math.radians(x)) for x in (alpha, beta, gamma))
    sg = math.sin(math.radians(gamma))
    if abs(sg) < 1e-9:
        return None
    cx = c * cb
    cy = c * (ca - cb * cg) / sg
    cz2 = c * c - cx * cx - cy * cy
    if cz2 <= 0:
        return None
    return Lattice([(a, 0.0, 0.0), (b * cg, b * sg, 0.0), (cx, cy, math.sqrt(cz2))])


def implied_kspacing(lattice: Lattice, grid) -> float:
    """Largest k-point spacing |b_i|/n_i (1/angstrom, no 2*pi factor) implied by an MP grid."""
    return max(norm(b) / n for b, n in zip(lattice.reciprocal(), grid))


def closest_pair(fracs, lattice: Lattice):
    """Shortest interatomic distance over +/-1 neighbouring images: (distance, i, j) or None.

    ``lattice`` should be Minkowski-reduced and ``fracs`` expressed in it, otherwise very
    skewed cells can be under-checked.
    """
    n = len(fracs)
    if n < 2:
        return None
    vecs = lattice.vectors
    shifts = list(product((-1, 0, 1), repeat=3))
    best = (math.inf, -1, -1)
    for i in range(n):
        fi = fracs[i]
        for j in range(i + 1, n):
            fj = fracs[j]
            d = [(fj[k] - fi[k]) for k in range(3)]
            d = [x - round(x) for x in d]
            for s in shifts:
                v = [d[k] + s[k] for k in range(3)]
                x = v[0] * vecs[0][0] + v[1] * vecs[1][0] + v[2] * vecs[2][0]
                y = v[0] * vecs[0][1] + v[1] * vecs[1][1] + v[2] * vecs[2][1]
                z = v[0] * vecs[0][2] + v[1] * vecs[1][2] + v[2] * vecs[2][2]
                dist2 = x * x + y * y + z * z
                if dist2 < best[0]:
                    best = (dist2, i, j)
    return math.sqrt(best[0]), best[1], best[2]
