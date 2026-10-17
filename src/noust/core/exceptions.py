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


class ConfirmationRequired(ValidationError):
    """
    Raised when a change would cost something the operator has to accept first.

    Nothing has been changed when it is raised. The same request with its
    confirmation (``--yes`` on the command line, ``confirm`` in the API) goes
    ahead; the API carries every warning in its error body, so a client shows
    them all before asking.

    Attributes:
        warnings: What the operator accepts by confirming, one English
            sentence or paragraph each.
    """

    def __init__(self, warnings: list[str], *, field: str = "confirm"):
        """
        Args:
            warnings: What the operator accepts by confirming; at least one.
            field: The request field that confirms it.
        """
        self.warnings = list(warnings)
        message = (
            self.warnings[0]
            if len(self.warnings) == 1
            else f"This change needs your confirmation: {len(self.warnings)} things to know"
        )
        details = "\n\n".join(self.warnings[1:] if len(self.warnings) == 1 else self.warnings)
        hint = "Confirm it to go ahead (--yes, or confirm in the API)."
        super().__init__(message, details=f"{details}\n\n{hint}".strip(), field=field)


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


class DeploysFrozenError(DeploymentError):
    """
    Raised when a deploy is asked for during a freeze.

    Nothing has been changed when it is raised. A person's own deploy goes ahead
    when it says it knows (``--during-freeze``, ``frozen_ok`` in the API); one that
    started by itself (a push, a tag, a schedule) never does.

    Attributes:
        freeze: The freeze that holds it, as the API describes it (``scope``,
            ``reason``, ``starts_at``, ``ends_at``, ``created_by``).
        automatic: The deploy started by itself, so there is nothing to confirm.
    """

    def __init__(
        self,
        message: str,
        details: str = "",
        *,
        freeze: dict[str, object],
        automatic: bool = False,
        field: str | None = "frozen_ok",
    ):
        """
        Args:
            message: Who is frozen and why.
            details: How to go on.
            freeze: The freeze, as a dictionary.
            automatic: The deploy was not a person's.
            field: The request field that confirms it.
        """
        super().__init__(message, details, field=None if automatic else field)
        self.freeze = freeze
        self.automatic = automatic


class DeploysPausedError(DeploymentError):
    """
    Raised when something that deploys by itself is asked to while it is paused.

    Attributes:
        paused_by: Who paused it (``rollback`` when going back did).
    """

    def __init__(self, message: str, details: str = "", *, paused_by: str | None = None):
        """
        Args:
            message: Which application is paused and since when.
            details: How to resume it.
            paused_by: Who paused it.
        """
        super().__init__(message, details)
        self.paused_by = paused_by


class DeploymentCancelled(DeploymentError):
    """
    Raised when an operator cancelled a deployment while it was being built.

    Not a failure of the build: nothing that served before was touched, the
    half-built release is removed, and the history row says "cancelled" rather
    than carrying an error. Code that has to tell a cancellation from a real
    failure asks :func:`noust.core.cancellation.is_cancellation`, which also
    finds it behind an error that wrapped it.

    Attributes:
        cancelled_by: Who asked for it, when it is known.
    """

    def __init__(self, message: str, details: str = "", *, cancelled_by: str | None = None):
        """
        Args:
            message: What was cancelled.
            details: What was left as it was.
            cancelled_by: Who asked for the cancellation.
        """
        super().__init__(message, details)
        self.cancelled_by = cancelled_by


class CancelRefused(NoustError):
    """
    Raised when there is no deployment that can be cancelled just now.

    Attributes:
        reason: ``idle`` (nothing is running), ``unsupported`` (what runs
            cannot be interrupted safely) or ``too_late`` (the release is
            already being activated and finishes, or rolls itself back).
    """

    def __init__(self, message: str, details: str = "", *, reason: str = "idle"):
        """
        Args:
            message: Why nothing was cancelled.
            details: What to do instead.
            reason: One of ``idle``, ``unsupported`` or ``too_late``.
        """
        super().__init__(message, details)
        self.reason = reason


class QueueFullError(NoustError):
    """
    Raised when the deploy queue is full and a new request cannot wait in it.

    Attributes:
        limit: How many requests the queue holds.
    """

    def __init__(self, message: str, details: str = "", *, limit: int = 0):
        """
        Args:
            message: What was refused.
            details: How to go on.
            limit: The queue's size (``deploy.queue_limit``).
        """
        super().__init__(message, details)
        self.limit = limit


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


class CertificateConflictError(CertificateError):
    """A certificate or authority is already there; the API answers 409."""


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


class PrerequisiteError(DependencyError):
    """
    Raised when the machine lacks something an operation needs and the operator can supply it.

    A web server module, a package, a block in a configuration file. Its own class so the
    API can answer 409 instead of the 500 a bug gets: the request was well formed and the
    server is simply not ready for it, and the error says what to do about that.
    """


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


class DomainOverlapError(DomainConflictError):
    """
    Raised when another site of the web server already answers on a name.

    Its own class so the API can say ``domain_overlap``: the console then offers to add the
    name anyway, which is the operator's decision and not an accident.
    """


class RecordConflictError(DomainConflictError):
    """
    Raised when a DNS record with another value is at the name an operation wants.

    The record and what it holds travel with the error (the API carries them in its body), so
    the operator can confirm replacing exactly that one.

    Attributes:
        hostname: The name.
        record_id: The provider's id of the record in the way.
        current: What it holds.
        wanted: What was asked for.
    """

    def __init__(
        self,
        message: str,
        details: str = "",
        *,
        hostname: str,
        record_id: str,
        current: str,
        wanted: str,
    ) -> None:
        """
        Args:
            message: The sentence for the operator.
            details: How to confirm the replacement.
            hostname: The name.
            record_id: The provider's id of the record in the way.
            current: What it holds.
            wanted: What was asked for.
        """
        self.hostname = hostname
        self.record_id = record_id
        self.current = current
        self.wanted = wanted
        super().__init__(message, details)


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


class DatabaseAccessError(DatabaseConnectionError):
    """Raised when an engine refuses to let Noust sign in."""

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


class CloudProviderError(IntegrationError):
    """
    Raised when a cloud provider (Hetzner, DigitalOcean, Vultr) refuses a request or is unreachable.

    Its own message stays verbatim in ``output``: an operator reading "server
    type is not available in this location" knows what to change, and a
    paraphrase would hide it.

    Attributes:
        status: The HTTP status the provider answered; None when it did not.
        retry_after: Seconds the provider asked to wait before trying again,
            when it said (a rate limit).
    """

    def __init__(
        self,
        message: str,
        details: str = "",
        *,
        output: str | None = None,
        status: int | None = None,
        retry_after: int | None = None,
    ):
        """
        Args:
            message: What failed.
            details: How to fix it.
            output: The provider's own words, verbatim.
            status: The provider's HTTP status.
            retry_after: Seconds to wait, when the provider asked.
        """
        super().__init__(message, details, output=output)
        self.status = status
        self.retry_after = retry_after


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
