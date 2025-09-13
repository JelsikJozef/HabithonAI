class AnonymizationError(Exception):
    """Base error for anonymization package."""


class DetectionError(AnonymizationError):
    pass


class PseudonymizationError(AnonymizationError):
    pass


class DeAnonymizationError(AnonymizationError):
    pass


class TokenVaultError(AnonymizationError):
    pass
