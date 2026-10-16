import subprocess
import os
import tempfile
import threading
import time
import queue
import platform
import re
import sys
import shlex
from core.logger.logger import logger


def _is_single_cmd_destroyer(sub_command: str, base_cwd: str) -> bool:
    sub_command = sub_command.strip()
    if not sub_command:
        return False

    cmd_lower = sub_command.lower()
    if (
        re.search(r'\brm\s+-[rf]+\s+(/+|\*+|/\*+)\s*$', cmd_lower)
        or re.search(r'\bdel\s+/[fsq]+\s+[a-z]:\\(\*+)?\s*$', cmd_lower)
        or re.search(r'\b(rd|rmdir)\s+/[sq]+\s+[a-z]:\\(\*+)?\s*$', cmd_lower)
    ):
        return True

    is_windows = platform.system() == 'Windows'
    try:
        tokens = shlex.split(sub_command, posix=not is_windows)
    except ValueError:
        tokens = sub_command.split()

    tokens = [t.strip('\'"') for t in tokens if t.strip('\'"')]
    if not tokens:
        return False

    if not is_windows and tokens[0].lower() == 'sudo' and len(tokens) > 1:
        tokens = tokens[1:]

    cmd = tokens[0].lower()

    if cmd in ['format', 'diskpart', 'mkfs', 'fdisk', 'parted', 'bcdedit', 'vssadmin']:
        return True

    if cmd == 'dd':
        for token in tokens[1:]:
            t_low = token.lower()
            if t_low.startswith('of=') or t_low.startswith('if='):
                if any(dev in t_low for dev in ['/dev/sd', '/dev/nvme', '/dev/hd', '\\\\.\\physicaldrive']):
                    return True
        return False

    if cmd in ['rm', 'del', 'rd', 'rmdir', 'erase']:
        tokens_lower = [t.lower() for t in tokens[1:]]
        has_force_recursive = False

        if is_windows:
            for t_low in tokens_lower:
                if t_low in ['/s', '/q', '/f'] or re.match(r'^/[sqf]+$', t_low):
                    has_force_recursive = True
                    break
        else:
            if (
                '-rf' in tokens_lower
                or '-fr' in tokens_lower
                or ('--recursive' in tokens_lower and '--force' in tokens_lower)
                or ('-r' in tokens_lower and '-f' in tokens_lower)
                or '-r' in tokens_lower
            ):
                has_force_recursive = True

        if not has_force_recursive:
            return False

        targets = []
        for token in tokens[1:]:
            if token.startswith('-'):
                continue
            if is_windows and re.match(r'^/[a-zA-Z?]+$', token):
                continue
            targets.append(token)

        if not targets:
            if sub_command.endswith('/') or sub_command.endswith('/*') or sub_command.endswith('\\') or sub_command.endswith('\\*'):
                return True
            return False

        for target in targets:
            clean_target = target.rstrip('*').strip()
            if not clean_target or clean_target in ['/', '\\', '.']:
                return True
            try:
                candidate = clean_target if os.path.isabs(clean_target) else os.path.join(base_cwd, clean_target)
                abs_path = os.path.normcase(os.path.realpath(candidate))
            except Exception:
                continue

            if is_windows:
                norm_trimmed = abs_path.rstrip('\\')
                if re.match(r'^[a-z]:$', norm_trimmed):
                    return True
                protected_win = [
                    'c:\\windows',
                    'c:\\windows\\system32',
                    'c:\\program files',
                    'c:\\program files (x86)',
                    'c:\\users',
                ]
                if norm_trimmed in protected_win or any(norm_trimmed.startswith(p + '\\') for p in ['c:\\windows', 'c:\\windows\\system32']):
                    return True
            else:
                norm_trimmed = abs_path.rstrip('/') or '/'
                protected_unix = ['/', '/boot', '/etc', '/usr', '/bin', '/sbin', '/lib', '/lib64', '/dev', '/var', '/root', '/home']
                if norm_trimmed in protected_unix:
                    return True

        return False

    return False


def _is_system_destroyer(command: str, cwd: str = None) -> bool:
    if not command or not command.strip():
        return False

    base_cwd = cwd if (cwd and os.path.isdir(cwd)) else os.path.expanduser("~")
    sub_commands = re.split(r'\s*(?:&&|\|\||;|\||\n|(?<![>&])&(?![>&]))\s*', command.strip())

    for sub_cmd in sub_commands:
        if _is_single_cmd_destroyer(sub_cmd, base_cwd):
            return True
    return False


