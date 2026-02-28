from enum import Enum

class CoreDomain(str, Enum):
    AGRICULTURE = "Agriculture"
    HEALTHCARE = "Healthcare"
    INFRASTRUCTURE = "Infrastructure"
    ENERGY = "Energy"
    DEFENSE = "Defense"
    TECHNOLOGY = "Technology"
    TRANSPORT = "Transport"
    OTHER = "Other"

class ProcurementType(str, Enum):
    WORKS = "Works"
    SUPPLY = "Supply"
    SERVICES = "Services"
    UNKNOWN = "Unknown"
