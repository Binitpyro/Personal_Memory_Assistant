import ctypes
import ctypes.wintypes as wintypes
import logging
import platform as _platform
import struct
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FSCTL_ENUM_USN_DATA = 0x000900B3
FILE_ATTRIBUTE_DIRECTORY = 0x00000010

NTFS_ROOT_REF = 5

_MFT_BUF_SIZE = 128 * 1024  # 128 KB
_ERROR_HANDLE_EOF = 38  # Win32 ERROR_HANDLE_EOF

if _platform.system() == "Windows":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
else:
    kernel32 = None  # type: ignore[assignment]


@dataclass
class MFTEntry:
    """A single Master File Table entry (file or directory)."""

    file_ref: int
    parent_ref: int
    name: str
    is_dir: bool


class NTFSScanner:
    """
    Reads the NTFS MFT to enumerate files without directory traversal.

    Usage::

        scanner = NTFSScanner()
        paths = scanner.scan_folder(Path("D:/Projects"), {".txt", ".md"})
        if paths is None:
    """

    def __init__(self) -> None:
        if kernel32 is None:
            raise RuntimeError("NTFSScanner requires Windows (kernel32 not available)")
        self._k32 = kernel32
        self.entries: dict[int, MFTEntry] = {}
        self.children_map: dict[int, list[MFTEntry]] = defaultdict(list)
        self.entry_count: int = 0

    def scan_folder(
        self,
        folder: Path,
        extensions: set[str],
    ) -> list[Path] | None:
        """
        Enumerate files under *folder* whose suffix is in *extensions*.

        Returns ``None`` when MFT access is unavailable (e.g. not admin,
        not NTFS).  The caller should fall back to ``os.scandir``.
        """
        volume = folder.drive  # e.g. "C:"
        if not volume:
            logger.warning("Cannot determine volume for path: %s", folder)
            return None

        handle = self._open_volume(volume)
        if handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()  # type: ignore[attr-defined]
            logger.info(
                "Cannot open volume %s (Win32 error %d). "
                "Run as Administrator for NTFS-accelerated scanning.",
                volume,
                err,
            )
            return None

        try:
            t0 = time.perf_counter()
            self._enumerate_mft(handle)
            elapsed = time.perf_counter() - t0
            self.entry_count = len(self.entries)
            logger.info(
                "MFT enumeration: %s entries in %.2fs",
                f"{self.entry_count:,}",
                elapsed,
            )
        finally:
            self._k32.CloseHandle(handle)

        self._build_children_map()

        target_ref = self._find_folder_ref(folder)
        if target_ref is None:
            logger.warning("Folder not found in MFT: %s", folder)
            return None

        file_paths = self._collect_files(target_ref, extensions, folder)
        logger.info(
            "MFT scan: %d matching files in %s",
            len(file_paths),
            folder,
        )
        return file_paths

    def _open_volume(self, volume: str) -> int:
        """Open a raw volume handle (requires admin)."""
        return int(self._k32.CreateFileW(
            f"\\\\.\\{volume}",
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        ))

    def _enumerate_mft(self, handle: int) -> None:
        """
        Read every MFT entry via ``FSCTL_ENUM_USN_DATA``.

        The input buffer is an ``MFT_ENUM_DATA_V0`` struct::

            StartFileReferenceNumber  DWORDLONG   (8 bytes)
            LowUsn                    LONGLONG    (8 bytes)
            HighUsn                   LONGLONG    (8 bytes)

        The output buffer starts with the *next* file-reference number
        (8 bytes), followed by a stream of ``USN_RECORD_V2`` entries.
        """
        med = struct.pack("<Qqq", 0, 0, 0x7FFFFFFFFFFFFFFF)

        buf_size = _MFT_BUF_SIZE
        buf = ctypes.create_string_buffer(buf_size)
        bytes_returned = wintypes.DWORD()

        self.entries.clear()

        while True:
            ok = self._k32.DeviceIoControl(
                handle,
                FSCTL_ENUM_USN_DATA,
                med,
                len(med),
                buf,
                buf_size,
                ctypes.byref(bytes_returned),
                None,
            )
            if not ok:
                err = ctypes.get_last_error()  # type: ignore[attr-defined]
                if err == _ERROR_HANDLE_EOF or err != 0:
                    logger.debug("DeviceIoControl ended (Win32 error %d)", err)
                break

            returned = bytes_returned.value
            if returned <= 8:
                break

            next_ref = struct.unpack_from("<Q", buf.raw, 0)[0]
            self._parse_usn_records(buf.raw, returned)
            med = struct.pack("<Qqq", next_ref, 0, 0x7FFFFFFFFFFFFFFF)

    def _parse_usn_records(self, raw: bytes, returned: int) -> None:
        """Parse USN_RECORD_V2 entries from a DeviceIoControl output buffer."""
        offset = 8
        while offset + 60 <= returned:
            rec_len = struct.unpack_from("<I", raw, offset)[0]
            if rec_len == 0 or offset + rec_len > returned:
                break

            entry = self._parse_single_record(raw, offset, returned)
            if entry is not None:
                self.entries[entry.file_ref] = entry

            offset += rec_len

    @staticmethod
    def _parse_single_record(raw: bytes, offset: int, returned: int) -> MFTEntry | None:
        """Decode a single USN_RECORD_V2 at *offset*. Returns None if unreadable."""
        file_ref = struct.unpack_from("<Q", raw, offset + 8)[0] & 0x0000FFFFFFFFFFFF
        parent_ref = struct.unpack_from("<Q", raw, offset + 16)[0] & 0x0000FFFFFFFFFFFF
        attrs = struct.unpack_from("<I", raw, offset + 52)[0]
        name_len = struct.unpack_from("<H", raw, offset + 56)[0]
        name_off = struct.unpack_from("<H", raw, offset + 58)[0]

        name_start = offset + name_off
        name_end = name_start + name_len
        if name_end > returned:
            return None

        name = raw[name_start:name_end].decode("utf-16-le", errors="replace")
        is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
        return MFTEntry(
            file_ref=file_ref,
            parent_ref=parent_ref,
            name=name,
            is_dir=is_dir,
        )

    def _build_children_map(self) -> None:
        """Index every entry by its parent reference."""
        self.children_map.clear()
        for entry in self.entries.values():
            self.children_map[entry.parent_ref].append(entry)

    def _find_folder_ref(self, folder: Path) -> int | None:
        """
        Walk from the NTFS root (ref 5) down the path components
        to find the target folder's MFT reference number.
        """
        parts = folder.parts[1:]
        current_ref = NTFS_ROOT_REF

        for part in parts:
            part_lower = part.lower()
            found = False
            for child in self.children_map.get(current_ref, []):
                if child.is_dir and child.name.lower() == part_lower:
                    current_ref = child.file_ref
                    found = True
                    break
            if not found:
                return None

        return current_ref

    def _collect_files(
        self,
        folder_ref: int,
        extensions: set[str],
        base_path: Path,
    ) -> list[Path]:
        """
        BFS from *folder_ref*, building full paths top-down.

        Only files whose extension is in *extensions* are returned.
        """
        results: list[Path] = []
        stack = [(folder_ref, base_path)]

        while stack:
            ref, current_path = stack.pop()
            for child in self.children_map.get(ref, []):
                child_path = current_path / child.name
                if child.is_dir:
                    stack.append((child.file_ref, child_path))
                else:
                    if not extensions or child_path.suffix.lower() in extensions:
                        results.append(child_path)

        return results
