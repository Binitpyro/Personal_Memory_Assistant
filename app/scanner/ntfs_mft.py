import ctypes
import ctypes.wintypes as wintypes
import logging
import platform as _platform
import sqlite3
import struct
import time
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


class NTFSScanner:
    """
    Reads the NTFS MFT to enumerate files without directory traversal.
    Uses a temporary SQLite database to avoid memory bloat.
    """

    def __init__(self) -> None:
        if kernel32 is None:
            raise RuntimeError("NTFSScanner requires Windows (kernel32 not available)")
        self._k32 = kernel32
        # Use a temporary on-disk database to stay within the 60MB RAM ceiling
        self.db = sqlite3.connect("")
        self.db.execute("PRAGMA synchronous = OFF")
        self.db.execute("PRAGMA journal_mode = MEMORY")
        self.db.execute(
            """
            CREATE TABLE mft (
                file_ref INTEGER PRIMARY KEY,
                parent_ref INTEGER,
                name TEXT,
                is_dir INTEGER
            )
            """
        )
        self.entry_count: int = 0

    def scan_folder(
        self,
        folder: Path,
        extensions: set[str],
    ) -> list[Path] | None:
        """
        Enumerate files under *folder* whose suffix is in *extensions*.
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

            # Create indices after bulk insert for faster querying
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_parent ON mft(parent_ref)")

            self.entry_count = self.db.execute("SELECT COUNT(*) FROM mft").fetchone()[0]
            logger.info(
                "MFT enumeration: %s entries in %.2fs",
                f"{self.entry_count:,}",
                elapsed,
            )
        finally:
            self._k32.CloseHandle(handle)

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
        return int(
            self._k32.CreateFileW(
                f"\\\\.\\{volume}",
                GENERIC_READ,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
        )

    def _enumerate_mft(self, handle: int) -> None:
        med = struct.pack("<Qqq", 0, 0, 0x7FFFFFFFFFFFFFFF)

        buf_size = _MFT_BUF_SIZE
        buf = ctypes.create_string_buffer(buf_size)
        bytes_returned = wintypes.DWORD()

        self.db.execute("DELETE FROM mft")

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
                if err == _ERROR_HANDLE_EOF:
                    logger.debug("DeviceIoControl ended normally (EOF)")
                elif err != 0:
                    logger.warning("DeviceIoControl ended with Win32 error %d", err)
                break

            returned = bytes_returned.value
            if returned <= 8:
                break

            next_ref = struct.unpack_from("<Q", buf.raw, 0)[0]
            self._parse_usn_records(buf.raw, returned)
            med = struct.pack("<Qqq", next_ref, 0, 0x7FFFFFFFFFFFFFFF)

    def _parse_usn_records(self, raw: bytes, returned: int) -> None:
        offset = 8
        records = []
        while offset + 60 <= returned:
            rec_len = struct.unpack_from("<I", raw, offset)[0]
            if rec_len == 0 or offset + rec_len > returned:
                break

            entry = self._parse_single_record(raw, offset, returned)
            if entry is not None:
                records.append(entry)

            offset += rec_len

        if records:
            self.db.executemany("INSERT OR IGNORE INTO mft VALUES (?, ?, ?, ?)", records)
            self.db.commit()

    @staticmethod
    def _parse_single_record(
        raw: bytes, offset: int, returned: int
    ) -> tuple[int, int, str, int] | None:
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
        is_dir = 1 if (attrs & FILE_ATTRIBUTE_DIRECTORY) else 0
        return (file_ref, parent_ref, name, is_dir)

    def _find_folder_ref(self, folder: Path) -> int | None:
        parts = folder.parts[1:]
        current_ref = NTFS_ROOT_REF

        for part in parts:
            part_lower = part.lower()
            row = self.db.execute(
                "SELECT file_ref FROM mft WHERE parent_ref = ? AND is_dir = 1 AND LOWER(name) = ?",
                (current_ref, part_lower),
            ).fetchone()
            if row:
                current_ref = row[0]
            else:
                return None

        return current_ref

    def _collect_files(
        self,
        folder_ref: int,
        extensions: set[str],
        base_path: Path,
    ) -> list[Path]:
        results: list[Path] = []
        stack = [(folder_ref, base_path)]

        while stack:
            ref, current_path = stack.pop()
            for child_ref, name, is_dir in self.db.execute(
                "SELECT file_ref, name, is_dir FROM mft WHERE parent_ref = ?", (ref,)
            ):
                child_path = current_path / name
                if is_dir:
                    stack.append((child_ref, child_path))
                else:
                    if not extensions or child_path.suffix.lower() in extensions:
                        results.append(child_path)

        return results
