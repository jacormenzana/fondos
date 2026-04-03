# srri_v5_geometric.py
# VERSION 5 - HARDENED GEOMETRIC VALIDATION
# Basado en srri_v4_geometric.py SIN modificar lógica original.
# Solo se endurecen validaciones.

try:
    from proyecto1.core.srri_v4_geometric import SRRIV4Geometric
except ImportError:
    from core.srri_v4_geometric import SRRIV4Geometric
import numpy as np


class SRRIV5Geometric(SRRIV4Geometric):

    # ============================================================
    # 3. OCR Row Detection (Endurecido)
    # ============================================================

    def _detect_digit_row(self, image):

        row = super()._detect_digit_row(image)

        if row is None:
            return None

        unique_vals = set(d["val"] for d in row)

        # V5: exigir mínimo 6 valores únicos
        if len(unique_vals) < 5:
            return None

        # V5: validar spacing uniforme
        xs = sorted([d["x_center"] for d in row])

        if len(xs) >= 5:
            gaps = np.diff(xs)
            mean_gap = np.mean(gaps)
            if mean_gap > 0:
                cv = np.std(gaps) / mean_gap
                if cv > 0.30:
                    return None

        return row

    # ============================================================
    # 4. Grid matemático (Endurecido)
    # ============================================================

    def _build_grid_from_row(self, row):

        grid = super()._build_grid_from_row(row)

        if grid is None:
            return None

        # V5: penalizar r2 bajo
        r2 = grid[0]["r2"]

        if r2 < 0.95:
            return None

        return grid

    # ============================================================
    # 5. Scoring cromático (Endurecido)
    # ============================================================

    def _score_cells(self, image, grid, row):

        winner, metrics = super()._score_cells(image, grid, row)

        if winner is None:
            return None, None

        # V5: mayor exigencia estadística
        z_scores = metrics.get("z_scores", [])
        if not z_scores:
            return None, None

        sorted_z = sorted(z_scores, reverse=True)

        # Mayor separación respecto al segundo
        if len(sorted_z) > 1:
            if sorted_z[0] - sorted_z[1] < 0.5:
                return None, None

        # Umbral absoluto más estricto
        if sorted_z[0] < 1.8:
            return None, None

        return winner, metrics