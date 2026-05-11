import logging
import math
import struct
import zlib

from fastapi.responses import StreamingResponse

from app.storage.db import DatabaseManager

logger = logging.getLogger(__name__)


async def _stream_visualizer_binary_impl(extension: str | None, db: DatabaseManager):
    """
    Implementation of the binary stream for the WebGPU visualizer.
    Packs file data into 32-byte chunks:
    - pos: float32x3 (12 bytes) @ 0
    - radius: float32 (4 bytes) @ 12
    - [padding]: 4 bytes @ 16
    - flags: uint32 (4 bytes) @ 20
    - type_hash: uint32 (4 bytes) @ 24
    - [padding/stride]: 4 bytes @ 28
    Total: 32 bytes
    """

    query = "SELECT id, path, type, size FROM files"
    params = []
    if extension:
        # Ensure extension starts with dot and is lowercase (already normalized in DB)
        clean_ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        query += " WHERE type = ?"
        params.append(clean_ext)

    try:
        rows = await db.execute_query(query, tuple(params))

        def generator():
            # Header or just raw stream? Frontend expects raw buffer
            # renderer.loadData(buffer) expects raw bytes

            for i, row in enumerate(rows):
                # Using dict-style access since db uses Row factory
                f_type = row["type"] or ".txt"
                size = row["size"] or 0

                # Deterministic spatial position (Spiral/Spherical)
                # P10-3: Use zlib.adler32 for stable, high-speed coordinate generation
                # (replaces MD5)
                # h = zlib.adler32(path.encode()) & 0xffffffff

                # Golden angle spiral on a sphere-ish volume
                phi = math.acos(1 - 2 * (i / max(1, len(rows))))
                theta = math.pi * (1 + 5**0.5) * i

                # Spread out based on count
                radius_spread = 200 + (len(rows) ** 0.5) * 5
                x = radius_spread * math.sin(phi) * math.cos(theta)
                y = radius_spread * math.sin(phi) * math.sin(theta)
                z = radius_spread * math.cos(phi)

                # Visual radius based on file size
                # Log scale so large files don't explode
                v_radius = 2.0 + math.log10(max(1, size)) * 1.5

                # Flags: 0=default, 1=highlighted, etc.
                flags = 0

                # Type Hash for coloring (High speed stable hash)
                type_hash = zlib.adler32(f_type.lower().encode()) & 0xFFFFFFFF

                # Pack: pos(fff) radius(f) parent_index(I=uint32) flags(I) type_hash(I) pad(I)
                # L-17: parent_index uses 0xFFFFFFFF (u32::MAX) as sentinel for "no parent / root
                # level", matching the Rust rust_core sentinel. Previously 0.0f was packed here
                # which cast to uint32=0, misattributing every file's LOD to the root node's radius
                # in the WebGPU culling shader.
                yield struct.pack(
                    "<ffff I I I I",
                    float(x),
                    float(y),
                    float(z),
                    float(v_radius),
                    0xFFFFFFFF,  # parent_index sentinel: root-level / no parent
                    int(flags),
                    int(type_hash),
                    0,  # padding to 32 bytes
                )

        return StreamingResponse(generator(), media_type="application/octet-stream")

    except Exception as e:
        logger.error(f"Error in visualizer binary stream: {e}")
        raise
