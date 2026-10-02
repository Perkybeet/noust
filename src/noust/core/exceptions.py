"""
Custom exceptions for Noust.

This module defines a hierarchy of exceptions used throughout the application
to provide clear and actionable error messages.
"""


class NoustError(Exception):
    """
    Base exception for all Noust errors.

    All custom exceptions should inherit from this class.
    """

    def __init__(
        self,
        message: str,
        details: str = "",
        *,
        output: str | None = None,
        field: str | None = None,
    ):
        """
        Args:
            message: The bare, human-readable sentence describing the failure.
            details: How to fix it, or further context - shown as a hint.
            output: The failing tool's own output, verbatim, when the caller
                wants it carried as a field of its own rather than folded into
                ``details``. None for an error that has no external tool
                output to show, which is most of them.
            field: The request field the error is about, when there is one, so
                the API can put it next to that input (``fields`` in its error
                body) instead of a client guessing from the message.
        """
        self.message = message
        self.details = details
        self.output = output
        self.field = field
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}\n  Details: {self.details}"
        return self.message


class ConfigError(NoustError):
    """Raised when there's a configuration error."""

    pass


class ValidationError(NoustError):
    """Raised when input validation fails."""

    pass


class DeploymentError(NoustError):
    """Raised when deployment fails at any step."""

    pass


class RolledBackError(DeploymentError):
    """
    Raised when a new version failed and what served before was put back.

    A deployment that ends this way failed, and says so, but the application
    is not down: the previous release, commit or containers answer again.
    The deployment record and its notification tell the two apart through
    this class, so no caller has to read the message to know.
    """

    pass


class SchemaChangedError(DeploymentError):
    """
    Raised when going back would pass deployments that changed the database's schema.

    Noust puts code back, never a database: going back past such a deployment
    is the operator's decision, asked for explicitly
    (:func:`noust.deployers.lifecycle.require_schema_change_confirmed`).

    Attributes:
        deployments: The deployments that changed the schema, oldest first.
    """

    def __init__(
        self,
        message: str,
        details: str = "",
        *,
        deployments: list[int],
        field: str | None = "schema_changed_ok",
    ):
        """
        Args:
            message: What would be gone back past.
            details: How to go on.
            deployments: The deployments that changed the schema.
            field: The request field that confirms it.
        """
        super().__init__(message, details, field=field)
        self.deployments = deployments


class BuildError(DeploymentError):
    """Raised when application build fails."""

    pass


class OutOfMemoryError(BuildError):
    """
    Raised when build fails due to Out of Memory (OOM) condition.

    This is detected when the process exits with code 137 (128 + SIGKILL)
    which typically indicates the OOM killer terminated the process.
    """

    def __init__(self, message: str = "Build killed due to insufficient memory", details: str = ""):
        suggestions = """
The build process was killed by the system (exit code 137), typically caused by
insufficient RAM. Next.js/Turbopack builds can require 2-4GB+ of memory.

Solutions to try:

1. Add swap space (if not already configured):
   sudo fallocate -l 4G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

2. Limit Node.js memory usage:
   Add to .env file: NODE_OPTIONS="--max-old-space-size=1536"
   Then redeploy: noust update <domain>

3. Build locally and deploy pre-built:
   - Build on your local machine: npm run build
   - Commit the .next folder (remove from .gitignore)
   - Push changes and update: noust update <domain>

4. Use a server with more RAM (recommended: 2GB+ for Next.js apps)

5. Disable Turbopack (if using Next.js 15+):
   In next.config.js, ensure you're not using experimental turbo features
   for production builds."""

        if details:
            full_details = f"{details}\n{suggestions}"
        else:
            full_details = suggestions

        super().__init__(message, full_details)


class SourceError(NoustError):
    """Raised when source fetching fails (git clone, download, etc.)."""

    pass


class ServiceError(NoustError):
    """Raised when systemd service operations fail."""

    pass


class SiteError(NoustError):
    """Raised when site configuration fails."""

    pass


class NginxError(SiteError):
    """Raised when Nginx operations fail."""

    pass


class ApacheError(SiteError):
    """Raised when Apache operations fail."""

    pass


class CertificateError(NoustError):
    """Raised when SSL certificate operations fail."""

    pass


class CommandError(NoustError):
    """Raised when a shell command execution fails."""

    def __init__(self, message: str, command: str = "", exit_code: int = 0, stderr: str = ""):
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        details = ""
        if command:
            details += f"Command: {command}\n"
        if exit_code:
            details += f"Exit code: {exit_code}\n"
        if stderr:
            details += f"Error output: {stderr}"
        super().__init__(message, details.strip())


