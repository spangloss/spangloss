"""spangloss scenarios run: one scripted Claude Code session in an isolated container.

docs/scenarios-design.md, sections 3 and 4. Per run:

- an internal Docker network with no route out, and volumes for the workspace,
  home, Collector output and config, all named sg-<run_id>-*. Only the proxy also
  joins a second network with internet access, so the agent's container reaches
  nothing but the run's own services;
- a key proxy (nginx) that alone holds the dedicated API key, read from
  ~/.config/spangloss/scenarios.env; the agent's container gets a placeholder key;
- a Collector writing the run's traces and logs to its own volume;
- for scenarios with a site, a local HTTPS stand-in for the fixtures site;
- the agent container, running `claude -p` with the scenario's flags.

Everything is removed afterwards unless keep is set. Only the traces, logs, the
stream-json transcript and run.json are copied out, to data/scenarios/runs/<run_id>/.
"""

import io
import json
import re
import secrets
import stat
import subprocess
import tarfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spangloss.parse import parse_claude_code
from spangloss.scenarios.spec import SCENARIOS_DIR, Scenario

CLAUDE_CODE_VERSION = "2.1.312"
AGENT_IMAGE = f"spangloss-scenarios:{CLAUDE_CODE_VERSION}"
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.162.0"
PROXY_IMAGE = "nginx:1.27-alpine"
HELPER_IMAGE = "alpine:3.20"

KEY_FILE = Path.home() / ".config" / "spangloss" / "scenarios.env"
KEY_VARIABLE = "ANTHROPIC_API_KEY"
PLACEHOLDER_KEY = "placeholder-the-proxy-adds-the-real-key"
PROXY_HOST = "anthropic-proxy"
PAGES_HOST = "docs.tinyfmt.example"
RUNS_DIR = Path("data/scenarios/runs")
CANARY_FILE = "spangloss-canary.txt"
# Before the session, the runner checks from the agent image that the internet is
# unreachable and that the proxy refuses a path it doesn't allow (section 4).
NETWORK_PROBE_PATH = "/spangloss-network-check"
NETWORK_CHECK_SCRIPT = f"""
if curl -s -o /dev/null -m 5 https://api.anthropic.com/; then echo internet=reachable;
else echo internet=blocked; fi
printf 'proxy_probe='; curl -s -o /dev/null -m 5 -w '%{{http_code}}\\n' \\
  http://{PROXY_HOST}:8080{NETWORK_PROBE_PATH} || echo error
"""

# The proxy logs denied requests as "<METHOD> <path> <status>"; nginx's startup
# messages share the stream.
DENIED_LINE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\S* \d{3}$")
INFRA = SCENARIOS_DIR / "infra"

# Lists a directory tree as {relative path: {sha256, size}}, skipping nothing the
# agent could have written. Run inside the agent image.
LISTING_SCRIPT = r"""
import hashlib, json, os, sys
out = {}
for root, prefix in (("/workspace", ""), ("/home/agent", "~/")):
    for d, dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(d, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            with open(p, "rb") as f:
                data = f.read()
            rel = prefix + os.path.relpath(p, root)
            out[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
json.dump(out, sys.stdout, sort_keys=True)
"""


class RunnerError(RuntimeError):
    pass


def read_key_file(path: Path = KEY_FILE) -> str:
    """The dedicated key. The file must be mode 600 and hold exactly one line,
    ANTHROPIC_API_KEY=<key>. The key is returned for the publishing gate only."""
    if not path.exists():
        raise RunnerError(f"{path} not found; see docs/scenarios-design.md, Sign-in")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise RunnerError(f"{path} must be mode 600, not {mode:o}")
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith(f"{KEY_VARIABLE}="):
        raise RunnerError(f"{path} must hold exactly one line, {KEY_VARIABLE}=<key>")
    key = lines[0].split("=", 1)[1].strip()
    if not key:
        raise RunnerError(f"{path}: {KEY_VARIABLE} is empty")
    return key


def new_run_id(scenario: Scenario) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{scenario.name}-v{scenario.version}-{stamp}-{secrets.token_hex(2)}"


@dataclass(frozen=True, slots=True)
class Names:
    run_id: str

    def __getattr__(self, part: str) -> str:  # names.network, names.workspace, ...
        if part.startswith("_"):
            raise AttributeError(part)
        return f"sg-{self.run_id}-{part}".lower()


