import asyncio
import os

import numpy as np

from app.embeddings.service import EmbeddingService


async def main():
    embedder = EmbeddingService()

    # Batch 1: Long sequences
    long_batch = [
        "This is a very long sequence to force the batch to pad to near maximum length. " * 30
        for _ in range(128)
    ]
    # Make one of them slightly shorter just to mix it up
    long_batch[-1] = "Slightly shorter."

    # Batch 2: Short sequences
    short_batch = [f"Short sequence {i}" for i in range(128)]

    # We want to run them sequentially through the embedder
    print("Computing embeddings for long batch...")
    emb_long = await embedder.embed_texts(long_batch, batch_size=128)

    print("Computing embeddings for short batch...")
    emb_short = await embedder.embed_texts(short_batch, batch_size=128)

    # Concatenate results
    all_emb = np.array(emb_long + emb_short)

    if os.path.exists("embeddings_old.npy"):
        print("Found old embeddings. Comparing...")
        old_emb = np.load("embeddings_old.npy")

        diff = np.abs(all_emb - old_emb).max()
        print(f"Max absolute difference: {diff}")

        if np.allclose(all_emb, old_emb, atol=1e-5):
            print("SUCCESS: Embeddings match perfectly!")
        else:
            print("FAILURE: Embeddings DO NOT match!")
            # Print specifically if the short batch has issues
            short_diff = np.abs(np.array(emb_short) - old_emb[128:]).max()
            print(f"Short batch max diff: {short_diff}")

    else:
        print("Saving old embeddings for future comparison...")
        np.save("embeddings_old.npy", all_emb)
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
