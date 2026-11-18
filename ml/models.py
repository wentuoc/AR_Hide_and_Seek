"""A collection of 1D-CNN models used for hyperparameter optimisation"""

import numpy as np
import torch
import torch.nn as nn
from abc import ABC, abstractmethod

class BaseModel(nn.Module, ABC):
    """Abstract base class for convolutional classifiers."""

    def __init__(self, num_input_features, num_classes):
        super().__init__()
        self.num_input_features = num_input_features
        self.num_classes = num_classes

    def forward(self, inputs: np.ndarray):
        if inputs.ndim != 3 or inputs.shape[1:] != (20, self.num_input_features):
            raise ValueError(f"inputs must have shape [batch, window, {self.num_input_features}]")
        return self._forward_impl(inputs)

    @abstractmethod
    def _forward_impl(self, inputs):
        """Perform model-specific computation."""
        raise NotImplementedError

    def predict_proba(self, inputs):
        """Return softmax probabilities without changing the training output."""
        return torch.softmax(self(inputs), dim=1)
        
        
class GestureCNN(BaseModel):
    """1D CNN for 20-sample windows with 11 sensor features per sample."""

    def __init__(self, num_input_features=11, num_classes=5, dropout=0.3):
        super().__init__(num_input_features, num_classes)

        self.features = nn.Sequential(
            nn.Conv1d(num_input_features, 32, kernel_size=5, padding=2),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(64, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),
        )
        self.global_average_pool = nn.AdaptiveAvgPool1d(1)
        self.global_max_pool = nn.AdaptiveMaxPool1d(1)
        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, num_classes),
        )

    def _forward_impl(self, inputs):
        # Conv1d expects [batch, channels, sequence_length].
        features = self.features(inputs.transpose(1, 2))
        average_features = self.global_average_pool(features).flatten(1)
        maximum_features = self.global_max_pool(features).flatten(1)
        pooled_features = torch.cat((average_features, maximum_features), dim=1)
        return self.classifier(pooled_features)


class GestureCNN_Prelim(BaseModel):
    """Compact CNN baseline for short gestures within 20-sample windows."""

    def __init__(self, num_input_features=11, num_classes=7, dropout=0.3):
        super().__init__(num_input_features, num_classes)

        self.features = nn.Sequential(
            nn.Conv1d(num_input_features, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm1d(64),
            nn.ReLU(),
        )
        self.global_average_pool = nn.AdaptiveAvgPool1d(1)
        self.global_max_pool = nn.AdaptiveMaxPool1d(1)
        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, num_classes),
        )

    def _forward_impl(self, inputs):
        # Conv1d expects [batch, channels, sequence_length].
        features = self.features(inputs.transpose(1, 2))
        average_features = self.global_average_pool(features).flatten(1)
        maximum_features = self.global_max_pool(features).flatten(1)
        pooled_features = torch.cat((average_features, maximum_features), dim=1)
        return self.classifier(pooled_features)


class GestureCNN_Small(BaseModel):
    """Small low-latency 1D CNN for gesture classification."""

    def __init__(self, num_input_features=11, num_classes=5, dropout=0.3):
        super().__init__(num_input_features, num_classes)

        self.features = nn.Sequential(
            nn.Conv1d(num_input_features, 16, kernel_size=3, padding=1),
            nn.BatchNorm1d(16),
            nn.ReLU(),
            nn.MaxPool1d(kernel_size=2),
            nn.Conv1d(16, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
        )
        self.global_average_pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(32, num_classes),
        )

    def _forward_impl(self, inputs):
        # Conv1d expects [batch, channels, sequence_length].
        features = self.features(inputs.transpose(1, 2))
        average_features = self.global_average_pool(features).flatten(1)
        return self.classifier(average_features)


class _ResidualBlock(nn.Module):
    def __init__(self, input_channels, output_channels, kernel_size, dilation=1):
        super().__init__()
        padding = dilation * (kernel_size - 1) // 2
        self.features = nn.Sequential(
            nn.Conv1d(
                input_channels,
                output_channels,
                kernel_size,
                padding=padding,
                dilation=dilation,
            ),
            nn.BatchNorm1d(output_channels),
            nn.ReLU(),
            nn.Conv1d(
                output_channels,
                output_channels,
                kernel_size=3,
                padding=1,
            ),
            nn.BatchNorm1d(output_channels),
        )
        self.skip = (
            nn.Identity()
            if input_channels == output_channels
            else nn.Conv1d(input_channels, output_channels, kernel_size=1)
        )

    def forward(self, inputs):
        return torch.relu(self.features(inputs) + self.skip(inputs))


class GestureCNN_Large(BaseModel):
    """Larger residual multi-scale 1D CNN for gesture classification."""

    def __init__(self, num_input_features=11, num_classes=5, dropout=0.3):
        super().__init__(num_input_features, num_classes)

        self.stem = nn.Sequential(
            nn.Conv1d(num_input_features, 32, kernel_size=3, padding=1),
            nn.BatchNorm1d(32),
            nn.ReLU(),
        )
        self.features = nn.Sequential(
            _ResidualBlock(32, 64, kernel_size=3),
            _ResidualBlock(64, 64, kernel_size=5),
            _ResidualBlock(64, 96, kernel_size=3, dilation=2),
        )
        self.global_average_pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(96, 64),
            nn.ReLU(),
            nn.Linear(64, num_classes),
        )

    def _forward_impl(self, inputs):
        # Conv1d expects [batch, channels, sequence_length].
        features = self.stem(inputs.transpose(1, 2))
        features = self.features(features)
        features = self.global_average_pool(features).flatten(1)
        return self.classifier(features)
