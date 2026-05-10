import os
import pty
import signal
import select
import struct
import fcntl
import termios
import subprocess
import threading
import time
from typing import Optional, List

from PySide6.QtCore import QObject, Signal, Slot
from file_text_extractor import extract_text_from_data_url
from hermes_locator import prepare_writable_hermes_home
from rag_context import RagContextManager


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
    rag_status_received = Signal(str)

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
        self.rag = RagContextManager()
        self.hidden_echo_lock = threading.Lock()
        self.hidden_echo_until = 0.0

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
            env["HERMES_HOME"] = prepare_writable_hermes_home()

            self._debug("environment PATH =", env.get("PATH", ""))
            self._debug("environment HERMES_HOME =", env.get("HERMES_HOME", ""))

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

                f"Cannot find command: {self.command}\n\n"

                "Hermes could not be located.\n"

                "If you installed Hermes, check its path with:\n"

                "    which hermes\n\n"

                "Then either add that path to the app search list or configure HERMES_COMMAND."

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
                text = self._filter_hidden_programmatic_echo(text)
                if not text:
                    continue

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

        self._write_to_pty(data, emit_user_input=True)

    def _write_to_pty(self, data: str, emit_user_input: bool = False):
        if not self.running or self.master_fd is None:
            self._debug("write ignored; backend not running")
            self.error_received.emit("Hermes is not running. Please restart Anna.")
            return

        if data is None:
            return

        if self.process and self.process.poll() is not None:
            self.running = False
            self._debug("write ignored; process already exited")
            self.error_received.emit("Hermes has exited. Please restart Anna.")
            return

        try:
            if emit_user_input:
                self.user_input_received.emit(data)

            encoded = data.encode("utf-8", errors="replace")
            os.write(self.master_fd, encoded)

            self._debug("write:", repr(data[:100]))

        except Exception as e:
            message = f"Failed to write to PTY:\n{e}"
            self._debug(message)
            self.error_received.emit(message)

    @Slot(str)
    def submit_chat_message(self, message: str):
        """
        Called by the separate chat composer.

        The complete message is available here, so RAG can retrieve file context
        before one augmented prompt is sent to Hermes.
        """

        message = (message or "").strip()
        if not message:
            return

        self.user_input_received.emit(message)
        worker = threading.Thread(
            target=self._submit_chat_message_worker,
            args=(message,),
            daemon=True,
        )
        worker.start()

    def _submit_chat_message_worker(self, message: str):
        try:
            self.rag_status_received.emit("Preparing message...")
            prompt = self.rag.build_cli_prompt(message)

            if prompt != message:
                self.rag_status_received.emit("RAG context added.")
                self._type_text_to_pty(prompt)
            else:
                self._type_text_to_pty(message)

        except Exception as e:
            self.rag_status_received.emit(f"RAG unavailable: {e}")
            self._type_text_to_pty(message)

    def _type_text_to_pty(self, text: str):
        """
        Feed text to Hermes like keyboard input.

        Hermes' prompt_toolkit TUI is much more stable when input arrives like
        normal typing instead of one large programmatic paste. The composer
        already displays the user's message, so hide the programmatic echo from
        xterm while the private RAG prompt is being injected.
        """

        text = sanitize_terminal_input(text)
        if not text:
            return

        for i in range(0, len(text), 16):
            self._hide_programmatic_echo_for(0.10)
            self._write_to_pty(text[i : i + 16])
            time.sleep(0.003)

        self._hide_programmatic_echo_for(0.10)
        self._write_to_pty("\r")

    def _hide_programmatic_echo_for(self, seconds: float):
        with self.hidden_echo_lock:
            self.hidden_echo_until = max(self.hidden_echo_until, time.monotonic() + seconds)

    def _filter_hidden_programmatic_echo(self, text: str) -> str:
        with self.hidden_echo_lock:
            if time.monotonic() < self.hidden_echo_until:
                return ""

        return text

    @Slot(str, str)
    def attach_file_text(self, name: str, text: str):
        """
        Receive a text file from the web composer and index it for file RAG.
        """

        name = (name or "uploaded-file").strip()
        text = text or ""

        worker = threading.Thread(
            target=self._attach_file_text_worker,
            args=(name, text),
            daemon=True,
        )
        worker.start()

    @Slot(str, str)
    def attach_file_data(self, name: str, data_url: str):
        """
        Receive a file as bytes from the web composer, extract readable text,
        then index it for file RAG.
        """

        name = (name or "uploaded-file").strip()
        data_url = data_url or ""

        worker = threading.Thread(
            target=self._attach_file_data_worker,
            args=(name, data_url),
            daemon=True,
        )
        worker.start()

    def _attach_file_data_worker(self, name: str, data_url: str):
        try:
            self.rag_status_received.emit(f"Reading {name}...")
            source, text = extract_text_from_data_url(name, data_url)
            status = self.rag.add_file_text(source, text)
            self.rag_status_received.emit(status)
        except Exception as e:
            self.rag_status_received.emit(f"Could not index {name}: {e}")

    def _attach_file_text_worker(self, name: str, text: str):
        try:
            self.rag_status_received.emit(f"Reading {name}...")
            status = self.rag.add_file_text(name, text)
            self.rag_status_received.emit(status)
        except Exception as e:
            self.rag_status_received.emit(f"Could not index {name}: {e}")

    @Slot()
    def reload_rag_config(self):
        self.rag.reload_config()
        if self.rag.client.is_configured():
            self.rag_status_received.emit("RAG settings saved. SiliconFlow is ready.")
        else:
            self.rag_status_received.emit("RAG settings saved. Add an API key to enable vector search.")

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


def sanitize_terminal_input(text: str) -> str:
    cleaned = []
    for ch in text or "":
        code = ord(ch)
        if ch in ["\r", "\n", "\t"]:
            cleaned.append(" ")
        elif code < 32 or code == 127:
            cleaned.append(" ")
        else:
            cleaned.append(ch)

    return " ".join("".join(cleaned).split())
