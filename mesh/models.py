"""Two-stage fraud scoring with separated training splits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest

from contracts.schema import MESH_FEATURES, SENSOR_FEATURES


FEATURE_COUNT = len(SENSOR_FEATURES) + len(MESH_FEATURES)
CLASSIFIER_LABELS = frozenset({"NORMAL", "CROSS_BANK_MULE_CASCADE", "ABORT_STORM"})


class FraudModels:
    """Anomaly model trained on split A and classifier trained on split B.

    Arrays passed to ``fit`` are grouped by chain before the call. The anomaly
    model only sees normal split-A rows; the classifier only sees split-B rows.
    Validation rows calibrate thresholds and never fit either model.
    """

    def __init__(self, random_state: int = 7) -> None:
        self.random_state = random_state
        self.anomaly_model: IsolationForest | None = None
        self.classifier: HistGradientBoostingClassifier | None = None
        self._normal_reference = np.asarray([], dtype=float)
        self._feature_reference = np.empty((0, FEATURE_COUNT), dtype=float)
        self.thresholds: dict[str, float] = {}

    def fit(
        self,
        split_a_normal: Sequence[Sequence[float]],
        split_b_features: Sequence[Sequence[float]],
        split_b_labels: Sequence[str],
        validation_features: Sequence[Sequence[float]],
        validation_labels: Sequence[str],
    ) -> "FraudModels":
        x_a = self._matrix(split_a_normal, "split_a_normal")
        x_b = self._matrix(split_b_features, "split_b_features")
        x_valid = self._matrix(validation_features, "validation_features")
        y_b = np.asarray(split_b_labels, dtype=str)
        y_valid = np.asarray(validation_labels, dtype=str)
        if len(x_b) != len(y_b) or len(x_valid) != len(y_valid):
            raise ValueError("feature and label counts must match")
        if not set(y_b).issubset(CLASSIFIER_LABELS):
            raise ValueError("split B labels must be NORMAL, CROSS_BANK_MULE_CASCADE, or ABORT_STORM")
        if not set(y_valid).issubset(CLASSIFIER_LABELS):
            raise ValueError("validation labels must use the classifier classes")
        if "CROSS_BANK_MULE_CASCADE" not in y_b:
            raise ValueError("split B must contain cascade examples")
        if "NORMAL" not in y_b or "ABORT_STORM" not in y_b:
            raise ValueError("split B must contain NORMAL and ABORT_STORM examples")
        normal_validation = y_valid == "NORMAL"
        if not normal_validation.any():
            raise ValueError("validation split must contain normal events")

        self.anomaly_model = IsolationForest(
            n_estimators=100,
            contamination="auto",
            random_state=self.random_state,
            n_jobs=1,
        )
        self.anomaly_model.fit(x_a)
        raw_a = -self.anomaly_model.decision_function(x_a)
        self._normal_reference = np.sort(raw_a)
        self._feature_reference = np.sort(np.asarray(x_a, dtype=float), axis=0)

        anom_b = self._anomaly_scores(x_b)
        anom_valid = self._anomaly_scores(x_valid)
        classifier_x = np.column_stack((x_b, anom_b))
        self.classifier = HistGradientBoostingClassifier(
            max_iter=100,
            learning_rate=0.08,
            max_leaf_nodes=15,
            l2_regularization=1.0,
            random_state=self.random_state,
        )
        self.classifier.fit(classifier_x, y_b)

        valid_cascade = self._probabilities(x_valid, anom_valid)["p_cascade"]
        normal_cascade = valid_cascade[normal_validation]
        valid_anom = anom_valid[normal_validation]
        self.thresholds = {
            "T_cascade": self._one_percent_fpr_threshold(normal_cascade),
            "T_anom": self._one_percent_fpr_threshold(valid_anom),
        }
        return self

    @staticmethod
    def _matrix(values: Sequence[Sequence[float]], name: str) -> np.ndarray:
        matrix = np.asarray(values, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != FEATURE_COUNT:
            raise ValueError(f"{name} must be a two-dimensional array with {FEATURE_COUNT} columns")
        if len(matrix) == 0 or not np.isfinite(matrix).all():
            raise ValueError(f"{name} must contain finite feature rows")
        return matrix

    @staticmethod
    def _one_percent_fpr_threshold(normal_scores: np.ndarray) -> float:
        # 'higher' keeps the empirical false-positive rate at or below the
        # selected tail when only a small validation set is available.
        try:
            return float(np.quantile(normal_scores, 0.99, method="higher"))
        except TypeError:  # NumPy versions before the `method` keyword.
            return float(np.quantile(normal_scores, 0.99, interpolation="higher"))

    def _require_models(self) -> tuple[IsolationForest, HistGradientBoostingClassifier]:
        if self.anomaly_model is None or self.classifier is None:
            raise RuntimeError("models have not been fitted")
        return self.anomaly_model, self.classifier

    def _anomaly_scores(self, features: np.ndarray) -> np.ndarray:
        anomaly_model = self.anomaly_model
        if anomaly_model is None:
            raise RuntimeError("anomaly model has not been fitted")
        raw = -anomaly_model.decision_function(features)
        if len(self._normal_reference) == 0:
            raise RuntimeError("normal reference distribution is unavailable")
        ranks = np.searchsorted(self._normal_reference, raw, side="right")
        return ranks.astype(float) / float(len(self._normal_reference))

    def _probabilities(self, features: np.ndarray, anomaly: np.ndarray) -> dict[str, np.ndarray]:
        _, classifier = self._require_models()
        matrix = np.column_stack((features, anomaly))
        probabilities = classifier.predict_proba(matrix)
        cascade_column = list(classifier.classes_).index("CROSS_BANK_MULE_CASCADE")
        return {"p_cascade": probabilities[:, cascade_column]}

    def score(self, features: Sequence[Sequence[float]]) -> list[dict[str, float]]:
        matrix = self._matrix(features, "features")
        anomaly = self._anomaly_scores(matrix)
        p_cascade = self._probabilities(matrix, anomaly)["p_cascade"]
        return [
            {"anom": float(anom), "p_cascade": float(prob)}
            for anom, prob in zip(anomaly, p_cascade)
        ]

    def evidence(self, feature_row: Sequence[float], limit: int = 3) -> list[list[object]]:
        row = np.asarray(feature_row, dtype=float)
        if row.shape != (FEATURE_COUNT,) or not np.isfinite(row).all():
            raise ValueError(f"feature_row must contain {FEATURE_COUNT} finite values")
        if len(self._feature_reference) == 0:
            raise RuntimeError("normal feature distribution is unavailable")
        names = SENSOR_FEATURES + MESH_FEATURES
        percentiles = [
            100.0 * float(np.searchsorted(self._feature_reference[:, index], row[index], side="right"))
            / len(self._feature_reference)
            for index in range(FEATURE_COUNT)
        ]
        ranked = sorted(
            range(FEATURE_COUNT),
            key=lambda index: abs(percentiles[index] - 50.0),
            reverse=True,
        )
        return [[names[index], float(percentiles[index])] for index in ranked[:limit]]

    def save_thresholds(self, path: str | Path) -> None:
        if set(self.thresholds) != {"T_cascade", "T_anom"}:
            raise RuntimeError("thresholds are unavailable before fit")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.thresholds, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load_thresholds(cls, path: str | Path) -> dict[str, float]:
        values = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(values) != {"T_cascade", "T_anom"}:
            raise ValueError("threshold file must contain exactly T_cascade and T_anom")
        for name, value in values.items():
            if not isinstance(value, (int, float)) or not np.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        return {name: float(value) for name, value in values.items()}
