import os
import json
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# Use same default URI as mongo_tool.py if not present
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://stackoverflow:stackoverflow%40123@cluster0.3kqbc.mongodb.net/myDatabase?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = os.getenv("MONGO_PRACTICE_DB_NAME", "practice_db")

def migrate():
    print(f"Connecting to MongoDB at URI: {MONGO_URI}")
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    
    # Load file
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'practice_db.json')
    if not os.path.exists(db_path):
        print(f"Could not find existing practice_db.json at {db_path}")
        return
        
    print(f"Loading data from {db_path}")
    with open(db_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    problems = data.get("problems", {})
    metadata = {
        "_id": "global_metadata",
        "lastSyncDate": data.get("lastSyncDate"),
        "streak": data.get("streak", 0),
        "checkIns": data.get("checkIns", {}),
        "topics": data.get("topics", {}),
        "techniques": data.get("techniques", {})
    }
    
    # Insert/Update metadata
    print("Migrating global metadata...")
    db.metadata.replace_one({"_id": "global_metadata"}, metadata, upsert=True)
    
    # Insert problems
    print(f"Migrating {len(problems)} problems...")
    problem_docs = []
    
    for pid, pdata in problems.items():
        # Using string problem id as the Mongo _id mapping
        pdata["_id"] = str(pid)
        problem_docs.append(pdata)
        
    if problem_docs:
        # Clear existing problems prior to migration 
        db.problems.delete_many({})
        db.problems.insert_many(problem_docs)
        
    print(f"Successfully migrated {len(problem_docs)} problems and the global metadata to MongoDB database '{DB_NAME}'.")

if __name__ == "__main__":
    migrate()