def is_python_code_safe(code: str) -> bool:
    if not code or not code.strip():
        return True

    always_blocked = [
        r'\bwinreg\b',
        r'__import__\s*\(\s*[\'"]os[\'"]\s*\)\s*\.\s*system',
        r'ctypes\.windll\.kernel32\.ExitWindows',
    ]
    for pattern in always_blocked:
        if re.search(pattern, code):
            return False

    code_lower = code.lower()

    lethal_system_targets = [
        r'c:[/\\]+windows',
        r'system32',
        r'\\\\\.\\physicaldrive',
        r'/dev/sd[a-z]',
        r'/dev/nvme',
        r'/boot\b',
        r'/etc/passwd',
        r'/etc/shadow',
    ]

    file_delete_patterns = [r'shutil\.rmtree', r'os\.remove', r'os\.unlink', r'os\.rmdir', r'os\.chmod', r'os\.chown', r'\.unlink\s*\(', r'\.rmdir\s*\(']
    if any(re.search(p, code) for p in file_delete_patterns):
        if any(re.search(t, code_lower) for t in lethal_system_targets):
            return False

    os_exec_patterns = [r'\bsubprocess\b', r'os\.system', r'os\.popen', r'os\.spawn']
    if any(re.search(p, code) for p in os_exec_patterns):
        risky_shell_commands = [
            r'\brm\s+-[rf]+\s+/',
            r'\bformat\s+[a-z]:',
            r'\bfdisk\b',
            r'\bmkfs\b',
            r'\bparted\b',
            r'\bshutdown\b',
            r'\breboot\b',
            r'\bpoweroff\b',
            r'\bdiskpart\b',
            r'\bvssadmin\b',
            r'\bbcdedit\b',
        ]
        if any(re.search(r_cmd, code_lower) for r_cmd in risky_shell_commands):
            return False
        if any(re.search(t, code_lower) for t in lethal_system_targets):
            return False

    return True


class StatefulTerminal:
    def __init__(self):
        self.is_windows = platform.system() == "Windows"
        self.cwd = os.path.expanduser("~")
        self.lock = threading.Lock()
        self.process = None
        self.output_queue = queue.Queue()
        self.reader_thread = None
        self._start_process()

    def _start_process(self):
        if not self.cwd or not os.path.isdir(self.cwd):
            self.cwd = os.path.expanduser("~")

        shell_cmd = ["cmd.exe", "/Q"] if self.is_windows else ["/bin/bash", "--noprofile", "--norc"]
        custom_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "PIP_PROGRESS_BAR": "off",
            "CI": "true",
            "NPM_CONFIG_PROGRESS": "false",
        }

        self.output_queue = queue.Queue()
        self.process = subprocess.Popen(
            shell_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            shell=False,
            cwd=self.cwd,
            env=custom_env,
        )
        self.reader_thread = threading.Thread(target=self._read_output, args=(self.process.stdout, self.output_queue), daemon=True)
        self.reader_thread.start()
        self._read_until_empty(timeout=0.2)

    @staticmethod
    def _read_output(pipe, out_queue):
        try:
            for line in iter(pipe.readline, ''):
                out_queue.put(line)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def _kill_process(self):
        if self.process is None:
            return
        try:
            if self.process.poll() is None:
                if self.is_windows:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                else:
                    self.process.kill()
                self.process.wait(timeout=3)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None

    def _drain_queue(self):
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break

    def _read_until_empty(self, timeout=0.2):
        lines = []
        end_time = time.time() + timeout
        while time.time() < end_time:
            try:
                line = self.output_queue.get(timeout=0.05)
                if line:
                    lines.append(line)
                    end_time = time.time() + timeout
            except queue.Empty:
                pass
        return "".join(lines)

    def execute(self, command: str, timeout: int = 30):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self._start_process()

            self._drain_queue()
            marker = f"__CMD_END_{int(time.time() * 1000)}__"

            if self.is_windows:
                full_command = f"@echo off\n{command}\n@echo {marker}:%ERRORLEVEL%:%CD%\n"
            else:
                full_command = f"{command}\nprintf '{marker}:%s:%s\\n' \"$?\" \"$PWD\"\n"

            try:
                self.process.stdin.write(full_command)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._kill_process()
                self._start_process()
                self.process.stdin.write(full_command)
                self.process.stdin.flush()

            lines = []
            exit_code = 0
            timed_out = True
            end_time = time.time() + max(1, int(timeout))

            while time.time() < end_time:
                try:
                    line = self.output_queue.get(timeout=0.1)
                    stripped = line.strip()
                    if stripped.startswith(f"{marker}:"):
                        parts = stripped.split(":", 2)
                        if len(parts) >= 2:
                            try:
                                exit_code = int(parts[1].strip())
                            except ValueError:
                                exit_code = 0
                        if len(parts) == 3:
                            new_cwd = parts[2].strip()
                            if new_cwd and os.path.isdir(new_cwd):
                                self.cwd = new_cwd
                        timed_out = False
                        break
                    if marker in stripped:
                        continue
                    lines.append(line)
                except queue.Empty:
                    if self.process.poll() is not None and self.output_queue.empty():
                        timed_out = False
                        break
                    continue

            if timed_out:
                self._kill_process()
                self._start_process()

            output = "".join(lines).strip()
            MAX_OUTPUT_LENGTH = 50000
            if len(output) > MAX_OUTPUT_LENGTH:
                output = (
                    output[:25000]
                    + "\n\n... [OUTPUT TRUNCATED BY SYSTEM TO SAVE CONTEXT] ...\n\n"
                    + output[-25000:]
                )
            return output, timed_out, exit_code


