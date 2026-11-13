"""Station transport and configuration. All commands have bounded execution."""
import getpass
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import uuid

import paramiko
import yaml

ERROR_TAIL_LINES = 50


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


LOGIN_KEYS = ("username", "password", "key_file", "key_only", "sudo_password")
# Root's PATH and home for commands run through sudo (dropbear gives other users /usr/bin:/bin).
SUDO_PREFIX = "export HOME=/root PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; cd /root; "


class Host:
    """A shell on the TC or the target.

    `alternatives` are further SSH logins (username, password or key_file,
    key_only, sudo_password) tried when the configured one is rejected, e.g.
    because a customer profile only allows a sudo user. The login that worked
    last is tried first. With a sudo_password, commands run as root via sudo.
    """

    def __init__(self, config, log_dir, name, jump=None, alternatives=()):
        self.config, self.name = config, name
        # Reach this host through another one (the TC is inside the LAN that
        # customer firewall profiles allow; the CI runner usually is not).
        self.jump = jump if jump is not None and not jump.local else None
        self.local = config.get("local", False)
        self.log = Path(log_dir) / f"{name}.log"
        login = {k: config[k] for k in LOGIN_KEYS if k in config}
        self.logins = [login] + [dict(a) for a in alternatives if dict(a) != login]
        self.login = self.logins[0]

    @property
    def sudo(self):
        return not self.local and bool(self.login.get("sudo_password"))

    def connect(self):
        """Connect with the last working login first, then the others in order."""
        error = None
        for login in sorted(self.logins, key=lambda l: l is not self.login):
            try:
                client = self._connect(login)
            except paramiko.AuthenticationException as exc:
                error = exc
                continue
            self.login = login
            return client
        raise error

    def _connect(self, login):
        c = paramiko.SSHClient()
        jump_client = None
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
        hostname, port = entry.get("hostname", opts["host"]), int(opts.get("port", entry.get("port", 22)))
        try:
            if self.jump is not None:
                jump_client = self.jump.connect()
                proxy = jump_client.get_transport().open_channel("direct-tcpip", (hostname, port), ("127.0.0.1", 0))
            c.connect(
                hostname,
                port=port,
                username=login.get("username", entry.get("user", getpass.getuser())),
                password=login.get("password"),
                key_filename=str(Path(login["key_file"]).expanduser()) if login.get("key_file") else entry.get("identityfile"),
                allow_agent=not (login.get("password") or login.get("key_only")),
                look_for_keys=not (login.get("password") or login.get("key_only")),
                timeout=15, auth_timeout=15, banner_timeout=15, sock=proxy,
            )
        except Exception:
            c.close()
            if proxy:
                proxy.close()
            if jump_client is not None:
                jump_client.close()
            raise
        if jump_client is not None:
            close = c.close  # closing the connection also closes the jump host connection

            def close_both():
                close()
                jump_client.close()
            c.close = close_both
        return c

    def run(self, command, timeout=60, check=True, input=None, secret=False):
        if self.local:
            # Serial console noise in installer output is not valid UTF-8.
            p = subprocess.run(["bash", "-c", command], input=input, text=True,
                               encoding="utf-8", errors="replace",
                               capture_output=True, timeout=timeout)
            status, out, err = p.returncode, p.stdout, p.stderr
        else:
            with self.connect() as client:
                remote, data = command, input
                if self.sudo:
                    # -k: always ask, so the password line is never left for the command's stdin.
                    remote = f"sudo -k -S -p '' /bin/sh -c {quote(SUDO_PREFIX + command)}"
                    data = self.login["sudo_password"] + "\n" + (input or "")
                channel = client.get_transport().open_session(timeout=15)
                channel.exec_command(remote)
                if data is not None:
                    channel.sendall(data.encode())
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
            if secret:
                detail = "sensitive command failed"
            else:
                # The full output is in the log; a long error floods the CI result channel.
                lines = f"{out}{err}".splitlines()
                tail = "\n".join(lines[-ERROR_TAIL_LINES:])
                if len(lines) > ERROR_TAIL_LINES:
                    tail = f"[{len(lines) - ERROR_TAIL_LINES} earlier lines in {self.log}]\n{tail}"
                detail = f"{command}\n{tail}"
            raise RuntimeError(f"{self.name}: exit {status}: {detail}")
        return out

    def put(self, source, destination):
        if self.local:
            import shutil
            shutil.copyfile(source, destination)
            return
        with self.connect() as client, client.open_sftp() as sftp:
            if not self.sudo:
                sftp.put(str(source), destination)
                return
            # The sudo user cannot write root's paths: stage in /tmp, then copy as root.
            staged = f"/tmp/yocto-put-{uuid.uuid4().hex}"
            try:
                sftp.put(str(source), staged)
                self.run(f"cp {quote(staged)} {quote(destination)}")
            finally:
                self.run(f"rm -f {quote(staged)}", check=False)

    def get(self, source, destination):
        if self.local:
            import shutil
            shutil.copyfile(source, destination)
            return
        with self.connect() as client, client.open_sftp() as sftp:
            if not self.sudo:
                sftp.get(source, str(destination))
                return
            staged = f"/tmp/yocto-get-{uuid.uuid4().hex}"
            try:
                self.run(f"cp {quote(source)} {quote(staged)} && chown {quote(self.login['username'])} {quote(staged)}")
                sftp.get(staged, str(destination))
            finally:
                self.run(f"rm -f {quote(staged)}", check=False)


class Station:
    def __init__(self, cfg, results, target_logins=()):
        """`target_logins`: SSH logins that customer profiles may leave as the only way in."""
        self.cfg = cfg
        self.results = Path(results).resolve()
        self.results.mkdir(parents=True, exist_ok=True)
        self.tc = Host(cfg["tc"], self.results, "tc")
        self.dut = Host(cfg["target"], self.results, "dut", jump=self.tc if cfg["target"].get("via_tc") else None,
                        alternatives=target_logins)

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
