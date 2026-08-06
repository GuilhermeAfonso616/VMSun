from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class KalmanPrediction:
    point: tuple[float, float]
    velocity: tuple[float, float]


class ConstantVelocityKalman:
    """Filtro Kalman simples para footpoint 2D.

    Mantemos o modelo enxuto para reduzir dependencias e ainda obter predicao
    suficiente para gating espacial e recuperacao de trilhas.
    """

    def __init__(self):
        self._x = np.zeros((4, 1), dtype=float)
        self._p = np.eye(4, dtype=float) * 1000.0
        self._f = np.array(
            [[1.0, 0.0, 1.0, 0.0],
             [0.0, 1.0, 0.0, 1.0],
             [0.0, 0.0, 1.0, 0.0],
             [0.0, 0.0, 0.0, 1.0]],
            dtype=float,
        )
        self._h = np.array(
            [[1.0, 0.0, 0.0, 0.0],
             [0.0, 1.0, 0.0, 0.0]],
            dtype=float,
        )
        self._q = np.eye(4, dtype=float) * 0.01
        self._r = np.eye(2, dtype=float) * 8.0

    def initialize(self, x: float, y: float) -> None:
        self._x[:] = [[x], [y], [0.0], [0.0]]
        self._p = np.eye(4, dtype=float)

    def predict(self) -> KalmanPrediction:
        self._x = self._f @ self._x
        self._p = self._f @ self._p @ self._f.T + self._q
        return KalmanPrediction(point=(float(self._x[0, 0]), float(self._x[1, 0])), velocity=(float(self._x[2, 0]), float(self._x[3, 0])))

    def peek_prediction(self) -> KalmanPrediction:
        x = self._f @ self._x
        return KalmanPrediction(point=(float(x[0, 0]), float(x[1, 0])), velocity=(float(x[2, 0]), float(x[3, 0])))

    def update(self, x: float, y: float) -> KalmanPrediction:
        z = np.array([[x], [y]], dtype=float)
        y_residual = z - (self._h @ self._x)
        s = self._h @ self._p @ self._h.T + self._r
        k = self._p @ self._h.T @ np.linalg.inv(s)
        self._x = self._x + (k @ y_residual)
        identity = np.eye(self._p.shape[0], dtype=float)
        self._p = (identity - (k @ self._h)) @ self._p
        return KalmanPrediction(point=(float(self._x[0, 0]), float(self._x[1, 0])), velocity=(float(self._x[2, 0]), float(self._x[3, 0])))
