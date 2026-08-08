"""One-shot elevation broker contracts.

The package intentionally contains a narrow operation boundary.  It is not a
general purpose elevated shell and it has no network or AI integration.
"""

from .broker import (
    BrokerEntryPoint,
    BrokerValidationError,
    ElevatedOperationDispatcher,
    FakeElevatedExecutor,
    OneShotClaimStore,
)
from .envelope import (
    EnvelopeFile,
    EnvelopeFileError,
    ElevationEnvelopeFactory,
    canonical_json_bytes,
    default_elevation_directory,
    read_envelope_file,
    write_envelope_file,
)
from .launcher import ElevationLauncher, FakeElevationLauncher, WindowsElevationLauncher
from .client import ElevationBrokerClient
from .models import (
    ElevationEnvelope,
    ElevationClientOutcome,
    ElevationLaunchOutcome,
    ElevationReason,
    ElevationRequest,
    ElevationResult,
    ElevationStatus,
)
from .validation import (
    BrokerFileIdentity,
    EnvelopeValidator,
    validate_broker_identity,
)


def __getattr__(name):
    if name in {"ElevationExecutionThread", "ElevationQtController"}:
        import importlib
        module = importlib.import_module(__name__ + ".qt")
        return getattr(module, name)
    raise AttributeError(name)

__all__ = [
    "BrokerEntryPoint", "BrokerFileIdentity", "BrokerValidationError", "ElevationBrokerClient",
    "ElevatedOperationDispatcher", "ElevationEnvelope", "ElevationEnvelopeFactory",
    "ElevationClientOutcome", "ElevationLaunchOutcome", "ElevationLauncher", "ElevationReason",
    "ElevationRequest", "ElevationResult", "ElevationStatus", "ElevationExecutionThread", "ElevationQtController", "EnvelopeFile",
    "EnvelopeFileError", "EnvelopeValidator", "FakeElevatedExecutor",
    "FakeElevationLauncher", "OneShotClaimStore", "WindowsElevationLauncher",
    "canonical_json_bytes", "default_elevation_directory", "read_envelope_file", "validate_broker_identity",
    "write_envelope_file",
]
