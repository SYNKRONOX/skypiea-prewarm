"""shell (light): simulated shell. Returns canned, deterministic output; executes nothing."""

import hashlib

from tools.common.data import repo_files
from tools.common.server import burn, create_server, instrument, run

NAME = "shell"
FILES = repo_files()

mcp = create_server(NAME, "Run a command in the sample repo (simulated).")


def _output(command: str) -> tuple[int, str]:
    cmd = command.strip()
    if cmd.startswith(("ls", "find")):
        return 0, "\n".join(FILES)
    if cmd.startswith("git status"):
        return 0, "On branch main\nnothing to commit, working tree clean"
    if cmd.startswith(("pip install", "npm install", "dotnet restore")):
        return 0, "Successfully installed 1 package"
    if cmd.startswith(("make", "python -m build", "dotnet build", "npm run build")):
        return 0, "Build succeeded. 0 warnings, 0 errors"
    if cmd.startswith(("cat ", "head ", "tail ")):
        return 0, f"(see read_file for {cmd.split()[-1]})"
    digest = hashlib.sha256(cmd.encode()).hexdigest()[:12]
    return 0, f"ok {digest}"


@mcp.tool(name=NAME)
@instrument(NAME)
async def shell(command: str = "ls", service_ms: int | None = None) -> dict:
    """Return the simulated exit code and output of a command."""
    await burn(service_ms, "cpu")
    code, out = _output(command)
    return {"command": command, "exit_code": code, "stdout": out, "stderr": ""}


if __name__ == "__main__":
    run(mcp, NAME)
