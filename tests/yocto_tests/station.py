"""Station transport and configuration. All commands have bounded execution."""
import getpass
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

import paramiko
import yaml


def quote(value):
    return shlex.quote(str(value))


def load_config(path):
    def expand(value):
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        if isinstance(value, str):
            def replace(match):
                name = match.group(1) or match.group(2)
                if name not in os.environ:
                    raise ValueError(f"Missing environment variable {name}")
                return os.environ[name]
            return re.sub(r"\$\{([A-Z_][A-Z_0-9]*)\}|\$([A-Z_][A-Z_0-9]*)", replace, value)
        return value
    cfg = expand(yaml.safe_load(Path(path).read_text()))
    if not isinstance(cfg, dict):
        raise ValueError("Station configuration must be a YAML mapping")
    if cfg.get("platform") not in {"cpu01", "cpu01plus"}:
        raise ValueError("platform must be cpu01 or cpu01plus")
    if not cfg.get("target", {}).get("host"):
        raise ValueError("target.host is required")
    if cfg.get("closed") and cfg.get("installer_image") == "ci4rail/tdx-installer-dev:v3":
        raise ValueError("Closed devices require an updated signed installer image")
    if not isinstance(cfg.get("tc"), dict):
        raise ValueError("tc configuration is required")
    if cfg["tc"].get("local") not in {True, False, None}:
        raise ValueError("tc.local must be a YAML boolean")
    return cfg


class Host:
    def __init__(self, config, log_dir, name):
        self.config, self.name = config, name
        self.local = config.get("local", False)
        self.log = Path(log_dir) / f"{name}.log"

    def connect(self):
        c = paramiko.SSHClient()
        if self.config.get("allow_unknown_host_key", False):
            # Dedicated reflashed target: do not compare against stale saved keys.
            # This policy is opt-in and must not alter the TC trust policy.
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else:
            c.load_system_host_keys()
        opts = self.config
        ssh_cfg = paramiko.SSHConfig()
        path = Path.home() / ".ssh/config"
        if path.exists():
            with path.open() as f:
                ssh_cfg.parse(f)
        entry = ssh_cfg.lookup(opts["host"])
        proxy = paramiko.ProxyCommand(entry["proxycommand"]) if "proxycommand" in entry else None
        try:
            c.connect(
                entry.get("hostname", opts["host"]),
                port=int(opts.get("port", entry.get("port", 22))),
                username=opts.get("username", entry.get("user", getpass.getuser())),
                password=opts.get("password"),
                key_filename=str(Path(opts["key_file"]).expanduser()) if opts.get("key_file") else entry.get("identityfile"),
                allow_agent=not bool(opts.get("password")), look_for_keys=not bool(opts.get("password")),
                timeout=15, auth_timeout=15, banner_timeout=15, sock=proxy,
            )
        except Exception:
            c.close()
            if proxy:
                proxy.close()
            raise
        return c

    def run(self, command, timeout=60, check=True, input=None, secret=False):
        if self.local:
            p = subprocess.run(["bash", "-c", command], input=input, text=True,
                               capture_output=True, timeout=timeout)
            status, out, err = p.returncode, p.stdout, p.stderr
        else:
            with self.connect() as client:
                channel = client.get_transport().open_session(timeout=15)
                channel.exec_command(command)
                if input is not None:
                    channel.sendall(input.encode())
                channel.shutdown_write()
                start, stdout, stderr = time.monotonic(), bytearray(), bytearray()
                while True:
                    while channel.recv_ready():
                        stdout.extend(channel.recv(65536))
                    while channel.recv_stderr_ready():
                        stderr.extend(channel.recv_stderr(65536))
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
                    if time.monotonic() - start > timeout:
                        channel.close()
                        raise TimeoutError(f"{self.name}: command timed out after {timeout}s")
                    time.sleep(0.02)
                status = channel.recv_exit_status()
                out, err = stdout.decode(errors="replace"), stderr.decode(errors="replace")
        if not secret:
            with self.log.open("a") as f:
                f.write(f"$ {command}\n{out}{err}\nexit={status}\n")
        if check and status:
            detail = "sensitive command failed" if secret else f"{command}\n{out}{err}"
            raise RuntimeError(f"{self.name}: exit {status}: {detail}")
        return out

    def put(self, source, destination):
        if self.local:
            import shutil
            shutil.copyfile(source, destination)
        else:
            with self.connect() as client, client.open_sftp() as sftp:
                sftp.put(str(source), destination)

    def get(self, source, destination):
        if self.local:
            import shutil
            shutil.copyfile(source, destination)
        else:
            with self.connect() as client, client.open_sftp() as sftp:
                sftp.get(source, str(destination))


class Station:
    def __init__(self, cfg, results):
        self.cfg = cfg
        self.results = Path(results).resolve()
        self.results.mkdir(parents=True, exist_ok=True)
        self.tc = Host(cfg["tc"], self.results, "tc")
        self.dut = Host(cfg["target"], self.results, "dut")

    def security(self, command, timeout=600):
        return self.tc.run(
            f"docker run --rm --network=host --cap-add=NET_RAW {quote(self.cfg['security_image'])} -lc {quote(command)}",
            timeout=timeout)

    def wait_boot(self):
        deadline = time.monotonic() + self.cfg.get("boot_timeout", 300)
        last = None
        while time.monotonic() < deadline:
            try:
                self.dut.run("true", timeout=20)
                return
            except (OSError, paramiko.SSHException, RuntimeError, TimeoutError) as exc:
                last = exc
                time.sleep(2)
        raise RuntimeError(f"Target did not become reachable: {last}")
