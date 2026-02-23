"""Docker container lifecycle management for isolated user sessions.

Translates the agent-isolation run.sh logic into Python. Manages creating,
starting, and executing commands in per-user Docker containers with gVisor.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class ContainerError(Exception):
    """Raised when a Docker container operation fails."""

    pass


def load_env_file(path: Path) -> dict[str, str]:
    """Load KEY=VALUE pairs from an env file.

    Ignores comments (lines starting with #) and blank lines.

    Args:
        path: Path to the .env file.

    Returns:
        Dictionary of environment variable names to values.
    """
    env_vars: dict[str, str] = {}
    if not path.exists():
        return env_vars

    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    except OSError as e:
        logger.warning(f"Failed to read env file {path}: {e}")

    return env_vars


class ContainerManager:
    """Manages Docker container lifecycle for isolated user sessions.

    Each user gets a persistent container (sandbox-{user_id}) that runs
    sleep infinity. Sessions are started via docker exec into that container.
    """

    def __init__(
        self,
        image: str,
        runtime: str,
        memory: str,
        cpus: str,
        users_dir: Path,
        env_vars: dict[str, str] | None = None,
    ):
        self._image = image
        self._runtime = runtime
        self._memory = memory
        self._cpus = cpus
        self._users_dir = users_dir
        self._env_vars = env_vars or {}

    def get_container_name(self, user_id: str) -> str:
        """Get the Docker container name for a user.

        Args:
            user_id: User identifier.

        Returns:
            Container name in format sandbox-{user_id}.
        """
        return f"sandbox-{user_id}"

    def get_user_dir(self, user_id: str) -> Path:
        """Get the host path for a user's home directory.

        Args:
            user_id: User identifier.

        Returns:
            Path to {users_dir}/{user_id}/
        """
        return self._users_dir / user_id

    def build_create_command(self, user_id: str) -> list[str]:
        """Build docker create command for a new user container.

        Creates a long-running container with:
        - gVisor runtime
        - Memory/CPU limits
        - Bind-mount of user directory as /root
        - Environment variables for API auth
        - IS_SANDBOX=1 flag

        Args:
            user_id: User identifier.

        Returns:
            List of command arguments for docker create.
        """
        container_name = self.get_container_name(user_id)
        user_dir = self.get_user_dir(user_id)

        cmd = [
            "docker", "create",
            "--name", container_name,
            f"--runtime={self._runtime}",
            f"--memory={self._memory}",
            f"--cpus={self._cpus}",
        ]

        # Environment variables
        for key, value in self._env_vars.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend(["-e", "IS_SANDBOX=1"])

        # Bind-mount user directory as /root
        cmd.extend(["-v", f"{user_dir}:/root"])

        # Image and entrypoint
        cmd.extend([self._image, "sleep infinity"])

        return cmd

    def build_exec_command(
        self,
        user_id: str,
        claude_args: list[str],
        interactive: bool = False,
    ) -> list[str]:
        """Build docker exec command for a user's container.

        Always includes --dangerously-skip-permissions since gVisor is the
        security boundary, not Claude's permission system.

        Args:
            user_id: User identifier.
            claude_args: Arguments to pass to the claude CLI.
            interactive: Whether to pass -i flag for stdin.

        Returns:
            List of command arguments for docker exec.
        """
        container_name = self.get_container_name(user_id)

        cmd = ["docker", "exec"]
        if interactive:
            cmd.append("-i")
        cmd.extend([
            container_name,
            "claude", "--dangerously-skip-permissions",
            *claude_args,
        ])
        return cmd

    def build_start_command(self, user_id: str) -> list[str]:
        """Build docker start command.

        Args:
            user_id: User identifier.

        Returns:
            List of command arguments for docker start.
        """
        return ["docker", "start", self.get_container_name(user_id)]

    def build_inspect_command(self, user_id: str) -> list[str]:
        """Build docker inspect command to check container state.

        Args:
            user_id: User identifier.

        Returns:
            List of command arguments for docker inspect.
        """
        return [
            "docker", "inspect",
            "-f", "{{.State.Running}}",
            self.get_container_name(user_id),
        ]

    async def ensure_container(self, user_id: str) -> None:
        """Ensure a user's container exists and is running.

        Inspects the container state and:
        - If not found: creates and starts it.
        - If stopped: starts it.
        - If running: no-op.

        Args:
            user_id: User identifier.

        Raises:
            ContainerError: If creation or start fails.
        """
        container_name = self.get_container_name(user_id)

        # Check current state
        inspect_cmd = self.build_inspect_command(user_id)
        proc = await asyncio.create_subprocess_exec(
            *inspect_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            # Container doesn't exist — create and start
            logger.info(f"Container {container_name} not found, creating")
            await self._create_container(user_id)
            await self._start_container(user_id)
            return

        # Container exists — check if running
        state = stdout.decode().strip().lower()
        if state == "true":
            logger.debug(f"Container {container_name} already running")
            return

        # Container exists but stopped
        logger.info(f"Container {container_name} stopped, starting")
        await self._start_container(user_id)

    async def _create_container(self, user_id: str) -> None:
        """Create a Docker container for the user.

        Raises:
            ContainerError: If docker create fails.
        """
        cmd = self.build_create_command(user_id)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to create container for {user_id}: {error_msg}")
            raise ContainerError(
                f"Failed to create container sandbox-{user_id}: {error_msg}"
            )

        logger.info(f"Created container sandbox-{user_id}")

    async def _start_container(self, user_id: str) -> None:
        """Start a stopped Docker container.

        Raises:
            ContainerError: If docker start fails.
        """
        cmd = self.build_start_command(user_id)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error(f"Failed to start container for {user_id}: {error_msg}")
            raise ContainerError(
                f"Failed to start container sandbox-{user_id}: {error_msg}"
            )

        logger.info(f"Started container sandbox-{user_id}")

    @staticmethod
    def is_docker_available() -> bool:
        """Check if Docker is available on the host.

        Returns:
            True if docker command is found in PATH.
        """
        return shutil.which("docker") is not None
