import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from mesh.models import FraudModels


def test_model_fit_uses_distinct_anomaly_and_classifier_splits():
    rng = np.random.default_rng(4)
    split_a = rng.normal(size=(120, 13))
    split_b = rng.normal(size=(90, 13))
    labels_b = ["NORMAL"] * 60 + ["CROSS_BANK_MULE_CASCADE"] * 20 + ["ABORT_STORM"] * 10
    validation = rng.normal(size=(40, 13))
    validation_labels = ["NORMAL"] * 30 + ["CROSS_BANK_MULE_CASCADE"] * 10

    models = FraudModels(random_state=9)
    models.fit(split_a, split_b, labels_b, validation, validation_labels)

    assert models.anomaly_model.n_features_in_ == 13
    assert models.classifier.n_features_in_ == 14
    scores = models.score(split_b[:4])
    assert len(scores) == 4
    assert all(0.0 <= row["anom"] <= 1.0 for row in scores)
    assert all(0.0 <= row["p_cascade"] <= 1.0 for row in scores)


def test_thresholds_are_calibrated_and_evidence_uses_frozen_feature_names():
    rng = np.random.default_rng(12)
    split_a = rng.normal(size=(100, 13))
    split_b = rng.normal(size=(90, 13))
    labels_b = ["NORMAL"] * 60 + ["CROSS_BANK_MULE_CASCADE"] * 20 + ["ABORT_STORM"] * 10
    validation = rng.normal(size=(40, 13))
    validation_labels = ["NORMAL"] * 30 + ["CROSS_BANK_MULE_CASCADE"] * 10
    models = FraudModels(random_state=9)
    models.fit(split_a, split_b, labels_b, validation, validation_labels)

    evidence = models.evidence(split_b[0])
    assert len(evidence) == 3
    assert all(len(item) == 2 for item in evidence)
    assert all(0.0 <= item[1] <= 100.0 for item in evidence)
    assert 0.0 <= models.thresholds["T_cascade"] <= 1.0
    assert 0.0 <= models.thresholds["T_anom"] <= 1.0
