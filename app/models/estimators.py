import numpy as np
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.neural_network import MLPRegressor


class NaNAwareMLPRegressor(BaseEstimator, RegressorMixin):
    """
    MLPRegressor that treats NaN as a first-class signal rather than imputing.

    For every input feature x_i, a binary indicator (1 if x_i was NaN, 0 otherwise)
    is appended to the feature vector and x_i itself is zero-filled.  The network
    receives 2*n_features inputs and can learn rules of the form
    "when x_i is missing AND x_j is high, do Z" — i.e., missingness-conditioned
    interaction effects — without any statistical imputation.

    Architecture: three hidden layers (128 → 64 → 32) give sufficient depth to
    compose non-linear cross-feature interactions while staying lightweight.
    """

    def __init__(
        self,
        hidden_layer_sizes=(128, 64, 32),
        activation='relu',
        alpha=1e-3,
        max_iter=1000,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=42,
    ):
        self.hidden_layer_sizes = hidden_layer_sizes
        self.activation = activation
        self.alpha = alpha
        self.max_iter = max_iter
        self.early_stopping = early_stopping
        self.validation_fraction = validation_fraction
        self.random_state = random_state

    def _augment(self, X):
        if hasattr(X, 'values'):
            X = X.values.astype(float)
        else:
            X = np.asarray(X, dtype=float)
        nan_mask = np.isnan(X).astype(np.float32)
        X_filled = np.where(np.isnan(X), 0.0, X)
        return np.hstack([X_filled, nan_mask])

    def fit(self, X, y):
        self._mlp = MLPRegressor(
            hidden_layer_sizes=self.hidden_layer_sizes,
            activation=self.activation,
            alpha=self.alpha,
            max_iter=self.max_iter,
            early_stopping=self.early_stopping,
            validation_fraction=self.validation_fraction,
            random_state=self.random_state,
        )
        self._mlp.fit(self._augment(X), y)
        return self

    def predict(self, X):
        return self._mlp.predict(self._augment(X))
