import sys
import os
import asyncio

# 🚀 THE PATH FIX: Tells Python to look in the parent folder (ml-service) for the 'app' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.qdrant_client import get_qdrant_client, init_qdrant_client
from app.core.config import get_settings, Domain # <--- IMPORT DOMAIN ENUM

async def wipe_database():
    print("Initializing Qdrant client...")
    
    # 🚀 THE STARTUP FIX: Manually wake up the database client
    init_result = init_qdrant_client()
    if asyncio.iscoroutine(init_result):
        await init_result

    client = get_qdrant_client()
    settings = get_settings()
    
    # 🚀 THE TYPE FIX: Pass the Domain enum objects instead of raw strings
    collections = [
        settings.qdrant_collection_name(Domain.GENERAL),
        settings.qdrant_collection_name(Domain.FINANCIAL),
        settings.qdrant_collection_name(Domain.MEDICAL)
    ]
    
    for collection in collections:
        try:
            # Check if it exists and delete it
            if await client.collection_exists(collection_name=collection):
                await client.delete_collection(collection_name=collection)
                print(f"✅ Deleted collection: {collection}")
            else:
                print(f"⏭️ Collection '{collection}' did not exist. Skipping.")
        except Exception as e:
            print(f"❌ Failed to delete {collection}: {e}")
            
    print("🎉 Database successfully wiped clean! You can now start fresh.")

if __name__ == "__main__":
    asyncio.run(wipe_database())