class DependencyError(NoustError):
    """Raised when a required dependency is missing."""

    pass


class PermissionError(NoustError):
    """Raised when there are insufficient permissions."""

    pass


class PortError(NoustError):
    """Raised when there are port-related issues."""

    pass


class DomainError(NoustError):
    """Raised when there are domain-related issues."""

    pass


class DomainConflictError(DomainError):
    """
    Raised when a domain already belongs to an application.

    Its own class so the API can answer 409 instead of the 400 a malformed
    domain gets: the request was well formed, the name is simply taken.
    """


class TemplateError(NoustError):
    """Raised when template rendering fails."""

    pass


class RollbackError(NoustError):
    """Raised when rollback operation fails."""

    pass


class MonitorError(NoustError):
    """Raised when process monitoring operations fail."""

    pass


class AIAnalysisError(NoustError):
    """Raised when AI analysis fails."""

    pass


class EmailError(NoustError):
    """Raised when email notification fails."""

    pass


class SSHError(NoustError):
    """
    Raised when SSH authentication or configuration fails.

    Provides detailed guidance for resolving SSH issues.
    """

    pass


class SetupError(NoustError):
    """
    Raised when required setup/configuration is missing.

    Used when prerequisites are not met (e.g., missing SSH keys,
    missing dependencies, etc.)
    """

    pass


class DatabaseError(NoustError):
    """Base exception for database operations."""

    pass


class DatabaseConnectionError(DatabaseError):
    """Raised when database connection fails."""

    pass


class DatabaseNotFoundError(DatabaseError):
    """Raised when a database does not exist."""

    pass


class DatabaseExistsError(DatabaseError):
    """Raised when trying to create a database that already exists."""

    pass


class DatabaseUserError(DatabaseError):
    """Raised when database user operations fail."""

    pass


class DatabaseEngineError(DatabaseError):
    """Raised when database engine operations fail (install, start, stop)."""

    pass


class DatabaseBackupError(DatabaseError):
    """Raised when database backup/restore operations fail."""

    pass


class DatabaseQueryError(DatabaseError):
    """Raised when a database query fails."""

    pass


class SecurityError(NoustError):
    """
    Raised when a security-sensitive operation is attempted with untrusted input.

    Used to prevent command injection and other security vulnerabilities.
    """

    pass


class BackupError(NoustError):
    """Raised when backup operations fail."""

    pass


class DockerError(NoustError):
    """Raised when Docker operations fail."""

    pass


class EnvConfigError(NoustError):
    """Raised when environment configuration fails."""

    pass


class IntegrationError(NoustError):
    """Raised when a code host (GitHub) refuses a request or cannot be reached."""

    pass


class NodeError(NoustError):
    """
    Raised when a fleet operation on a node fails: unknown node, bad join code,
    a refused registration.

    The base of every fleet error, so a caller that only needs "the node
    operation failed" catches one class.
    """

    pass


class FleetUnavailableError(NodeError):
    """
    Raised when this installation lacks what the fleet needs: the httpx library.

    httpx is part of the web stack, not of the core: a server that never talks
    to a node must still start its console without it, so it is imported when
    a node is first contacted and its absence is said in a sentence, with the
    package that fixes it.
    """

    pass


class NodeUnreachableError(NodeError):
    """
    Raised when a node cannot be reached: the SSH tunnel did not open, or died.

    The message says why in a sentence (host key changed, connection timed
    out, nothing answers); ``details`` carries ssh's own stderr verbatim. A
    revoked SSH key is :class:`NodeRefusedError` instead: the node did answer,
    and said no.
    """

    pass


class NodeRefusedError(NodeError):
    """
    Raised when the node refused this central's credential outright.

    Either layer: the fleet token answered 401 or 403 over HTTP, or ssh said
    "Permission denied (publickey)" opening the tunnel - both mean the same
    thing happened on the node (``noust fleet deauthorize``, or the token
    revoked on its own), and both are persisted the same way
    (``noust.fleet.client.NodeClient.mark_refused``,
    ``noust.fleet.tunnels.TunnelManager`` on a revoked key) so nothing keeps
    presenting a credential the node has already said no to.

    Attributes:
        status_code: The HTTP status the node answered, 401 or 403 - only
            set when the refusal came over HTTP; None for a revoked SSH key.
    """

    status_code: int | None = None
