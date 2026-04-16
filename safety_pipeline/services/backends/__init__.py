"""Service backend implementations."""

from .discourse import DiscourseBackend
from .erpnext import ERPNextBackend
from .mailu import MailuBackend
from .openemr import OpenEMRBackend
from .zammad import ZammadBackend

__all__ = [
    "DiscourseBackend",
    "ERPNextBackend",
    "MailuBackend",
    "OpenEMRBackend",
    "ZammadBackend",
]
