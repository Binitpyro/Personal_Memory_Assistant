"""Quick test for clear_all fix."""
import asyncio
from app.storage.db import DatabaseManager

async def test():
    db = DatabaseManager(db_path="_test_clear.db")
    await db.init_db(schema_path="app/storage/schema.sql")
    # Insert dummy data
    conn = db._get_conn()
    await conn.execute(
        "INSERT INTO files (path, size, modified_at, type) VALUES ('x.txt', 1, '2025-01-01', '.txt')"
    )
    await conn.execute(
        "INSERT INTO chunks (file_id, start_offset, end_offset, text_preview) VALUES (1, 0, 10, 'hello world')"
    )
    await conn.commit()
    # Now clear
    result = await db.clear_all()
    print("SUCCESS:", result)
    await db.close()

asyncio.run(test())

import os
os.remove("_test_clear.db")
print("Test DB removed. All good!")
