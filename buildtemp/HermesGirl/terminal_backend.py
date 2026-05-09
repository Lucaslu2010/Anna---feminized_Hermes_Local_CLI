import os
import pty
import signal
import select
import struct
import fcntl
import termios
import subprocess
import threading
from typing import Optional, List

from PySide6.QtCore import QObject, Signal, Slot


class TerminalBackend(QObject):
    """
    PTY backend for running Hermes inside a real pseudo-terminal.

    Data flow:
        xterm.js keyboard input
            -> QWebChannel
            -> write_input()
            -> PTY master fd
            -> Hermes running on PTY slave fd

        Hermes output
            -> PTY master fd
            -> _reader_loop()
            -> output_received signal
            -> WebTerminalPanel
            -> xterm.js terminal display
    """

    output_received = Signal(str)
    raw_output_received = Signal(str)
    process_started = Signal()
    process_stopped = Signal()
    error_received = Signal(str)
    user_input_received = Signal(str)
    def __init__(
        self,
        command: Optional[List[str]] = None,
        cols: int = 120,
        rows: int = 36,
        debug: bool = False,
    ):
        super().__init__()

        self.command = command or ["hermes"]
        self.cols = cols
        self.rows = rows
        self.debug = debug

        self.master_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.running = False

    def _debug(self, *args):
        if self.debug:
            print("[TerminalBackend]", *args)

    @Slot()
    def start_process(self):
        """
        Called by JavaScript after xterm.js and QWebChannel are ready.
        """
        self._debug("start_process called")
        self.start()

    def start(self):
        """
        Start the command inside a pseudo-terminal.
        """

        self._debug("start called, command =", self.command)

        if self.running:
            self._debug("already running")
            return

        try:
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd
            self._set_nonblocking(master_fd)
            self._set_pty_size(self.cols, self.rows)

            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["COLORTERM"] = "truecolor"
            env["COLUMNS"] = str(self.cols)
            env["LINES"] = str(self.rows)

            self._debug("environment PATH =", env.get("PATH", ""))

            self.process = subprocess.Popen(
                self.command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                preexec_fn=os.setsid,
                close_fds=True,
            )

            os.close(slave_fd)

            self.running = True

            self.reader_thread = threading.Thread(
                target=self._reader_loop,
                daemon=True,
            )
            self.reader_thread.start()

            self._debug("process started, pid =", self.process.pid)
            self.process_started.emit()

        except FileNotFoundError:
            message = (
                f"Cannot find command: {self.command}\n"
                "Check HERMES_COMMAND in main.py.\n"
                "If PyCharm cannot find Hermes, use the full path from `which hermes`."
            )
            self._debug(message)
            self.error_received.emit(message)

        except Exception as e:
            message = f"Failed to start terminal backend:\n{e}"
            self._debug(message)
            self.error_received.emit(message)

    @Slot()
    def send_nul_heartbeat(self):
        """
        Send a NUL byte to the PTY to wake up Hermes/TUI refresh.

        This is a workaround for TUI programs that only repaint after receiving input.
        It does not press Enter and usually does not change the current prompt,
        but it is still technically input, so use it only as a controlled heartbeat.
        """

        if not self.running or self.master_fd is None:
            return

        try:
            os.write(self.master_fd, b"\x00")
            self._debug("NUL heartbeat sent")
        except Exception as e:
            self._debug("NUL heartbeat failed:", e)
    @Slot()
    def force_redraw(self):
        """
        Ask the child process to redraw the terminal without sending fake keyboard input.
        This is safer than sending NUL, space, or Enter.
        """
        if not self.process:
            return

        try:
            self._set_pty_size(self.cols, self.rows)

            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGWINCH)
            except Exception:
                pass

            self._debug("force_redraw: SIGWINCH sent")

        except Exception as e:
            self._debug("force_redraw failed:", e)

    def _set_nonblocking(self, fd: int):
        try:
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._debug("master fd set to non-blocking")
        except Exception as e:
            self._debug("failed to set non-blocking:", e)

    def stop(self):
        """
        Stop the child process and close PTY.
        """

        self._debug("stop called")
        self.running = False

        if self.process:
            try:
                pgid = os.getpgid(self.process.pid)
                os.killpg(pgid, signal.SIGTERM)
                self._debug("sent SIGTERM to process group", pgid)
            except Exception:
                try:
                    self.process.terminate()
                    self._debug("sent terminate")
                except Exception:
                    pass

            try:
                self.process.wait(timeout=2)
                self._debug("process exited")
            except Exception:
                try:
                    pgid = os.getpgid(self.process.pid)
                    os.killpg(pgid, signal.SIGKILL)
                    self._debug("sent SIGKILL to process group", pgid)
                except Exception:
                    try:
                        self.process.kill()
                        self._debug("process killed")
                    except Exception:
                        pass

            self.process = None

        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
                self._debug("master fd closed")
            except Exception:
                pass

            self.master_fd = None

        self.process_stopped.emit()

    def _reader_loop(self):
        """
        Background thread:
        continuously read output from PTY master fd and emit it back to Qt main thread.
        """

        self._debug("reader loop started")

        while self.running and self.master_fd is not None:
            try:
                readable, _, _ = select.select([self.master_fd], [], [], 0.02)

                if not readable:
                    continue

                chunks = []

                while True:
                    try:
                        data = os.read(self.master_fd, 8192)

                        if not data:
                            break

                        chunks.append(data)

                        # If less than buffer size, probably drained for now.
                        if len(data) < 8192:
                            break

                    except BlockingIOError:
                        break
                    except OSError:
                        break

                if not chunks:
                    continue

                raw = b"".join(chunks)
                text = raw.decode("utf-8", errors="replace")

                self._debug("read:", repr(text[:200]))

                self.raw_output_received.emit(text)
                self.output_received.emit(text)

            except OSError as e:
                self._debug("reader loop OSError:", e)
                break

            except Exception as e:
                message = f"PTY read error:\n{e}"
                self._debug(message)
                self.error_received.emit(message)
                break

        self.running = False
        self._debug("reader loop stopped")
        self.process_stopped.emit()

    @Slot(str)
    def write_input(self, data: str):
        """
        Called from JavaScript through QWebChannel.
        """

        if not self.running or self.master_fd is None:
            self._debug("write ignored; backend not running")
            return

        if data is None:
            return

        try:
            self.user_input_received.emit(data)

            encoded = data.encode("utf-8", errors="replace")
            os.write(self.master_fd, encoded)

            self._debug("write:", repr(data[:100]))

        except Exception as e:
            message = f"Failed to write to PTY:\n{e}"
            self._debug(message)
            self.error_received.emit(message)

    @Slot(int, int)
    def resize_pty(self, cols: int, rows: int):
        """
        Called from xterm.js when terminal size changes.
        """

        if cols <= 0 or rows <= 0:
            return

        self.cols = cols
        self.rows = rows

        self._debug(f"resize_pty called: cols={cols}, rows={rows}")

        self._set_pty_size(cols, rows)

    def _set_pty_size(self, cols: int, rows: int):
        """
        Apply terminal size to PTY using ioctl(TIOCSWINSZ).
        """

        if self.master_fd is None:
            return

        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

            if self.process and self.process.pid:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGWINCH)
                except Exception:
                    pass

            self._debug(f"PTY size set: {cols}x{rows}")

        except Exception as e:
            self._debug("failed to resize PTY:", e)