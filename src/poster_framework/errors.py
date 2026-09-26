class AgentFrameworkError(Exception):
    pass


class AgentRegistrationError(AgentFrameworkError, ValueError):
    pass


class AgentSetConfigurationError(AgentFrameworkError, ValueError):
    pass


class AgentIdentityMismatchError(AgentFrameworkError, ValueError):
    pass


class InterfaceVersionCompatibilityError(AgentFrameworkError, ValueError):
    pass


class ContentFormatCompatibilityError(AgentFrameworkError, ValueError):
    pass


class ToolRegistrationError(AgentFrameworkError, ValueError):
    pass


class ToolInvocationError(AgentFrameworkError, ValueError):
    pass


class BudgetExceededError(AgentFrameworkError, RuntimeError):
    pass


class FileSecurityError(AgentFrameworkError, ValueError):
    pass