def claude_argv(scenario: Scenario, session_id: str, prompt: str) -> list[str]:
    c = scenario.claude
    argv = [
        "claude",
        "-p",
        prompt,
        "--model",
        c.model,
        "--tools",
        ",".join(c.tools),
        "--permission-mode",
        c.permission_mode,
        "--permission-prompts",
        "none",
        "--max-turns",
        str(c.max_turns),
        "--max-budget-usd",
        f"{c.max_budget_usd:.2f}",
        "--session-id",
        session_id,
        "--no-session-persistence",
        "--strict-mcp-config",
        "--settings",
        "/scenario-cfg/settings.json",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if c.allowed_tools:
        argv += ["--allowedTools", ",".join(c.allowed_tools)]
    if c.disallowed_tools:
        argv += ["--disallowedTools", ",".join(c.disallowed_tools)]
    if c.agents:
        argv += ["--agents", json.dumps(c.agents, separators=(",", ":"))]
    return argv


def agent_env(scenario: Scenario, run_id: str) -> dict[str, str]:
    """The agent container's whole environment besides the image's PATH. It holds
    no credential: the proxy adds the key."""
    env = {
        "HOME": "/home/agent",
        "USER": "agent",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "ANTHROPIC_BASE_URL": f"http://{PROXY_HOST}:8080",
        KEY_VARIABLE: PLACEHOLDER_KEY,
        "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1",
        "DISABLE_AUTOUPDATER": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317",
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_LOG_TOOL_CONTENT": "1",
        "OTEL_LOG_USER_PROMPTS": "1",
        "OTEL_RESOURCE_ATTRIBUTES": (
            f"scenario.name={scenario.name},scenario.version={scenario.version},"
            f"scenario.run_id={run_id}"
        ),
        "CLAUDE_CODE_OTEL_SHUTDOWN_TIMEOUT_MS": "10000",
    }
    if scenario.site is not None:
        env["NODE_EXTRA_CA_CERTS"] = "/scenario-cfg/tls/cert.pem"
        env["SSL_CERT_FILE"] = "/scenario-cfg/tls/bundle.pem"
    return env


def proxy_argv(names: Names, key_file: Path) -> list[str]:
    """The only container that sees the key, through --env-file."""
    return [
        "run",
        "-d",
        "--name",
        names.proxy,
        "--network",
        names.network,
        "--network-alias",
        PROXY_HOST,
        "--env-file",
        str(key_file),
        "-e",
        f"NGINX_ENVSUBST_FILTER={KEY_VARIABLE}",
        "-v",
        f"{names.cfg}:/cfg:ro",
        "--entrypoint",
        "/bin/sh",
        PROXY_IMAGE,
        "-c",
        (
            "mkdir -p /etc/nginx/templates"
            " && cp /cfg/proxy.conf.template /etc/nginx/templates/default.conf.template"
            " && exec /docker-entrypoint.sh nginx -g 'daemon off;'"
        ),
    ]


def agent_run_argv(names: Names, env: dict[str, str], argv: list[str]) -> list[str]:
    cmd = [
        "run",
        "--rm",
        "--network",
        names.network,
        "-v",
        f"{names.workspace}:/workspace",
        "-v",
        f"{names.home}:/home/agent",
        "-v",
        f"{names.cfg}:/scenario-cfg:ro",
    ]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    return [*cmd, AGENT_IMAGE, *argv]


@dataclass(slots=True)
class Docker:
    """Runs docker commands; tests swap in a recorder."""

    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run
    log: list[list[str]] = field(default_factory=list)

    def __call__(
        self, *args: str, input: bytes | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        self.log.append(list(args))
        result = self.run(["docker", *args], input=input, capture_output=True)
        if check and result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            raise RunnerError(f"docker {args[0]} failed: {err[-500:]}")
        return result


def ensure_image(docker: Docker) -> None:
    if docker("image", "inspect", AGENT_IMAGE, check=False).returncode == 0:
        return
    docker(
        "build",
        "--build-arg",
        f"CLAUDE_CODE_VERSION={CLAUDE_CODE_VERSION}",
        "-t",
        AGENT_IMAGE,
        str(SCENARIOS_DIR),
    )


def run_scenario(
    scenario: Scenario,
    prompt_id: str,
    *,
    key_file: Path = KEY_FILE,
    runs_dir: Path = RUNS_DIR,
    keep: bool = False,
    docker: Docker | None = None,
) -> Path:
    """Run the scenario once; returns the run directory."""
    docker = docker or Docker()
    prompt = scenario.prompt(prompt_id)
    read_key_file(key_file)  # validate before starting anything
    run_id = new_run_id(scenario)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    names = Names(run_id)
    session_id = str(uuid.uuid4())
    run_dir = runs_dir / run_id
    (run_dir / "raw").mkdir(parents=True)
    ensure_image(docker)

    started: list[str] = []
    volumes = [names.workspace, names.home, names.otel, names.cfg]
    try:
        docker("network", "create", "--internal", names.network)
        docker("network", "create", names.egress)
        for v in volumes:
            docker("volume", "create", v)
        _fill_cfg(docker, names, scenario)
        _fill_fixtures(docker, names, scenario)
        _setup(docker, names, scenario)
        pre = _listing(docker, names)

        docker(*proxy_argv(names, key_file))
        started.append(names.proxy)
        docker("network", "connect", names.egress, names.proxy)
        docker(
            "run", "-d", "--name", names.collector, "--network", names.network,
            "--network-alias", "collector", "--user", "0",
            "-v", f"{names.otel}:/out", "-v", f"{names.cfg}:/cfg:ro",
            COLLECTOR_IMAGE, "--config=/cfg/collector.yaml",
        )  # fmt: skip
        started.append(names.collector)
        if scenario.site is not None:
            docker(*_pages_argv(names))
            started.append(names.pages)
        time.sleep(3)  # let the Collector, proxy and pages listen
        network_check = _network_check(docker, names)
        if network_check != {"internet": "blocked", "proxy_probe": "403"}:
            raise RunnerError(f"network isolation check failed: {network_check}")

        argv = claude_argv(scenario, session_id, prompt.text)
        result = docker(
            *agent_run_argv(names, agent_env(scenario, run_id), argv), check=False
        )
        (run_dir / "stream.jsonl").write_bytes(result.stdout)
        (run_dir / "agent.stderr").write_bytes(result.stderr[-20_000:])

        docker("stop", "-t", "15", names.collector)  # flushes the file exporter
        for name in ("traces.jsonl", "logs.jsonl"):
            out = docker(
                "run", "--rm", "-v", f"{names.otel}:/o:ro", HELPER_IMAGE,
                "sh", "-c", f"cat /o/{name} 2>/dev/null || true",
            )  # fmt: skip
            (run_dir / "raw" / name).write_bytes(out.stdout)
        denied = docker("logs", names.proxy, check=False).stdout.decode(
            errors="replace"
        )
        post = _listing(docker, names)
        canary = _canary_file(docker, names, scenario)
    finally:
        for name in started:
            docker("rm", "-f", name, check=False)
        if not keep:
            for v in volumes:
                docker("volume", "rm", "-f", v, check=False)
            docker("network", "rm", names.network, check=False)
            docker("network", "rm", names.egress, check=False)

    stream = _read_stream(run_dir / "stream.jsonl")
    record = {
        "run_id": run_id,
        "scenario": scenario.name,
        "version": scenario.version,
        "prompt_id": prompt.id,
        "prompt": prompt.text,
        "session_id": session_id,
        "started": started_at,
        "pinned_model": scenario.claude.model,
        "claude_code_version": CLAUDE_CODE_VERSION,
        "exit_code": result.returncode,
        "init": stream["init"],
        "result": stream["result"],
        "pre_listing": pre,
        "post_listing": post,
        "network_check": network_check,
        "proxy_denied": [
            line for line in denied.splitlines() if DENIED_LINE.match(line)
        ],
        "canary": canary,
        "publishable": scenario.publishable,
    }
    (run_dir / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    calls = parse_claude_code(
        run_dir / "raw" / "traces.jsonl", run_dir / "raw" / "logs.jsonl"
    )
    with (run_dir / "tool_calls.jsonl").open("w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
    return run_dir


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 1000
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tree(root: Path, prefix: str = "") -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {
        prefix + str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _untar_into(docker: Docker, volume: str, files: dict[str, bytes]) -> None:
    docker(
        "run", "--rm", "-i", "-v", f"{volume}:/v", HELPER_IMAGE,
        "sh", "-c", "tar -C /v -xf - && chown -R 1000:1000 /v",
        input=_tar(files),
    )  # fmt: skip


def _fill_cfg(docker: Docker, names: Names, scenario: Scenario) -> None:
    files = {
        "collector.yaml": (INFRA / "collector.yaml").read_bytes(),
        "settings.json": (INFRA / "settings.json").read_bytes(),
        "proxy.conf.template": (INFRA / "proxy.conf.template").read_bytes(),
    }
    if scenario.site is not None:
        files |= _tree(scenario.site, "site/")
    _untar_into(docker, names.cfg, files)
    if scenario.site is not None:
        # A throwaway certificate for the local pages stand-in, valid for two days.
        docker(
            "run", "--rm", "-v", f"{names.cfg}:/c", HELPER_IMAGE, "sh", "-c",
            "apk add -q openssl >/dev/null && mkdir -p /c/tls && openssl req -x509"
            " -newkey rsa:2048 -nodes -days 2"
            f" -subj /CN={PAGES_HOST} -addext subjectAltName=DNS:{PAGES_HOST}"
            " -keyout /c/tls/key.pem -out /c/tls/cert.pem 2>/dev/null"
            " && chmod 644 /c/tls/cert.pem",
        )  # fmt: skip
        docker(
            "run", "--rm", "--user", "0", "-v", f"{names.cfg}:/c", AGENT_IMAGE,
            "sh", "-c",
            "cat /etc/ssl/certs/ca-certificates.crt /c/tls/cert.pem > /c/tls/bundle.pem",
        )  # fmt: skip


def _fill_fixtures(docker: Docker, names: Names, scenario: Scenario) -> None:
    _untar_into(docker, names.workspace, _tree(scenario.workspace_fixtures))
    _untar_into(docker, names.home, _tree(scenario.home_fixtures))


def _setup(docker: Docker, names: Names, scenario: Scenario) -> None:
    """Setup steps run with no network and no telemetry, so they leave no spans."""
    for step in scenario.setup:
        docker(
            "run", "--rm", "--network", "none",
            "-v", f"{names.workspace}:/workspace", "-v", f"{names.home}:/home/agent",
            "-e", "HOME=/home/agent", "-e", "GIT_CONFIG_NOSYSTEM=1",
            AGENT_IMAGE, "bash", "-c", step,
        )  # fmt: skip


def _listing(docker: Docker, names: Names) -> dict[str, Any]:
    out = docker(
        "run", "--rm", "--network", "none",
        "-v", f"{names.workspace}:/workspace:ro", "-v", f"{names.home}:/home/agent:ro",
        AGENT_IMAGE, "python3", "-c", LISTING_SCRIPT,
    )  # fmt: skip
    return json.loads(out.stdout or b"{}")


def _pages_argv(names: Names) -> list[str]:
    server = (
        "import http.server,ssl,functools;"
        "h=functools.partial(http.server.SimpleHTTPRequestHandler,directory='/c/site');"
        "s=http.server.ThreadingHTTPServer(('0.0.0.0',443),h);"
        "c=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);"
        "c.load_cert_chain('/c/tls/cert.pem','/c/tls/key.pem');"
        "s.socket=c.wrap_socket(s.socket,server_side=True);s.serve_forever()"
    )
    return [
        "run", "-d", "--name", names.pages, "--network", names.network,
        "--network-alias", PAGES_HOST, "--user", "0",
        "-v", f"{names.cfg}:/c:ro", AGENT_IMAGE, "python3", "-c", server,
    ]  # fmt: skip


def _network_check(docker: Docker, names: Names) -> dict[str, str]:
    out = docker(
        "run", "--rm", "--network", names.network, AGENT_IMAGE,
        "sh", "-c", NETWORK_CHECK_SCRIPT,
    )  # fmt: skip
    pairs = (
        line.split("=", 1) for line in out.stdout.decode().splitlines() if "=" in line
    )
    return {k.strip(): v.strip() for k, v in pairs}


def _canary_file(docker: Docker, names: Names, scenario: Scenario) -> dict[str, Any]:
    if not scenario.canary_token:
        return {}
    out = docker(
        "run", "--rm", "--network", "none", "-v", f"{names.workspace}:/w:ro",
        HELPER_IMAGE, "sh", "-c", f"cat /w/{CANARY_FILE} 2>/dev/null || true",
    )  # fmt: skip
    text = out.stdout.decode(errors="replace")
    return {"file_present": scenario.canary_token in text}


def _read_stream(path: Path) -> dict[str, Any]:
    init: dict[str, Any] = {}
    result: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = {
                "model": event.get("model"),
                "tools": event.get("tools"),
                "agents": event.get("agents"),
                "plugins": [p.get("name") for p in event.get("plugins") or []],
                "mcp_servers": [s.get("name") for s in event.get("mcp_servers") or []],
                "permission_mode_reported": event.get("permissionMode"),
                "claude_code_version": event.get("claude_code_version"),
            }
        elif event.get("type") == "result":
            result = {
                "is_error": event.get("is_error"),
                "num_turns": event.get("num_turns"),
                "total_cost_usd": event.get("total_cost_usd"),
                "permission_denials": [
                    {"tool_name": d.get("tool_name")}
                    for d in event.get("permission_denials") or []
                ],
            }
    return {"init": init, "result": result}