_terminal_session = None
_session_init_lock = threading.Lock()


def get_terminal_session() -> StatefulTerminal:
    global _terminal_session
    if _terminal_session is None:
        with _session_init_lock:
            if _terminal_session is None:
                _terminal_session = StatefulTerminal()
    return _terminal_session


def execute_terminal_command(command: str, timeout_seconds: int = 30) -> str:
    if not command or not command.strip():
        return "Observation: [ERROR] Empty terminal command provided."

    try:
        timeout_seconds = max(1, int(timeout_seconds))
    except (ValueError, TypeError):
        timeout_seconds = 30

    current_cwd = _terminal_session.cwd if _terminal_session is not None else os.path.expanduser("~")
    if _is_system_destroyer(command, cwd=current_cwd):
        logger.error(f"[SYSTEM PROTECTION] Auto-blocked lethal command: {command}")
        return "Observation: [ERROR] 🚫 CRITICAL SYSTEM PROTECTION ACTIVE. Command targets system core and was automatically blocked. No execution took place."

    if command.strip().lower().startswith("ssh "):
        logger.info(f"[DIRECT SSH EXECUTION]: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                encoding='utf-8',
                errors='replace',
            )
            output = (result.stdout + "\n" + result.stderr).strip()
            if result.returncode != 0:
                return f"Observation: [FAILED] SSH command exited with code {result.returncode}.\nOutput:\n{output}"
            if output:
                return f"Observation: Command executed.\nOutput:\n{output}"
            return "Observation: Command executed successfully with no output."
        except subprocess.TimeoutExpired:
            return f"Observation: [ERROR] SSH command timed out after {timeout_seconds} seconds."
        except Exception as e:
            return f"Observation: [ERROR] SSH execution crashed -> {str(e)}"

    heavy_keywords = ['pip install', 'npm install', 'npm i ', 'git clone', 'yarn add', 'pnpm install', 'apt-get install']
    if any(kw in command.lower() for kw in heavy_keywords) and timeout_seconds <= 30:
        timeout_seconds = 180
        logger.warning(f"[HEAVY COMMAND DETECTED]: Auto-extending terminal timeout to {timeout_seconds} seconds.")

    logger.info(f"[TERMINAL EXECUTION]: {command}")
    try:
        session = get_terminal_session()
        output, timed_out, exit_code = session.execute(command, timeout=timeout_seconds)

        if timed_out:
            if output:
                return f"Observation: [ERROR] Terminal command timed out after {timeout_seconds} seconds and was terminated.\nPartial Output:\n{output}"
            return f"Observation: [ERROR] Terminal command timed out after {timeout_seconds} seconds and was terminated."

        if exit_code != 0:
            if output:
                return f"Observation: [FAILED] Command exited with code {exit_code}.\nOutput:\n{output}"
            return f"Observation: [FAILED] Command exited with code {exit_code} and produced no output."

        if output:
            return f"Observation: Command executed.\nOutput:\n{output}"
        return "Observation: Command executed successfully with no output."
    except Exception as e:
        return f"Observation: [ERROR] Terminal execution crashed -> {str(e)}"


def run_python_code(code_string: str) -> str:
    if not is_python_code_safe(code_string):
        logger.warning("[SECURITY] Unsafe Python code blocked.")
        return "Observation: [ERROR] Python code blocked for safety reasons. If you trust this code, modify the safety rules."

    logger.info("[PYTHON EXECUTION]: Running Python script...")
    fd, temp_path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(code_string)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        python_path = sys.executable
        result = subprocess.run(
            [python_path, temp_path],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            timeout=120,
        )
        output = result.stdout.strip()
        error = result.stderr.strip()
        full_output = ""
        if output:
            full_output += f"Output:\n{output}\n"
        if error:
            full_output += f"Error:\n{error}\n"
        MAX_OUTPUT_LENGTH = 50000
        if len(full_output) > MAX_OUTPUT_LENGTH:
            full_output = full_output[:25000] + "\n\n... [TRUNCATED] ...\n\n" + full_output[-25000:]
        if result.returncode == 0:
            return f"Observation: Python code executed successfully.\n{full_output}".strip()
        else:
            return f"Observation: [FAILED] Python script failed (exit code {result.returncode}).\n{full_output}".strip()
    except subprocess.TimeoutExpired:
        return "Observation: [ERROR] Python script timed out after 120 seconds."
    except Exception as e:
        return f"Observation: [ERROR] Python execution crashed -> {str(e)}"
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass